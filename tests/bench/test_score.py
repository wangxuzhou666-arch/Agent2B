"""The scorer: number reading agrees with the numeric lock; each rule on small cases."""

import random

import pytest
from bench.questions import Expected, ExpectedText, Item
from bench.score import (
    ABSTAIN_RE,
    Evidence,
    Pool,
    View,
    matches,
    numbers_in,
    result_numbers,
    score,
    wilson,
    written,
)

from twotrack.core.guards.numeric_lock import WrittenNumber, is_backed, lock_narrative


def item(*expected: Expected, text: str = "How many units of SKU-10077 are available?",
         answerable: bool = True, tool: str | None = "inventory_lookup",
         also: tuple[str, ...] = (), expected_text: tuple[ExpectedText, ...] = ()) -> Item:
    return Item(id="t-01", text=text, lang="en",
                category="lookup" if answerable else "unanswerable", answerable=answerable,
                expected=expected, expected_text=expected_text,
                expected_tool=tool if answerable else None, also_accepted=also)


def evidence(tool: str = "inventory_lookup", rows=(), aggregates=None) -> Evidence:
    return Evidence([(tool, Pool(result_numbers(list(rows), aggregates or {})))])


AVAILABLE = Expected(name="available units", value=376, locator="agg:total_available",
                     distractors=(292673.0,))
INVENTORY = evidence(rows=[{"warehouse": "WH-EAST", "on_hand": 300, "reserved": 20},
                           {"warehouse": "WH-WEST", "on_hand": 100, "reserved": 4}],
                     aggregates={"total_on_hand": 400, "total_reserved": 24,
                                 "total_available": 376, "sku_count": 1})


# ---------------------------------------------------------------- agreement with the lock


@pytest.mark.parametrize("text", [
    "Available: 4,321 units. Reserved 12.5 percent; 1.2k on order.",
    "库存为 1.2万 件，另有四十二件在途。",
    "1. First line has 7\n2) second has 8.25%",
    "In Q3 SKU-10042 had #2 rank; on 2026-10-01 at 09:30 it was 2025 vintage.",
])
def test_numbers_are_read_exactly_as_the_lock_reads_them(text):
    found = numbers_in(text)
    allowed = [n.value for n in found] + [n.value / 100 for n in found if n.percent]
    assert lock_narrative(text, allowed).stripped_count == 0
    for number in found:
        others = [n.value for n in found if n is not number]
        locked = lock_narrative(text, [v for v in others if v != number.value])
        assert locked.stripped_count >= 1, number.text


def test_exempt_tokens_are_not_numbers_to_either():
    assert numbers_in("Q3 SKU-10042 #2 2026-10-01 09:30 in 2025") == []


def test_pool_backs_exactly_like_is_backed():
    rng = random.Random(3)
    values = [rng.choice([rng.randint(0, 5000), round(rng.uniform(0, 500), rng.randint(0, 3))])
              for _ in range(400)]
    pool = Pool(values)
    for _ in range(3000):
        step = rng.choice([1.0, 0.1, 0.01, 100.0, 1000.0])
        value = round(rng.uniform(0, 6000), 2)
        number = WrittenNumber(value, step, rng.random() < 0.2, str(value))
        assert pool.backs(number) == is_backed(number, [abs(v) for v in values])


# ---------------------------------------------------------------- matching


@pytest.mark.parametrize(("written_text", "value", "tol", "expected"), [
    ("4,321", 4321, 0.0, True),
    ("12.3", 12.34, 0.0, True),
    ("12.3", 12.4, 0.0, False),
    ("1.2k", 1234, 0.0, True),
    ("1.2 million", 1_234_567, 0.0, True),
    ("50", 48, 0.0, False),
    ("50", 48, 2.0, True),
    ("$9,796.16", 9796.16, 0.0, True),
    ("四十二", 42, 0.0, True),
    ("1.2万", 12000, 0.0, True),
])
def test_matching_uses_written_precision_and_tolerance(written_text, value, tol, expected):
    number = numbers_in(written_text)[0]
    assert matches(number, Expected(name="x", value=value, tol=tol)) is expected


