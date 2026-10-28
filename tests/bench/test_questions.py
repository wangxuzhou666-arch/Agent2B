"""The question set: composition, determinism, and ground truth that agrees with the tools."""

import re
from collections import Counter

import pytest
from bench.questions import (
    CATEGORIES,
    MISSING_PO,
    MISSING_SKU,
    build_questions,
    example_ids,
    parse_locator,
    question_digest,
    question_numbers,
    routing_examples,
)
from bench.score import View, locate
from bench.truth import Truth
from bench.world import build_world, template_workflow_runner

from twotrack.core.routing.text import normalize


def run_expected(world, item):
    outcome = world.pipeline.run(world.ctx(), item.expected_tool, dict(item.expected_params),
                                 workflow_runner=template_workflow_runner)
    assert outcome.status.value == "ok", (item.id, outcome.problem)
    return outcome.result


def test_composition(items):
    assert 75 <= len(items) <= 90
    assert len({i.id for i in items}) == len(items)
    langs = Counter(i.lang for i in items)
    assert langs["zh"] == 16
    assert sum(1 for i in items if not i.answerable) == 10
    assert {i.category for i in items} == set(CATEGORIES)
    for item in items:
        assert item.answerable == (item.category != "unanswerable")
        if item.lang == "zh":
            assert re.search(r"[一-鿿]", item.text), item.id


def test_rebuilt_from_a_fresh_store_identically(items):
    other = build_world()
    try:
        again = build_questions(other)
    finally:
        other.close()
    assert [i.model_dump() for i in again] == [i.model_dump() for i in items]
    assert question_digest(again) == question_digest(items)


def test_every_located_truth_equals_the_tool_output(world, items):
    checked = 0
    for item in (i for i in items if i.answerable):
        result = run_expected(world, item)
        view = View(text="", aggregates=dict(result.aggregates), rows=result.rows)
        for expected in item.expected:
            locator = expected.check or expected.locator
            if locator is None:
                continue
            assert locate(locator, view) == pytest.approx(expected.value), (item.id, locator)
            checked += 1
        for text in item.expected_text:
            if text.locator:
                assert locate(text.locator, view) in text.alternatives, item.id
    beyond = sum(1 for i in items if "beyond_params" in i.tags)
    assert checked == sum(len(i.expected) for i in items) - beyond == 72


def test_category_items_are_answered_by_the_filtered_call(world, by_id):
    """The category items name the filtered call; its output equals the truth recomputed from
    the unfiltered call's rows plus the stored categories (two independent routes)."""
    truth = Truth(world.store, world.today)
    category = {sku: truth.category(sku) for sku in truth.positions}
    every_late = world.pipeline.run(world.ctx(), "late_shipments", {}).result.rows
    every_low = world.pipeline.run(world.ctx(), "low_stock", {}).result.rows
    for item_id, wanted in (("mc-05", "packaging"), ("mc-06", "spare_part")):
        assert by_id[item_id].expected_params == {"category": wanted}
        assert by_id[item_id].expected[0].value == sum(
            1 for r in every_late if category[r["sku_id"]] == wanted)
    assert by_id["mc-07"].expected[0].value == sum(
        1 for r in every_low if r["category"] == "packaging")
    assert by_id["mc-zh-01"].expected[0].value == by_id["mc-07"].expected[0].value
    assert by_id["mc-08"].expected[0].value == sum(
        1 for r in every_low if r["category"] == "spare_part" and r["criticality"] == "C")
    assert by_id["mc-09"].expected[0].value == sum(
        r["shortfall"] for r in every_low if r["category"] == "spare_part")
    assert not any("beyond_params" in by_id[i].tags
                   for i in ("mc-05", "mc-06", "mc-07", "mc-08", "mc-09", "mc-zh-01"))


def test_beyond_param_truths_match_the_closest_tool_rows_filtered_by_hand(world, by_id):
    """The four items no single call answers, recomputed from the closest call's rows plus the
    stored criticality."""
    beyond = sorted(i for i, item in by_id.items() if "beyond_params" in item.tags)
    assert beyond == ["mc-10", "mc-11", "mc-12", "mc-zh-03"]
    truth = Truth(world.store, world.today)
    crit = {sku: p.criticality for sku, p in truth.positions.items()}
    late_rows = run_expected(world, by_id["mc-10"]).rows
    level = re.search(r"class ([ABC])", by_id["mc-10"].text).group(1)
    assert by_id["mc-10"].expected[0].value == sum(
        1 for r in late_rows if crit[r["sku_id"]] == level)
    assert by_id["mc-zh-03"].expected[0].value == by_id["mc-10"].expected[0].value
    assert by_id["mc-11"].expected[0].value == sum(
        1 for r in run_expected(world, by_id["mc-11"]).rows if r["status"] == "shipped")
    stock_rows = run_expected(world, by_id["mc-12"]).rows
    assert by_id["mc-12"].expected[0].value == sum(
        r["on_hand"] for r in stock_rows if crit[r["sku_id"]] == "B")
    for item_id in beyond:  # the unfiltered figure is the stand-in to catch
        assert by_id[item_id].expected[0].distractors


def test_questions_never_reuse_routing_examples_or_their_ids(world, items):
    examples = routing_examples(world)
    reserved = example_ids(world)
    assert reserved  # the tools do name ids in their examples
    for item in items:
        assert normalize(item.text) not in examples, item.id
        for entity_id in reserved:
            assert entity_id not in item.text, (item.id, entity_id)


def test_expected_values_are_checkable_and_distractors_are_distinct(items):
    for item in items:
        echoes = question_numbers(item.text)
        for expected in item.expected:
            assert expected.value != 0, item.id
            if float(expected.value).is_integer() and not item.expected_text:
                assert expected.value >= 3, item.id
            for d in expected.distractors:
                assert d != 0 and d != pytest.approx(expected.value), item.id
                assert all(d != pytest.approx(e) for e in echoes), item.id
            if expected.locator and expected.locator.startswith("cell:"):
                _, _, _, key = parse_locator(expected.locator)
                assert key in item.text, item.id


def test_zh_variants_keep_the_english_truth(by_id):
    variants = [i for i in by_id.values() if i.variant_of]
    assert len(variants) == 16
    for item in variants:
        source = by_id[item.variant_of]
        assert source.lang == "en" and item.lang == "zh"
        assert [e.value for e in item.expected] == [e.value for e in source.expected]
        assert item.expected_tool == source.expected_tool
        assert item.expected_params == source.expected_params


def test_unanswerable_items_have_no_truth_and_missing_ids_really_are_missing(world, items):
    truth = Truth(world.store, world.today)
    assert not truth.orders_where(po=MISSING_PO) and MISSING_SKU not in truth.positions
    for item in (i for i in items if not i.answerable):
        assert not item.expected and not item.expected_text and item.expected_tool is None
        assert item.note
