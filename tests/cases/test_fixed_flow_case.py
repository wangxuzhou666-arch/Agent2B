"""The fixed-flow case (docs/EVIDENCE.md), through the HTTP API: no explorer takes part.

(i) ``REORDER_PLAN_CASE`` routes by rule to the ``reorder_plan`` workflow, which runs its steps
in order (three deterministic, then the narration), and every number in the answer is one the
code computed. (ii) ``CREATE_PO_CASE`` is a complete write: it goes straight to the one-time
approval, runs exactly once when approved, and the audit log keeps the whole chain.
"""

import pytest

from tests.cases.world import (
    CREATE_PO_CASE,
    REORDER_PLAN_CASE,
    explorer_steps,
    gate_targets,
    steps,
)
from twotrack.plugins.supply_chain import logic


def workflow_steps(body: dict) -> list[tuple[str, str]]:
    return [(s["facts"]["step"], s["facts"]["kind"]) for s in steps(body, "execute")
            if "workflow" in s["facts"]]


def test_a_reorder_plan_request_runs_the_workflow_by_rule_with_numbers_from_code(
        make_case_world):
    world = make_case_world(runtime="builtin")
    body = world.ask("olga", REORDER_PLAN_CASE)

    decision = body["decision"]
    assert (decision["kind"], decision["target"], decision["source"]) == (
        "workflow", "reorder_plan", "rule")
    assert explorer_steps(body) == []
    assert gate_targets(body) == [("reorder_plan", "ok")]
    # the sequential executor: three deterministic steps in order, then the narration
    assert workflow_steps(body) == [("find_low_stock", "deterministic"),
                                    ("compute_suggestions", "deterministic"),
                                    ("group_and_source", "deterministic"), ("narrate", "llm")]
    # the figures are the supply-chain logic's own, recomputed here from the store
    store = world.runtime.store
    low = logic.low_stock_positions(logic.stock_positions(store, logic.sku_filter(store, None)))
    quantities = [q for q in (logic.suggested_qty(p) for p in low) if q > 0]
    assert body["aggregates"]["sku_count"] == len(quantities) == len(body["rows"]) > 0
    assert body["aggregates"]["total_suggested_qty"] == sum(quantities)
    assert body["narrative_stripped"] == 0 and str(sum(quantities)) in body["answer"]
    (entry,) = world.audit()
    assert (entry["target"], entry["status"], entry["route_source"]) == (
        "reorder_plan", "ok", "rule")


def test_a_complete_create_po_request_waits_for_approval_and_runs_exactly_once(
        make_case_world):
    world = make_case_world(runtime="builtin")
    before = world.purchase_orders()
    body = world.ask("olga", CREATE_PO_CASE)

    decision = body["decision"]
    assert (decision["kind"], decision["target"], decision["source"]) == (
        "tool", "create_po", "rule")
    assert decision["params"] == {"sku_id": "SKU-10042", "qty": 200}
    assert explorer_steps(body) == []
    assert gate_targets(body) == [("confirmation", "blocked")]
    grant_id = body["approval"]["grant_id"]
    assert world.purchase_orders() == before  # nothing ran yet

    done = world.approve(grant_id)
    assert done.status_code == 200 and done.json()["status"] == "ok"
    (new,) = [o for o in world.purchase_orders() if o not in before]
    assert (new["sku_id"], new["qty"], new["status"]) == ("SKU-10042", 200, "draft")
    again = world.approve(grant_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"
    assert len(world.purchase_orders()) == len(before) + 1
    trail = world.records(grant_id)
    assert [(e["status"], e["problem"]) for e in trail] == [
        ("needs_confirmation", None), ("started", None), ("ok", None), ("blocked", "grant_used")]
    assert (trail[0]["route_source"], trail[0]["user_id"]) == ("rule", "olga")


@pytest.mark.pi
def test_neither_fixed_flow_starts_a_worker_even_with_pi_as_the_explorer(pi_harness,
                                                                         make_case_world):
    world = make_case_world(harness=pi_harness)
    for query in (REORDER_PLAN_CASE, CREATE_PO_CASE):
        assert explorer_steps(world.ask("olga", query)) == []
    assert world.launcher.procs == [] and world.lines == []
    assert world.provider.payloads == []  # no agent step was asked of the model
