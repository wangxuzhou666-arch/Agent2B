"""The scope guard: a narrower question never gets a broader number.

Routing fills a filter the question names, asks when the routed target cannot apply it, and lets
an acknowledged unfiltered run through once, saying so in the answer's provenance.
"""

import dataclasses
from pathlib import Path

import pytest
from world import OPERATOR, build_world

from twotrack.core import (
    PLUGIN_PATHS_ENV,
    SCOPE_ACK_PARAM,
    RouteKind,
    RouteSource,
    TraceStatus,
    load_registry,
)
from twotrack.core.graph import Engine, run_query
from twotrack.core.routing import decide_route
from twotrack.providers.base import LLMRoute
from twotrack.providers.mock import MockProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"
CRITICALITY_A = "field:sku.criticality=A"


class RoutesTo(MockProvider):
    """The zero-key provider, except that its routing opinion is scripted."""

    def __init__(self, target: str, confidence: float = 0.95, **params) -> None:
        super().__init__()
        self.reply = LLMRoute(target=target, confidence=confidence, params=params)

    def route(self, query, tool_cards):
        return self.reply


def route(world, query, provider=None, language=None, **kw):
    steps = []
    facts = {"language": language} if language else {}
    ctx = dataclasses.replace(world.ctx(OPERATOR, **facts), trace=steps.append)
    decision = decide_route(query, ctx, world.registry, provider or MockProvider(), **kw)
    return decision, steps


def scope_step(steps):
    return next(s for s in steps if s.step == "scope")


@pytest.fixture(scope="module")
def drop_in():
    import os

    previous = os.environ.get(PLUGIN_PATHS_ENV)
    os.environ[PLUGIN_PATHS_ENV] = str(EXAMPLES)
    try:
        registry = load_registry()
    finally:
        if previous is None:
            os.environ.pop(PLUGIN_PATHS_ENV, None)
        else:
            os.environ[PLUGIN_PATHS_ENV] = previous
    return build_world(registry, seed=True)


# ---------------------------------------------------------------- binding


@pytest.mark.parametrize("query", ["delayed deliveries of spare parts", "备件的延迟订单"])
def test_a_named_category_fills_the_filter(seeded, query):
    decision, steps = route(seeded, query)
    assert (decision.kind, decision.target, decision.source) == (
        RouteKind.TOOL, "late_shipments", RouteSource.RULE)
    assert decision.params == {"category": "spare_part"}
    assert decision.scope.filled == {"category": "spare_part"}
    step = scope_step(steps)
    assert step.status is TraceStatus.OK and step.facts["verdict"] == "pass"
    assert step.facts["bound"] == [{"key": "category:sku", "value": "spare_part",
                                    "param": "category", "implied": False}]
    assert step.facts["unbound"] == []
    assert [s.step for s in steps] == ["candidates", "understand", "scope"]


def test_a_drop_in_category_is_bound_from_its_declarations_alone(drop_in):
    decision, _ = route(drop_in, "delayed deliveries of refrigerated items")
    assert (decision.target, decision.params) == ("late_shipments",
                                                  {"category": "cold_chain_item"})


def test_given_params_win_over_the_question(seeded):
    decision, steps = route(seeded, "delayed deliveries of spare parts",
                            explicit_target="late_shipments",
                            explicit_params={"category": "packaging"})
    assert decision.kind is RouteKind.TOOL and decision.params == {"category": "packaging"}
    assert scope_step(steps).facts["bound"][0]["value"] == "spare_part"  # named, not filled
    model = RoutesTo("late_shipments", category="packaging")
    decision, _ = route(seeded, "delayed deliveries of spare parts", provider=model)
    assert decision.params["category"] == "packaging"


def test_a_model_param_given_as_a_label_becomes_the_category_id(seeded):
    model = RoutesTo("late_shipments", category="Packing Materials")
    decision, steps = route(seeded, "anything delayed right now", provider=model)
    assert decision.params["category"] == "packaging"
    assert not any(s.step == "scope" for s in steps)  # the question names no constraint


