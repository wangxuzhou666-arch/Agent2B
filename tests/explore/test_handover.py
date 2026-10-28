"""The explicit hand-over to exploration, and the explore node's guard (core.explore.handover).

Routing to ``open_task`` emits a ``handover`` step (reason ``intent_not_covered``, with its
evidence). The explore node runs only for such a request: one that names an explicit target,
already reached an outcome on the fixed path, or was never handed over is refused before any
runtime starts (stop reason ``handover_refused``), and the refusal is traced, audited and said.
No graph edge leads into ``open_task`` but the router's, and none leads back from ``execute``.
"""

from dataclasses import replace

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from tests.explore.boundary import BOUNDARY, MemorySink, ask, call, engine, scripted
from tests.explore.world import fake_harness, fake_launcher
from tests.fakes import FakeOutcome, FakePipeline, ScriptedProvider, fake_lock, make_ctx
from tests.server.conftest import PASSWORD, Api
from twotrack.core import load_registry
from twotrack.core.contract import RouteDecision, RouteKind, RouteSource, TraceStatus, TraceStep
from twotrack.core.explore.explorer import ExploreRequest
from twotrack.core.explore.handover import Refusal, handover_step, refusal
from twotrack.core.explore.pi import PiExplorer
from twotrack.core.graph import GraphState
from twotrack.core.graph.builder import (
    _BRANCH,
    NODES,
    RunContext,
    _after_ingress,
    _after_open_task,
    _after_router,
    compiled_graph,
)
from twotrack.core.i18n import say
from twotrack.providers.base import AgentStep, guarded
from twotrack.runtime import build_runtime
from twotrack.seed import seed_database
from twotrack.settings import Settings


class SpyExplorer:
    """An explorer that must never be asked to run."""

    name = "spy"

    def __init__(self) -> None:
        self.requests: list[ExploreRequest] = []

    def explore(self, request: ExploreRequest):
        self.requests.append(request)
        raise AssertionError("the guard let a refused request reach the explorer")


def handover() -> TraceStep:
    return handover_step(rule_target=None, llm_target=None, semantic_best=None,
                         semantic_score=0.0, threshold=0.5, candidates=4)


# ---------------------------------------------------------------- the hand-over step


def test_routing_to_open_task_hands_over_with_its_evidence():
    response = ask(engine(scripted(AgentStep(text="Nothing to add."))))

    steps = [s.step for s in response.trace]
    assert steps[:4] == ["candidates", "understand", "handover", "execute"]
    (step,) = [s for s in response.trace if s.step == "handover"]
    assert (step.status, step.source) == (TraceStatus.OK, RouteSource.AGENT)
    assert step.facts == {
        "reason": "intent_not_covered", "to": "open_task",
        "evidence": {"rule": None, "llm": None, "arms_silent": True,
                     "semantic": {"best": None, "score": 0.0, "threshold": 0.5, "below": True},
                     "candidates": 4}}


@pytest.mark.parametrize(("query", "options", "kind"), [
    ("stock snapshot", {}, "tool"),
    ("anything at all", {"explicit_target": "read_ok"}, "tool"),
    ("zzz qqq unmatched gibberish", {"explicit_target": "read_hidden_nope"}, "clarify"),
])
def test_no_other_route_hands_over(query, options, kind):
    response = ask(engine(scripted()), query=query, **options)

    assert response.decision.kind.value == kind
    assert "handover" not in [s.step for s in response.trace]


# ---------------------------------------------------------------- the graph's structure


def test_only_the_router_leads_into_open_task_and_nothing_leads_back_from_execute():
    graph = compiled_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}

    assert {s for s, t in edges if t == "open_task"} == {"router"}
    assert {t for s, t in edges if s == "execute"} == {"narrate", "respond"}
    seen, todo = set(), ["execute"]
    while todo:  # nothing reachable from execute leads into open_task either
        node = todo.pop()
        seen.add(node)
        todo.extend(t for s, t in edges if s == node and t not in seen)
    assert "open_task" not in seen and "router" not in seen


