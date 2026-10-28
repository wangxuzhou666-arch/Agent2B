"""The exploration case (docs/EVIDENCE.md), through the HTTP API, on the mock tier.

The request is ``EXPLORATION_CASE``: no fixed flow covers it, so the router hands it over. Pi
runs it; the mock's script supplies the plan. The real Pi worker talks to the repository's mock
model, which follows its fixed script (``providers.mock_script``): three reads through the
gate, then a ``create_po`` proposal that stops the run at a one-time approval. The same case as
a viewer shows the write is never offered and every attempt is refused on record; the built-in
loop runs the same case without Node. All of it is the mock tier: a scripted mock plans, not a
model.
"""

import json
from pathlib import Path

import pytest

from tests.cases.world import (
    CASE_READS,
    CASE_SKU,
    CASE_SUPPLIER,
    NARRATOR_KEYS,
    build_case_world,
    end_of_run,
    fence_key,
    gate_targets,
    ran_after_gate,
    start_of_run,
    steps,
)
from twotrack.core.explore.pi import EXPLORER_SYSTEM
from twotrack.core.i18n import say
from twotrack.providers.mock_script import EXPLORATION_CASE

PROPOSAL = {"sku_id": CASE_SKU, "qty": 74, "supplier_id": "SUP-007"}
REPO = Path(__file__).resolve().parents[2]
# where a reader is told which request to ask; each must carry the scripted sentence
CASE_DOCUMENTS = ("README.md", "README.zh-CN.md", "docs/EVIDENCE.md",
                  "web/e2e/explore/exploration.spec.ts")


def unwrapped(text: str) -> str:
    """``text`` with every hard line wrap (and a blockquote's ``>``) read as one space."""
    return " ".join(line.strip().lstrip(">").strip() for line in text.splitlines())


@pytest.fixture(scope="module")
def operator_run(pi_harness, tmp_path_factory):
    """One run of the case as the operator, shared by the read-only checks below."""
    world = build_case_world(tmp_path_factory.mktemp("case") / "data", harness=pi_harness)
    orders = len(world.purchase_orders())
    body = world.ask("olga")
    yield world, body, orders
    world.runtime.close()


# ---------------------------------------------------------------- the operator, in Pi


@pytest.mark.pi
def test_no_fixed_flow_covers_the_request_so_the_router_hands_it_over(operator_run):
    _, body, _ = operator_run
    assert body["decision"]["kind"] == "open_task" and body["decision"]["source"] == "agent"
    (handover,) = steps(body, "handover")
    facts = handover["facts"]
    assert (facts["reason"], facts["to"]) == ("intent_not_covered", "open_task")
    evidence = facts["evidence"]
    assert evidence["rule"] is None and evidence["llm"] is None and evidence["arms_silent"]
    assert evidence["semantic"]["below"] is True


@pytest.mark.pi
def test_pi_starts_with_the_exposed_tools_the_example_plugin_included(operator_run):
    world, body, _ = operator_run
    facts = start_of_run(body)["facts"]
    assert (facts["loop"], facts["runtime"]) == ("start", "pi")
    exposed = facts["exposed"]
    assert {"supplier_scorecard", "create_po", *CASE_READS} <= set(exposed)
    assert exposed == sorted(exposed)
    registry = world.runtime.registry
    assert all(registry.target(name).explorable is True for name in exposed)
    # the worker was declared exactly those tools, with Pi's own system prompt
    (start,) = world.sent("host", "start")
    assert [t["name"] for t in start["tools"]] == exposed
    assert start["system"] == EXPLORER_SYSTEM and start["task"] == EXPLORATION_CASE


@pytest.mark.pi
def test_every_call_passes_the_gate_and_the_write_stops_there(operator_run):
    world, body, _ = operator_run
    assert gate_targets(body) == [(name, "ok") for name in CASE_READS] + [
        ("confirmation", "blocked")]
    (confirmation,) = [s["facts"] for s in steps(body, "gate", "blocked")]
    assert confirmation["stage"] == "confirmation"
    # each read ran (one execute step each), in the order the script asked for them, and the
    # gate step that passed it comes directly before the step that ran it
    assert ran_after_gate(body) == CASE_READS
    turns = [s["facts"]["requested"] for s in steps(body, "execute")
             if s["facts"].get("loop") == "turn"]
    assert turns == [["late_shipments"], ["reorder_suggestion", "supplier_scorecard"],
                     ["create_po"]]
    # what the worker forwarded: ids from the request, the proposal from the fenced results
    assert [(m["name"], m["args"]) for m in world.sent("worker", "tool_call")] == [
        ("late_shipments", {"supplier_id": CASE_SUPPLIER, "sku_id": CASE_SKU}),
        ("reorder_suggestion", {"sku_id": CASE_SKU}),
        ("supplier_scorecard", {}),
        ("create_po", PROPOSAL)]