def test_two_values_for_one_filter_ask_which(seeded):
    decision, steps = route(seeded, "delayed deliveries of packaging and spare parts")
    assert decision.kind is RouteKind.CLARIFY and decision.clarify_options == ["late_shipments"]
    choices = decision.scope.choices
    assert [(c.target, c.label, c.params) for c in choices] == [
        ("late_shipments", "Packaging", {"category": "packaging"}),
        ("late_shipments", "Spare Part", {"category": "spare_part"})]
    assert decision.reason == ("The question names more than one category (Packaging, Spare "
                               "Part), but “Late shipments” filters by one at a time. Pick one.")
    assert scope_step(steps).facts["verdict"] == "conflict"


# ---------------------------------------------------------------- the guard


def test_a_constraint_the_target_cannot_apply_asks_instead_of_answering(seeded):
    decision, steps = route(seeded, "which class A items have delayed deliveries")
    assert decision.kind is RouteKind.CLARIFY and decision.target == "late_shipments"
    assert decision.reason == (
        "“Late shipments” can't be limited to criticality A. “Show all criticalities” shows its "
        "result for all criticalities instead; the other choices can apply the filter.")
    unfiltered, *others = decision.scope.choices
    assert unfiltered.target == "late_shipments" and unfiltered.unfiltered
    assert unfiltered.label == "Show all criticalities"
    assert unfiltered.params == {SCOPE_ACK_PARAM: [CRITICALITY_A]}
    # the other choices can apply the constraint
    assert others and {c.target for c in others} <= {"low_stock", "reorder_plan"}
    assert all(not c.unfiltered and c.params is None for c in others)
    step = scope_step(steps)
    assert step.status is TraceStatus.BLOCKED and step.facts["verdict"] == "gap"
    assert step.facts["mentioned"] == [{"key": "field:sku.criticality", "value": "A",
                                        "text": "class A"}]
    assert step.facts["unbound"] == [{"key": "field:sku.criticality", "value": "A"}]


def test_the_gap_is_explained_in_the_users_language(seeded):
    decision, _ = route(seeded, "A类物料的延迟订单", language="zh")
    assert decision.kind is RouteKind.CLARIFY
    assert decision.reason == ("“逾期采购单”无法按重要等级“A类”筛选。选择“显示全部重要等级”会显示"
                               "全部重要等级的结果；其他选项可以按此筛选。")
    assert decision.scope.choices[0].label == "显示全部重要等级"


@pytest.mark.parametrize(("query", "language", "reason"), [
    ("low stock items except packaging", None,
     "“Low stock” can't leave out category Packaging. “Show all categories” shows its result "
     "for all categories instead."),
    ("除了包装材料以外的低库存物料", "zh",
     "“低库存”无法排除类别“包装材料”。选择“显示全部类别”会显示全部类别的结果。"),
    ("class A items in the west warehouse running low except packaging", None,
     "“Low stock” can't be limited to warehouse WH-WEST, and can't leave out category "
     "Packaging. “Show all warehouses and categories” shows its result for all warehouses and "
     "categories instead."),
])
def test_a_gap_reads_as_plain_text_naming_the_report(seeded, query, language, reason):
    decision, _ = route(seeded, query, language=language)
    assert decision.kind is RouteKind.CLARIFY and decision.reason == reason
    assert not any(mark in decision.reason for mark in ("`", "*", "low_stock", "_"))


def test_a_warehouse_on_a_report_summed_across_warehouses_asks(seeded):
    decision, _ = route(seeded, "items running low in the west warehouse")
    assert decision.kind is RouteKind.CLARIFY and decision.target == "low_stock"
    assert [c.target for c in decision.scope.choices] == ["low_stock", "inventory_lookup"]
    assert decision.scope.choices[0].label == "Show all warehouses"


def test_an_excluded_constraint_is_never_applied_as_a_filter(seeded):
    decision, _ = route(seeded, "overdue orders except packaging")
    assert decision.kind is RouteKind.CLARIFY
    assert decision.reason == ("“Late shipments” can't leave out category Packaging. “Show all "
                               "categories” shows its result for all categories instead.")
    (only,) = decision.scope.choices  # no report can exclude a category
    assert only.unfiltered and decision.scope.unbound[0].negated


