"""Workflow runner and open-task agent loop."""

import pytest

from tests.fakes import (
    REGISTRY,
    ROW_MARKERS,
    FakePipeline,
    ScriptedProvider,
    fake_lock,
    make_ctx,
)
from twotrack.core import TraceStatus
from twotrack.core.agent_loop import run_agent_loop
from twotrack.core.workflow import WorkflowInputError, run_workflow
from twotrack.providers import AgentStep, ToolCall


def run(name, provider, *, query="", steps=None, narrations=None):
    ctx = make_ctx("operator", sink=steps.append if steps is not None else None, query=query)
    return run_workflow(ctx, REGISTRY.workflow(name), {}, provider, lock=fake_lock,
                        on_narration=narrations.append if narrations is not None else None)


def assert_no_rows_sent(provider):
    sent = provider.payloads()
    assert not any(str(marker) in sent for marker in ROW_MARKERS)


# ---------------------------------------------------------------- workflow


def test_workflow_runs_steps_in_order_and_narrates_from_the_view_only():
    steps, narrations = [], []
    provider = ScriptedProvider(narration="Order 420 units first.")
    result = run("reorder_plan", provider, steps=steps, narrations=narrations)
    assert result.aggregates == {"units_to_order": 420}
    assert result.message == "Order 420 units first."
    assert narrations[0].source == "model" and narrations[0].stripped == 0
    assert [s.facts["step"] for s in steps] == ["compute", "explain"]
    assert_no_rows_sent(provider)


def test_llm_steps_name_their_provider():
    from twotrack.providers import MockProvider

    steps = []
    run("reorder_plan", MockProvider(), steps=steps)
    compute, explain = steps
    assert "provider" not in compute.facts  # deterministic steps call no model
    assert (explain.facts["narration"], explain.facts["provider"], explain.facts["mock"]) == (
        "model", "mock", True)
    steps = []
    run("reorder_plan", ScriptedProvider(narration="Order 420 units first."), steps=steps)
    assert (steps[-1].facts["provider"], steps[-1].facts["mock"]) == ("ScriptedProvider", False)
    steps = []
    run("demand_brief", ScriptedProvider(chat_text='{"horizon_weeks": 6}'), steps=steps,
        query="brief for six weeks")
    assert steps[0].facts["kind"] == "llm" and steps[0].facts["provider"] == "ScriptedProvider"


def test_workflow_narration_is_numerically_locked():
    narrations = []
    provider = ScriptedProvider(narration="Order 420 units. Demand will rise 31 percent.")
    result = run("reorder_plan", provider, narrations=narrations)
    assert result.message == "Order 420 units."
    assert narrations[0].stripped == 1


def test_workflow_narration_failure_degrades_to_template():
    narrations = []
    result = run("reorder_plan", ScriptedProvider(fail={"narrate"}), narrations=narrations)
    assert narrations[0].source == "template" and "420" in result.message


def test_first_llm_step_output_is_schema_gated():
    provider = ScriptedProvider(chat_text='{"horizon_weeks": 6}')
    result = run("demand_brief", provider, query="brief for six weeks")
    assert result.aggregates == {"horizon_weeks": 6}
    assert "brief for six weeks" in provider.payloads("chat")


@pytest.mark.parametrize("chat_text", ["sure, six weeks", '{"horizon_weeks": "soon"}'])
def test_first_llm_step_failure_is_a_user_facing_input_problem(chat_text):
    with pytest.raises(WorkflowInputError, match="restate"):
        run("demand_brief", ScriptedProvider(chat_text=chat_text), query="a brief")


# ---------------------------------------------------------------- agent loop


def loop(provider, *roles, steps=None, **kw):
    ctx = make_ctx(*(roles or ("operator",)), sink=steps.append if steps is not None else None)
    pipeline = FakePipeline()
    outcome = run_agent_loop("q", ctx, REGISTRY.candidates(ctx), provider, pipeline, **kw)
    return outcome, pipeline


def calls(*names, **arguments):
    return AgentStep(tool_calls=[ToolCall(name=n, arguments=arguments) for n in names])


def test_agent_loop_calls_tools_through_pipeline_and_sees_only_views():
    provider = ScriptedProvider(steps=[calls("stock_level"),
                                       AgentStep(text="You hold 15543 units.")])
    outcome, pipeline = loop(provider)
    assert outcome.status == "done" and outcome.text_source == "model"
    assert pipeline.runs == [("stock_level", {})]
    assert 15543.0 in outcome.allowed
    assert "15543" in provider.payloads("agent_step")
    assert_no_rows_sent(provider)


def test_agent_loop_stops_at_write_tool_with_needs_confirmation():
    provider = ScriptedProvider(steps=[calls("stock_adjust", item="a", qty=5),
                                       calls("order_status")])
    outcome, pipeline = loop(provider)
    assert outcome.status == "needs_confirmation"
    assert outcome.grant.token == FakePipeline.GRANT
    assert [r[0] for r in pipeline.runs] == ["stock_adjust"]


def test_agent_loop_stops_on_turn_budget_with_blocked_trace_step():
    steps = []
    provider = ScriptedProvider(steps=lambda messages, cards: calls("order_status"))
    outcome, pipeline = loop(provider, steps=steps, max_turns=3)
    assert outcome.status == "budget" and outcome.turns == 3
    assert len(pipeline.runs) == 2  # the final turn is offered no tools
    stop = steps[-1]
    assert stop.status is TraceStatus.BLOCKED and stop.facts["reason"] == "max_turns"
    assert provider.received[-1][1].endswith("[]]")  # no cards on the final turn


def test_agent_loop_stops_on_tool_call_budget():
    steps = []
    provider = ScriptedProvider(steps=[calls("order_status", "lead_time", "stock_level")])
    outcome, pipeline = loop(provider, steps=steps, max_tool_calls=2)
    assert outcome.status == "budget" and len(pipeline.runs) == 2
    assert steps[-1].facts["reason"] == "max_tool_calls"
    # the answer says why it stopped first, then shows what the run had found
    assert outcome.text.startswith("Exploration stopped early: it reached its tool-call limit")
    assert "Last result before it stopped:" in outcome.text


def test_agent_loop_refuses_unknown_hidden_and_malformed_calls():
    malformed = AgentStep(tool_calls=[ToolCall(name="order_status", malformed=True)])
    provider = ScriptedProvider(steps=[calls("admin_purge"), calls("nope"), malformed,
                                       AgentStep(text="Nothing to report.")])
    steps = []
    outcome, pipeline = loop(provider, steps=steps, max_turns=5)
    assert pipeline.runs == []
    assert [c.status for c in outcome.calls] == ["blocked", "blocked", "invalid"]
    assert "nope" not in str([s.facts for s in steps])  # raw model names never reach the trace


def test_agent_loop_provider_failure_returns_deterministic_text():
    outcome, _ = loop(ScriptedProvider(fail={"agent_step"}))
    assert outcome.status == "error" and outcome.text_source == "template"