@pytest.mark.pi
def test_the_run_pauses_with_needs_confirmation_and_nothing_is_written(operator_run):
    world, body, orders = operator_run
    step = end_of_run(body)
    assert step["status"] == "blocked"
    facts = step["facts"]
    assert (facts["runtime"], facts["model"], facts["stop_reason"]) == (
        "pi", "mock", "needs_confirmation")
    assert facts["calls"] == [{"tool": n, "outcome": "ok"} for n in CASE_READS] + [
        {"tool": "create_po", "outcome": "needs_confirmation"}]
    approval = body["approval"]
    assert approval["label"] == "Create purchase order"
    assert "SKU-10053" in approval["summary"] and "SUP-007" in approval["summary"]
    assert body["answer"] == say("needs_confirmation", "en", summary=approval["summary"])
    assert body["model_errors"] == [] and len(world.purchase_orders()) == orders
    assert world.sent("host", "abort") == [{"type": "abort", "reason": "needs_confirmation"}]


@pytest.mark.pi
def test_every_call_is_on_record_with_route_source_agent(operator_run):
    world, body, _ = operator_run
    trail = [(e["user_id"], e["target"], e["status"], e["route_source"]) for e in world.audit()]
    assert trail == [("olga", name, "ok", "agent") for name in CASE_READS] + [
        ("olga", "create_po", "needs_confirmation", "agent")]
    assert body["audit_ids"] == [e["id"] for e in world.audit()]


@pytest.mark.pi
def test_the_model_saw_fenced_figures_never_a_row(operator_run):
    world, _, _ = operator_run
    results = [json.loads(m["content"]) for m in world.sent("host", "tool_result") if m["ok"]]
    assert len(results) == len(CASE_READS)
    for result in results:
        assert set(result) == NARRATOR_KEYS
        assert fence_key(result["scope"]) == "scope"
        assert result["message"] == "" or fence_key(result["message"]) == "message"
    # the scorecard's message names the suppliers the script reads one from: it was sent, fenced
    assert sum(bool(result["message"]) for result in results) >= 1


# ---------------------------------------------------------------- approving it


