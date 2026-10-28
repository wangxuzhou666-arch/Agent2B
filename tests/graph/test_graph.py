"""The query graph end to end, mostly on the zero-key mock provider."""

from tests.fakes import (
    REGISTRY,
    ROW_MARKERS,
    FakePipeline,
    RecordingScrubber,
    ScriptedProvider,
    fake_lock,
    make_ctx,
)
from twotrack.core import RouteKind, RouteSource, TraceStatus, TraceStep
from twotrack.core.graph import Engine, GraphSettings, QueryResponse, run_query, stream_query
from twotrack.core.graph.builder import NODES, compiled_graph
from twotrack.providers import AgentStep, LLMRoute, MockProvider, ToolCall


def engine(provider=None, **kw):
    settings = GraphSettings(allow_open_task=kw.pop("agent", False))
    return Engine(registry=REGISTRY, provider=provider or MockProvider(),
                  pipeline=kw.pop("pipeline", FakePipeline()), lock=fake_lock,
                  settings=settings, **kw)


def ask(query, *roles, provider=None, sink=None, facts=None, **kw):
    options = {k: kw.pop(k) for k in ("explicit_target", "explicit_params", "grant_token")
               if k in kw}
    ctx = make_ctx(*(roles or ("operator",)), sink=sink, **(facts or {}))
    return run_query(engine(provider, **kw), ctx, query, **options)


def steps_of(response):
    return [s.step for s in response.trace]


def test_graph_compiles_with_every_node():
    assert set(compiled_graph().get_graph().nodes) >= set(NODES)


def test_deterministic_tool_path():
    pipeline = FakePipeline()
    response = ask("what is our stock level", "viewer", pipeline=pipeline)
    assert pipeline.sources == [RouteSource.RULE]  # route source reaches the audit
    assert response.decision.kind is RouteKind.TOOL
    assert response.answer_source == "model"
    assert "15543" in response.answer and response.narrative_stripped == 0
    assert response.columns == ["item", "qty"] and len(response.rows) == 2
    assert response.provenance.tool == "stock_level"
    assert steps_of(response) == ["candidates", "understand", "execute", "narrate", "respond"]


def test_clarify_path_offers_labelled_options_and_runs_nothing():
    pipeline = FakePipeline()
    response = ask("lead time please", "viewer", pipeline=pipeline)
    assert response.decision.kind is RouteKind.CLARIFY
    assert [o.name for o in response.clarify_options] == ["delivery_delay", "lead_time"]
    assert all(o.description for o in response.clarify_options)
    assert pipeline.runs == [] and "execute" not in steps_of(response)


def test_open_task_path_with_scripted_agent():
    provider = ScriptedProvider(steps=[
        AgentStep(tool_calls=[ToolCall(name="stock_level")]),
        AgentStep(text="You hold 15543 units. Expect 999 more next month."),
    ])
    response = ask("summarize where we stand", provider=provider, agent=True)
    assert response.decision.kind is RouteKind.OPEN_TASK
    assert response.answer == "You hold 15543 units."
    assert response.narrative_stripped == 1
    assert len(response.rows) == 2
    assert not any(str(m) in provider.payloads() for m in ROW_MARKERS)


def test_open_task_on_mock_is_honest_and_runs_nothing():
    response = ask("summarize where we stand", agent=True)
    assert response.decision.kind is RouteKind.OPEN_TASK
    assert "No language model" in response.answer


def test_chat_path():
    response = ask("write a poem about the sea")
    assert response.decision.kind is RouteKind.CHAT
    assert response.answer and response.rows == []
    assert steps_of(response)[-2:] == ["narrate", "respond"]


def test_chat_text_citing_figures_is_locked():
    provider = ScriptedProvider(chat_text="We probably shipped 5000 units. Ask a report.")
    response = ask("write a poem about the sea", provider=provider)
    assert response.answer == "Ask a report." and response.narrative_stripped == 1


def test_narration_never_receives_rows():
    provider = ScriptedProvider()
    ask("what is our stock level", "viewer", provider=provider)
    sent = provider.payloads("narrate")
    assert "15543" in sent
    assert not any(str(marker) in sent for marker in ROW_MARKERS)


def test_scrubber_sees_every_provider_payload():
    scrubber = RecordingScrubber()
    provider = ScriptedProvider()
    response = ask("what is our stock level, reply to ops@example.co", "viewer",
                   provider=provider, scrubber=scrubber)
    assert {m for m, _ in provider.received} == {"route", "narrate"}
    assert "@" not in provider.payloads()
    assert any("ops@example.co" in s for s in scrubber.seen)
    assert response.trace[-1].facts["redacted"] == 2  # route query + narrate question


def test_provider_failure_falls_back_to_template_narration():
    response = ask("what is our stock level", "viewer",
                   provider=ScriptedProvider(fail={"route", "narrate"}))
    assert response.answer_source == "template"
    assert "15543" in response.answer and response.error is None


def test_hallucinated_number_is_stripped_by_the_lock():
    response = ask("what is our stock level", "viewer",
                   provider=MockProvider(hallucinated_number=31337))
    assert "31337" not in response.answer and response.narrative_stripped == 1


def test_workflow_narration_comes_from_the_runner_once():
    provider = ScriptedProvider(narration="Order 420 units first.")
    response = ask("reorder plan", provider=provider)
    assert response.answer == "Order 420 units first."
    assert sum(1 for m, _ in provider.received if m == "narrate") == 1


def test_workflow_needing_input_asks_for_it():
    response = ask("demand brief", provider=ScriptedProvider(chat_text="no idea"))
    assert "restate" in response.answer and response.error is None
    assert response.trace[-2].status is TraceStatus.BLOCKED


def test_write_tool_returns_grant_then_runs_with_it():
    first = ask("anything", explicit_target="stock_adjust", explicit_params={"qty": 3})
    assert first.grant is not None and first.answer.startswith("Confirmation needed")
    second = ask("anything", explicit_target="stock_adjust", explicit_params={"qty": 3},
                 grant_token=first.grant.token)
    assert second.grant is None and second.answer_source in ("model", "template")


def test_explicit_target_outside_candidates_is_blocked():
    pipeline = FakePipeline()
    response = ask("purge", "viewer", explicit_target="admin_purge", pipeline=pipeline)
    assert response.decision.source is RouteSource.EXPLICIT
    gate = next(s for s in response.trace if s.step == "gate")
    assert gate.status is TraceStatus.BLOCKED
    assert pipeline.runs == [] and "not available" in response.answer


def test_empty_query_is_rejected_at_ingress():
    response = ask("   ")
    assert response.error and response.decision is None


def test_pipeline_steps_are_not_recorded_twice_and_reach_caller_sink():
    seen = []
    response = ask("what is our stock level", "viewer", sink=seen.append)
    assert steps_of(response).count("execute") == 1
    assert [s.step for s in seen] == steps_of(response)


def test_stream_yields_trace_steps_then_response():
    events = list(stream_query(engine(), make_ctx("viewer"), "what is our stock level"))
    *steps, final = events
    assert all(isinstance(s, TraceStep) for s in steps)
    assert isinstance(final, QueryResponse)
    assert [s.step for s in steps] == steps_of(final)


def test_llm_strong_alone_asks_for_confirmation():
    provider = ScriptedProvider(route=LLMRoute(target="order_status", confidence=0.85))
    response = ask("anything new from the folks we buy from", provider=provider)
    assert response.decision.source is RouteSource.LLM_CONFIRM
    assert [o.name for o in response.clarify_options] == ["order_status"]