# ---------------------------------------------------------------- the guard, unit


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"explicit_target": "read_ok", "trace": [handover()]}, Refusal("explicit_target")),
    ({"trace": []}, Refusal("no_handover")),
    ({"trace": [TraceStep(step="handover", status=TraceStatus.OK,
                          facts={"reason": "something_else"})]}, Refusal("no_handover")),
    ({"trace": [TraceStep(step="gate", status=TraceStatus.BLOCKED, facts={"stage": "authz"}),
                handover()]}, Refusal("earlier_outcome", "denied")),
    ({"trace": [TraceStep(step="gate", status=TraceStatus.BLOCKED, facts={"stage": "params"}),
                handover()]}, Refusal("earlier_outcome", "invalid")),
    ({"trace": [TraceStep(step="gate", status=TraceStatus.BLOCKED,
                          facts={"stage": "precondition"}), handover()]},
     Refusal("earlier_outcome", "blocked")),
    ({"trace": [TraceStep(step="execute", status=TraceStatus.ERROR, facts={"error": "X"}),
                handover()]}, Refusal("earlier_outcome", "error")),
    ({"trace": [TraceStep(step="execute", status=TraceStatus.BLOCKED,
                          facts={"loop": "stopped", "reason": "max_turns"}), handover()]},
     Refusal("earlier_outcome", "budget")),
    ({"trace": [handover()], "error": "boom"}, Refusal("earlier_outcome", "error")),
    ({"trace": [handover()], "grant": object()},
     Refusal("earlier_outcome", "needs_confirmation")),
    ({"trace": [handover()], "audit_ids": [7]}, Refusal("earlier_outcome", "executed")),
])
def test_the_guard_refuses(kwargs, expected):
    assert refusal(**{"explicit_target": None, **kwargs}) == expected


def test_the_guard_lets_a_handed_over_request_through():
    routing = [TraceStep(step="candidates", status=TraceStatus.ERROR,  # a failed precondition
                         facts={"precondition": "x", "error": "E"}),
               TraceStep(step="understand", status=TraceStatus.OK), handover()]
    assert refusal(explicit_target=None, trace=routing) is None


# ---------------------------------------------------------------- the guard, in the node


def run_node(state: GraphState, sink: MemorySink | None = None):
    spy = SpyExplorer()
    eng = engine(scripted(), explorer=spy)
    if sink is not None:
        eng = replace(eng, audit=sink)
    ctx = make_ctx("operator", request_id="r1", language="en")
    context = RunContext(engine=eng, ctx=ctx, provider=guarded(eng.provider), lock=fake_lock)
    update = NODES["open_task"](state, Runtime(context=context))
    assert spy.requests == []  # no runtime started
    return update


def open_task_state(**fields) -> GraphState:
    decision = RouteDecision(kind=RouteKind.OPEN_TASK, confidence=0.0, source=RouteSource.AGENT)
    return GraphState(query="zzz", decision=decision, **fields)


@pytest.mark.parametrize(("fields", "reason", "outcome"), [
    ({"explicit_target": "read_ok", "trace": [handover()]}, "explicit_target", None),
    ({"trace": [TraceStep(step="gate", status=TraceStatus.BLOCKED, facts={"stage": "authz"}),
                handover()]}, "earlier_outcome", "denied"),
    ({"trace": []}, "no_handover", None),
])
def test_a_refused_request_never_starts_a_runtime_and_is_traced_audited_and_said(
        fields, reason, outcome):
    sink = MemorySink()
    update = run_node(open_task_state(**fields), sink)

    (step,) = update["trace"]
    assert (step.step, step.status) == ("execute", TraceStatus.ERROR)
    assert step.facts == {"loop": "refused", "runtime": "spy", "stop_reason": "handover_refused",
                          "error": "HandoverRefused", "refused": reason, "outcome": outcome}
    (entry,) = sink.entries
    assert (entry.status.value, entry.route_source, entry.target, entry.user_id) == (
        "error", "agent", None, "u1")
    assert entry.problem == f"handover_refused: {reason}" + (f" ({outcome})" if outcome else "")
    assert entry.trace[-1] == step  # the record replays the refusal
    assert update["audit_ids"] == [1] and update["audit_mark"] == len(fields["trace"]) + 1
    assert (update["draft"], update["draft_source"]) == (
        say(f"explore_refused_{reason}", "en"), "template")
    assert "result" not in update and "grant" not in update


def test_a_refusal_keeps_the_records_the_request_already_has():
    trace = [TraceStep(step="execute", status=TraceStatus.ERROR, facts={"error": "X"}),
             handover()]
    update = run_node(open_task_state(trace=trace, audit_ids=[41]), MemorySink())
    assert update["audit_ids"] == [41, 1]


def test_without_an_audit_sink_the_refusal_is_still_traced_and_said():
    update = run_node(open_task_state(trace=[]))
    assert update["trace"][0].facts["stop_reason"] == "handover_refused"
    assert "audit_ids" not in update
    assert update["draft"] == say("explore_refused_no_handover", "en")


# ---------------------------------------------------------------- a regression, simulated