def test_a_constraint_the_target_always_applies_needs_no_filter(seeded, drop_in):
    decision, steps = route(seeded, "raise a draft PO for SKU-10001, 40 units",
                            explicit_target="create_po")
    assert decision.kind is RouteKind.TOOL
    assert scope_step(steps).facts["bound"][0]["implied"] is True
    decision, _ = route(drop_in, "cold chain stock expiring this month")
    assert (decision.kind, decision.target) == (RouteKind.TOOL, "expiring_soon")
    decision, _ = route(drop_in, "packaging expiring this month")
    assert decision.kind is RouteKind.CLARIFY  # it only covers cold-chain items


def test_questions_without_constraints_skip_the_check(seeded):
    decision, steps = route(seeded, "what is overdue right now")
    assert decision.kind is RouteKind.TOOL and decision.scope is None
    assert [s.step for s in steps] == ["candidates", "understand"]


def test_the_chat_fallback_is_not_a_semantic_decision(seeded):
    decision, _ = route(seeded, "compose a limerick about tides")
    assert (decision.kind, decision.source) == (RouteKind.CHAT, RouteSource.FALLBACK)


# ---------------------------------------------------------------- acknowledgement and audit


def _engine(world, provider=None):
    return Engine(registry=world.registry, provider=provider or MockProvider(),
                  pipeline=world.pipeline, audit=world.audit)


def test_an_acknowledged_run_goes_through_once_and_says_it_is_unfiltered():
    world = build_world(seed=True)
    engine = _engine(world)
    query = "which class A items have delayed deliveries"
    asked = run_query(engine, world.ctx(OPERATOR), query)
    option = asked.clarify_options[0]
    assert option.unfiltered and option.label == "Show all criticalities"
    assert asked.rows == [] and asked.aggregates == {}  # nothing ran, no figure shown

    ran = run_query(engine, world.ctx(OPERATOR), query, explicit_target=option.name,
                    explicit_params=option.explicit_params)
    assert ran.decision.kind is RouteKind.TOOL and ran.decision.scope.acknowledged
    assert ran.aggregates["late_count"] > 0
    assert ran.provenance.scope.endswith("not filtered by criticality A; all criticalities shown")
    assert "not filtered by criticality A" in ran.answer  # the template narration cites it

    again = run_query(engine, world.ctx(OPERATOR), query, explicit_target="late_shipments")
    assert again.decision.kind is RouteKind.CLARIFY  # the acknowledgement was for one run


def test_only_the_users_explicit_pick_can_acknowledge(seeded):
    sneaky = RoutesTo("late_shipments", **{SCOPE_ACK_PARAM: [CRITICALITY_A]})
    decision, _ = route(seeded, "which class A items have delayed deliveries", provider=sneaky)
    assert decision.kind is RouteKind.CLARIFY
    assert SCOPE_ACK_PARAM not in decision.params


def test_an_acknowledgement_covers_only_the_constraints_it_names(seeded):
    decision, _ = route(seeded, "class A items in the west warehouse running low",
                        explicit_target="low_stock",
                        explicit_params={SCOPE_ACK_PARAM: ["category:sku=packaging"]})
    assert decision.kind is RouteKind.CLARIFY  # the warehouse was never acknowledged
    assert decision.params == {"criticality": "A"}


def test_the_scope_step_is_in_the_answer_and_the_audit_replay():
    world = build_world(seed=True)
    engine = _engine(world)
    ran = run_query(engine, world.ctx(OPERATOR), "delayed deliveries of spare parts")
    assert [s.step for s in ran.trace][:3] == ["candidates", "understand", "scope"]
    record = world.audit.get(ran.audit_ids[-1])
    assert record.status.value == "ok" and record.params["category"] == "spare_part"
    replay = [s for s in record.trace if s.step == "scope"]
    assert replay and replay[0].facts["bound"][0]["param"] == "category"

    asked = run_query(engine, world.ctx(OPERATOR), "which class A items have delayed deliveries")
    record = world.audit.get(asked.audit_ids[-1])
    assert (record.status.value, record.target) == ("clarify", "late_shipments")
    (step,) = [s for s in record.trace if s.step == "scope"]
    assert step.status is TraceStatus.BLOCKED and step.facts["unbound"]
    assert [s.step for s in record.trace] == [s.step for s in asked.trace]