def test_percent_matches_a_ratio():
    assert matches(numbers_in("93.5%")[0], Expected(name="share", value=0.935))


# ---------------------------------------------------------------- roles and outcomes


def test_correct_answer_with_context_numbers():
    result = score(item(AVAILABLE), View(
        text="SKU-10077 has 376 units available: 400 on hand minus 24 reserved."), INVENTORY)
    assert result.outcome == "correct" and not result.wrong_number
    assert {n.role for n in result.numbers} == {"expected", "supported"}


def test_invented_number_is_unsupported_and_wrong():
    result = score(item(AVAILABLE), View(
        text="376 units are available, about 58 more than last week."), INVENTORY)
    assert result.unsupported == 1 and result.wrong_number and result.outcome == "wrong_number"


def test_derived_numbers_are_supported():
    # a column mean and a group's share are derivable; 296 (= 300 - 4) is not: no 4 here
    rows = [{"warehouse": "WH-EAST", "on_hand": 300}, {"warehouse": "WH-WEST", "on_hand": 100},
            {"warehouse": "WH-WEST", "on_hand": 0}]
    ev = evidence(rows=rows, aggregates={"total_on_hand": 400, "sku_count": 1})
    target = item(Expected(name="on hand", value=400))
    assert score(target, View(text="400 on hand, about 133.3 per row."), ev).correct
    assert score(target, View(text="400 on hand; 75% of it in WH-EAST."), ev).correct
    assert score(target, View(text="400 on hand, 50 per WH-WEST row."), ev).correct
    assert score(target, View(text="400 on hand; 296 usable."), ev).wrong_number


def test_question_numbers_are_echoes_not_claims():
    target = item(Expected(name="qty", value=376), text="Can we ship 250 units of SKU-10077?")
    result = score(target, View(text="Yes: 376 are available, more than the 250 asked."),
                   INVENTORY)
    assert result.correct
    assert [n.role for n in result.numbers] == ["expected", "echo"]


def test_wrong_scope_stand_in_is_a_contradiction():
    ev = evidence(aggregates={"total_available": 292673, "sku_count": 120})
    result = score(item(AVAILABLE), View(text="Total available: 292,673 units."), ev)
    assert result.contradiction and result.wrong_number and result.unsupported == 0


def test_distractor_next_to_the_right_answer_is_context():
    ev = evidence(aggregates={"total_available": 292673, "a": 376})
    result = score(item(AVAILABLE), View(
        text="SKU-10077 has 376 available out of 292,673 across all SKUs."), ev)
    assert result.correct and not result.contradiction


def test_caveated_stand_in_is_not_a_wrong_number():
    ev = evidence(aggregates={"total_available": 292673})
    result = score(item(AVAILABLE), View(
        text="I can't filter by that SKU; across all SKUs 292,673 units are available."), ev)
    assert result.caveated and not result.wrong_number and result.outcome == "abstained"


def test_coincidental_match_from_an_unrelated_tool_is_not_credited():
    target = item(Expected(name="late orders", value=3), tool="late_shipments",
                  text="How many late purchase orders does SUP-006 have?")
    ev = evidence(tool="po_status", aggregates={"po_count": 19, "shipped_count": 3})
    result = score(target, View(text="Po count: 19. Shipped count: 3."), ev)
    assert not result.correct and result.outcome == "incomplete"
    ok = evidence(tool="late_shipments", aggregates={"late_count": 3})
    assert score(target, View(text="SUP-006 has 3 late orders."), ok).correct