def rogue_graph():
    """The query graph with the edge it must never grow: a fixed path that did not produce a
    result goes on to exploration instead of answering."""
    g = StateGraph(GraphState, context_schema=RunContext)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_edge(START, "ingress")
    g.add_conditional_edges("ingress", _after_ingress, {"router": "router", "respond": "respond"})
    g.add_conditional_edges("router", _after_router, {v: v for v in set(_BRANCH.values())})
    g.add_conditional_edges("execute", lambda s: "narrate" if s.result is not None
                            else "open_task", {"narrate": "narrate", "open_task": "open_task"})
    g.add_conditional_edges("open_task", _after_open_task,
                            {"narrate": "narrate", "respond": "respond"})
    g.add_edge("chat", "narrate")
    g.add_edge("clarify", "respond")
    g.add_edge("narrate", "respond")
    g.add_edge("respond", "record")
    g.add_edge("record", END)
    return g.compile()


class DenyingPipeline(FakePipeline):
    """Denies every run at authorization, the way the real pipeline traces it."""

    def run(self, ctx, target, params, **kwargs):
        self.runs.append((target, dict(params)))
        step = TraceStep(step="gate", status=TraceStatus.BLOCKED, facts={"stage": "authz"})
        ctx.emit(step)
        return FakeOutcome("blocked", problem="not allowed", trace=[step])


@pytest.mark.parametrize("options", [{"explicit_target": "read_ok"}, {}])
def test_even_a_graph_that_retried_a_denied_call_as_exploration_would_refuse_it(
        monkeypatch, options):
    from twotrack.core.graph import builder

    spy = SpyExplorer()
    pipeline = DenyingPipeline(BOUNDARY)
    monkeypatch.setattr(builder, "compiled_graph", rogue_graph)
    query = "anything" if options else "stock snapshot"
    response = ask(engine(scripted(call("read_ok")), explorer=spy, pipeline=pipeline),
                   query=query, **options)

    assert response.decision.kind is RouteKind.TOOL and pipeline.runs == [("read_ok", {})]
    assert spy.requests == []
    (stop,) = [s for s in response.trace if s.facts.get("stop_reason")]
    assert stop.facts["refused"] == ("explicit_target" if options else "earlier_outcome")
    assert response.answer == say(f"explore_refused_{stop.facts['refused']}", "en")


# ---------------------------------------------------------------- a viewer names a write


@pytest.mark.parametrize("runtime", ["builtin", "pi"])
def test_a_viewer_naming_create_po_is_denied_on_the_fixed_path_and_never_explores(
        tmp_path, runtime):
    provider = ScriptedProvider(steps=[call("create_po", sku_id="SKU-1", qty=5)])
    launcher = fake_launcher("answer")
    explorer = PiExplorer(fake_harness(tmp_path), None, launcher=launcher) if runtime == "pi" \
        else None
    settings = Settings(data_dir=tmp_path / "data", allow_open_task=True, explorer=runtime)
    rt = build_runtime(settings, registry=load_registry(extra_paths=[]), provider=provider,
                       explorer=explorer)
    try:
        seed_database(rt.store, rt.registry)
        rt.users.add("vera", PASSWORD, ["viewer"])
        rt.users.add("ada", PASSWORD, ["admin"])
        api = Api(rt)
        orders = rt.store.count("purchase_order")
        found = api.post("/api/query?stream=false", "vera", json={
            "query": "order 5 more of SKU-1", "explicit_target": "create_po",
            "explicit_params": {"sku_id": "SKU-1", "qty": 5}})
        assert found.status_code == 200
        body = found.json()

        assert (body["decision"]["kind"], body["decision"]["source"]) == ("clarify", "explicit")
        assert [(s["step"], s["status"]) for s in body["trace"]] == [
            ("candidates", "ok"), ("gate", "blocked"), ("understand", "ok"),
            ("respond", "ok")]
        assert body["trace"][1]["facts"]["target"] == "create_po"
        # nothing explored: no model was asked to plan, no worker started
        assert not [m for m, _ in provider.received if m == "agent_step"]
        assert launcher.procs == []
        # denied, on record, and nothing waits or changed
        (entry,) = api.get("/api/audit", "ada").json()["items"]
        assert (entry["user_id"], entry["target"], entry["status"], entry["route_source"]) == (
            "vera", "create_po", "blocked", "explicit")
        assert body["approval"] is None and rt.store.count("purchase_order") == orders
        assert api.get("/api/approvals", "ada").json()["items"] == []
    finally:
        rt.close()