@pytest.mark.pi
def test_approving_the_proposal_runs_it_exactly_once(pi_harness, make_case_world):
    world = make_case_world(harness=pi_harness)
    before = world.purchase_orders()
    grant_id = world.ask("olga")["approval"]["grant_id"]

    done = world.approve(grant_id)
    assert done.status_code == 200
    assert (done.json()["status"], done.json()["target"]) == ("ok", "create_po")
    (new,) = [o for o in world.purchase_orders() if o not in before]
    assert {k: new[k] for k in ("sku_id", "qty", "supplier_id", "status")} == {
        **PROPOSAL, "status": "draft"}

    again = world.approve(grant_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"
    assert len(world.purchase_orders()) == len(before) + 1
    trail = world.records(grant_id)
    assert [(e["status"], e["problem"]) for e in trail] == [
        ("needs_confirmation", None), ("started", None), ("ok", None), ("blocked", "grant_used")]
    assert (trail[0]["user_id"], trail[0]["route_source"]) == ("olga", "agent")
    assert world.pending() == []


@pytest.mark.pi
def test_an_approval_whose_params_were_changed_after_the_request_is_refused(pi_harness,
                                                                          make_case_world):
    world = make_case_world(harness=pi_harness)
    before = len(world.purchase_orders())
    grant_id = world.ask("olga")["approval"]["grant_id"]

    with world.runtime.database.transaction() as conn:
        conn.execute("UPDATE pending_request SET params = ? WHERE grant_id = ?",
                     (json.dumps({**PROPOSAL, "qty": 7400}), grant_id))
    refused = world.approve(grant_id)
    assert refused.status_code == 200 and refused.json()["status"] == "blocked"
    # blocked for the reason that matters: the grant was given for other parameters
    assert [s["facts"] for s in steps(refused.json(), "gate", "blocked")] == [
        {"stage": "confirmation"}]
    assert len(world.purchase_orders()) == before
    (request, refusal) = world.records(grant_id)
    assert request["status"] == "needs_confirmation"
    assert (refusal["status"], refusal["user_id"]) == ("blocked", "olga")


# ---------------------------------------------------------------- the same case as a viewer


@pytest.mark.pi
def test_a_viewer_is_never_offered_create_po_and_each_attempt_is_refused_on_record(
        pi_harness, make_case_world):
    world = make_case_world(harness=pi_harness)
    before = len(world.purchase_orders())
    body = world.ask("vera")

    exposed = start_of_run(body)["facts"]["exposed"]
    assert "create_po" not in exposed and "supplier_scorecard" not in exposed  # roles
    registry = world.runtime.registry
    assert {registry.target(n).effect.value for n in exposed} == {"read"}
    (start,) = world.sent("host", "start")
    assert "create_po" not in {t["name"] for t in start["tools"]}
    # the script asked for both anyway; Pi refused each itself, and each is traced and audited
    assert [m["name"] for m in world.sent("worker", "tool_call")] == [
        "late_shipments", "reorder_suggestion"]
    assert ran_after_gate(body) == ["late_shipments", "reorder_suggestion"]
    assert [s["facts"] for s in steps(body, "gate", "blocked")] == [
        {"stage": "lookup", "target": "invalid", "reason": "unknown_tool", "blocked_by": "pi"}
    ] * 2
    trail = [(e["user_id"], e["target"], e["status"], e["problem"]) for e in world.audit()]
    assert trail == [
        ("vera", "late_shipments", "ok", None), ("vera", "reorder_suggestion", "ok", None),
        ("vera", "supplier_scorecard", "blocked",
         "'supplier_scorecard' is not an available tool"),
        ("vera", "create_po", "blocked", "'create_po' is not an available tool")]
    # nothing waits for approval and nothing was written
    assert body["approval"] is None and world.pending() == []
    assert len(world.purchase_orders()) == before
    facts = end_of_run(body)["facts"]
    assert (facts["runtime"], facts["model"], facts["stop_reason"]) == ("pi", "mock", "answered")
    assert body["answer"] == say("mock_script_end", "en")


# ---------------------------------------------------------------- the built-in loop


def test_the_builtin_loop_runs_the_same_case_on_the_mock_tier(make_case_world):
    """Tier 1: the built-in loop against the mock model, no Node. Same script, same gate."""
    world = make_case_world(runtime="builtin")
    before = len(world.purchase_orders())
    body = world.ask("olga")

    assert start_of_run(body)["facts"]["runtime"] == "builtin"
    assert gate_targets(body) == [(name, "ok") for name in CASE_READS] + [
        ("confirmation", "blocked")]
    assert ran_after_gate(body) == CASE_READS
    facts = end_of_run(body)["facts"]
    assert (facts["runtime"], facts["model"], facts["stop_reason"]) == (
        "builtin", "mock", "needs_confirmation")
    assert [(e["target"], e["status"], e["route_source"]) for e in world.audit()] == [
        (name, "ok", "agent") for name in CASE_READS] + [
        ("create_po", "needs_confirmation", "agent")]

    done = world.approve(body["approval"]["grant_id"])
    assert done.json()["status"] == "ok"
    assert len(world.purchase_orders()) == before + 1
    assert world.approve(body["approval"]["grant_id"]).status_code == 409


def test_without_the_example_plugin_the_scorecard_is_not_offered(make_case_world):
    """The start step names what the deployment's plugins provide: no plugin path, no
    scorecard. The script's call to it is refused, so its proposal names no supplier."""
    world = make_case_world(runtime="builtin", plugins=False)
    body = world.ask("olga")

    assert "supplier_scorecard" not in start_of_run(body)["facts"]["exposed"]
    assert gate_targets(body) == [("late_shipments", "ok"), ("reorder_suggestion", "ok"),
                                  ("invalid", "blocked"), ("confirmation", "blocked")]
    assert "best-ranked for this SKU" in body["approval"]["summary"]


# ---------------------------------------------------------------- the checks themselves


def test_a_call_with_no_passed_gate_step_right_before_it_fails_the_gate_check(make_case_world):
    """``ran_after_gate`` can fail: swap a read's gate step with the execute step after it, or
    turn that gate step into a blocked one, and the trace no longer shows a gated call."""
    body = make_case_world(runtime="builtin").ask("olga")
    assert ran_after_gate(body) == CASE_READS
    trace = body["trace"]
    gate = next(at for at, s in enumerate(trace) if (s["step"], s["status"]) == ("gate", "ok"))
    swapped = [*trace[:gate], trace[gate + 1], trace[gate], *trace[gate + 2:]]
    with pytest.raises(AssertionError, match="no passed gate step right before it"):
        ran_after_gate({**body, "trace": swapped})
    blocked = [*trace[:gate], {**trace[gate], "status": "blocked"}, *trace[gate + 1:]]
    with pytest.raises(AssertionError, match="no passed gate step right before it"):
        ran_after_gate({**body, "trace": blocked})


# ---------------------------------------------------------------- the sentence in the docs


def test_the_documents_give_the_request_the_mock_scripts_word_for_word():
    """The request a reader is told to ask is the one the mock scripts. Reworded in one place
    only, it would get no script and the case would not run. Line wraps do not count."""
    missing = [path for path in CASE_DOCUMENTS
               if EXPLORATION_CASE not in unwrapped((REPO / path).read_text(encoding="utf-8"))]
    assert not missing