def test_derived_answer_outside_every_pool_is_still_credited():
    target = item(Expected(name="late packaging", value=5), tool="late_shipments")
    ev = evidence(tool="late_shipments", rows=[{"po_id": f"PO-{2000 + i}", "qty": 10 * i}
                                               for i in range(1, 16)],
                  aggregates={"late_count": 15})
    assert not ev.backs(numbers_in("5")[0])  # nothing in the output says 5
    assert score(target, View(text="5 of the 15 late orders are for packaging."), ev).correct


def test_expected_text_is_required_for_rankings():
    target = item(Expected(name="rating", value=4.7), tool="supplier_compare",
                  expected_text=(ExpectedText(name="supplier",
                                              alternatives=("SUP-012", "Supplier Omicron Co"),
                                              locator="top:supplier_id"),))
    ev = evidence(tool="supplier_compare", aggregates={"top_rating": 4.7})
    assert score(target, View(text="Supplier Omicron Co is best at 4.7."), ev).correct
    assert score(target, View(text="The best rating is 4.7."), ev).outcome == "incomplete"
    seen = View(text="Top rating: 4.7.", aggregates={"top_rating": 4.7},
                rows=[{"rank": 1, "supplier_id": "SUP-012"}], use_locators=True,
                tool="supplier_compare")
    assert score(target, seen, ev).correct
    elsewhere = seen.model_copy(update={"tool": "po_status"})
    assert not score(target, elsewhere, ev).correct


def test_cell_locator_credits_the_named_row_only_for_an_accepted_tool():
    target = item(Expected(name="days late", value=40, locator="cell:days_late@po_id=PO-20150"),
                  tool="late_shipments", text="How many days late is PO-20150?")
    rows = [{"po_id": "PO-20098", "days_late": 66}, {"po_id": "PO-20150", "days_late": 40}]
    ev = evidence(tool="late_shipments", rows=rows, aggregates={"late_count": 2})
    seen = View(text="2 rows matched.", rows=rows, use_locators=True, tool="late_shipments")
    assert score(target, seen, ev).correct
    assert not score(target, seen.model_copy(update={"use_locators": False}), ev).correct


@pytest.mark.parametrize(("text", "outcome"), [
    ("I can't forecast demand with the available data.", "correct"),
    ("No language model is configured, so I can only run the reports.", "correct"),
    ("Several reports could answer this. Which one did you mean?", "correct"),
    ("现有数据无法预测下个季度的需求。", "correct"),
    ("Current stock is 376 units.", "answered"),
    ("Next quarter demand will be about 1,180 units.", "wrong_number"),
])
def test_unanswerable_outcomes(text, outcome):
    target = item(text="What will demand for SKU-10077 be next quarter?", answerable=False)
    assert score(target, View(text=text), INVENTORY).outcome == outcome


def test_abstaining_on_an_answerable_question_is_its_own_outcome():
    result = score(item(AVAILABLE), View(text="I don't have access to that data."), INVENTORY)
    assert result.outcome == "abstained" and result.abstained and not result.wrong_number


def test_aggregates_shown_beside_the_text_are_scored_too():
    seen = View(text="Here is the stock.", aggregates={"total_available": 292673.0},
                use_locators=True, tool="inventory_lookup")
    ev = evidence(aggregates={"total_available": 292673})
    assert score(item(AVAILABLE), seen, ev).contradiction


def test_abstention_lexicon_is_narrow():
    assert ABSTAIN_RE.search("The figures do not break down by category.")
    assert not ABSTAIN_RE.search("SKU-10077 has 376 units available in WH-EAST.")
    assert not ABSTAIN_RE.search("3 rows matched (sku_id=SKU-10077).")


def test_written_value_for_a_year_like_number_is_still_a_number():
    assert written(2026).value == 2026 and written(195570.68).step == pytest.approx(0.01)


def test_wilson_interval():
    low, high = wilson(0, 10)
    assert low == 0 and high == pytest.approx(0.2775, abs=1e-3)
    low, high = wilson(40, 80)
    assert low == pytest.approx(0.393, abs=1e-3) and high == pytest.approx(0.607, abs=1e-3)
