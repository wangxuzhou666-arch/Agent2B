"""System A: a standard tool-calling agent over the same tools, through the OpenAI SDK.

The common pattern, on purpose: the model gets every tool as a function (the same JSON schemas
twotrack's own agent loop sends), each call runs through twotrack's pipeline (same authz,
preconditions, param validation and write grants), and the tool result goes back to the model
as JSON *including rows*. The model writes the final answer itself; nothing checks its numbers.

The client is anything shaped like ``openai.OpenAI`` (``client.chat.completions.create``), so
the scripted fake in ``bench.fake_llm`` and a metered real client drop in unchanged.

A model call that fails ends the question, and is recorded the way twotrack's answers record
theirs (``BaselineRun.failures``: the call, the error class, the HTTP status, never the message):
an HTTP error, a timeout, or a reply the loop cannot use (no message, or neither text nor a tool
call, what twotrack's provider calls ``UnparseableReply``).
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from twotrack.core import RouteSource, ToolResult
from twotrack.core.ports import status_of
from twotrack.core.routing.llm_arm import tool_cards
from twotrack.providers.base import ToolCard, http_status_of

from .config import DEFAULT_MAX_TURNS, DEFAULT_TIMEOUT
from .world import World, template_workflow_runner

BASELINE_SYSTEM = """\
You answer a business user's questions about a supply-chain database by calling the tools
provided. Today is {today}.
- Call tools to get data. Each tool returns JSON with "rows", "aggregates", "columns",
  "row_count", "scope" and "message".
- Base every number in your answer on the tool results. Do not estimate or invent figures.
- Write every number in digits.
- If the tools cannot answer the question, say so instead of guessing.
- Answer in the language of the question, in a few plain sentences."""

NEEDS_CONFIRMATION = ("This call changes data and waits for a person to confirm it; nothing "
                      "was changed.")


def function_spec(card: ToolCard) -> dict[str, Any]:
    """The wire format of twotrack's agent loop (description + route hint, params schema)."""
    description = card.description
    if card.route_hint:
        description += f" Use when: {card.route_hint}"
    return {"type": "function",
            "function": {"name": card.name, "description": description,
                         "parameters": card.params_schema}}


def function_specs(world: World) -> list[dict[str, Any]]:
    return [function_spec(c) for c in tool_cards(world.registry.candidates(world.ctx()))]


def result_payload(result: ToolResult) -> dict[str, Any]:
    """What the baseline model reads: the narrator view plus every row."""
    return {"rows": result.rows, "aggregates": dict(result.aggregates),
            "columns": list(result.columns), "row_count": result.provenance.row_count,
            "scope": result.provenance.scope, "message": result.message}


@dataclass
class ToolCallRecord:
    name: str
    arguments: Any
    status: str
    payload_chars: int
    problem: str | None = None


@dataclass
class BaselineRun:
    answer: str
    turns: int
    stop: str  # answer | max_turns | error
    calls: list[ToolCallRecord] = field(default_factory=list)
    results: list[tuple[str, ToolResult]] = field(default_factory=list)  # (tool, ok result)
    payloads: list[str] = field(default_factory=list)  # every tool message the model read
    error: str | None = None
    latency_ms: float = 0.0
    # failed model calls, shaped like twotrack's ``ModelCallError`` (call, error, http_status)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def fail(self, error: str, http_status: int | None = None) -> None:
        self.error, self.stop = error, "error"
        self.failures.append({"call": "baseline", "error": error, "http_status": http_status})


def execute_call(world: World, name: str, raw_arguments: str | None
                 ) -> tuple[ToolCallRecord, str, ToolResult | None]:
    try:
        arguments = json.loads(raw_arguments or "{}")
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        payload = json.dumps({"status": "invalid", "problem": "arguments must be a JSON object"})
        return (ToolCallRecord(name, raw_arguments, "invalid", len(payload),
                               "arguments must be a JSON object"), payload, None)
    outcome = world.pipeline.run(world.ctx(), name, arguments,
                                 workflow_runner=template_workflow_runner,
                                 route_source=RouteSource.AGENT)
    status = status_of(outcome)
    if status == "ok" and outcome.result is not None:
        payload = json.dumps(result_payload(outcome.result), ensure_ascii=False, default=str)
        return ToolCallRecord(name, arguments, status, len(payload)), payload, outcome.result
    if status == "needs_confirmation":
        payload = json.dumps({"status": status, "message": NEEDS_CONFIRMATION})
    else:
        payload = json.dumps({"status": status, "problem": outcome.problem}, ensure_ascii=False)
    return ToolCallRecord(name, arguments, status, len(payload), outcome.problem), payload, None


def _reply_message(reply: Any) -> Any:
    try:
        return reply.choices[0].message
    except (AttributeError, IndexError, KeyError, TypeError):
        return None


def _assistant_turn(message: Any, calls: list[Any], turn: int) -> dict[str, Any]:
    wire = []
    for i, call in enumerate(calls):
        call_id = getattr(call, "id", None) or f"call_{turn}_{i}"
        wire.append({"id": call_id, "type": "function",
                     "function": {"name": call.function.name,
                                  "arguments": call.function.arguments or "{}"}})
    return {"role": "assistant", "content": getattr(message, "content", None) or None,
            "tool_calls": wire}


def run_baseline(client: Any, model: str, question: str, world: World, *,
                 max_turns: int = DEFAULT_MAX_TURNS, temperature: float | None = 0.0,
                 timeout: float = DEFAULT_TIMEOUT) -> BaselineRun:
    """At most ``max_turns`` model calls; the last one is offered no tools, so it must answer.
    ``temperature=None`` leaves it to the endpoint's default."""
    started = time.perf_counter()
    tools = function_specs(world)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": BASELINE_SYSTEM.format(today=world.today.isoformat())},
        {"role": "user", "content": question}]
    run = BaselineRun(answer="", turns=0, stop="answer")
    for turn in range(1, max(1, max_turns) + 1):
        final = turn == max(1, max_turns)
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "timeout": timeout}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if not final:
            kwargs["tools"] = tools
        run.turns = turn
        try:
            reply = client.chat.completions.create(**kwargs)
        except Exception as exc:  # recorded, never raised: one failed item must not stop a run
            run.fail(type(exc).__name__, http_status_of(exc))
            break
        message = _reply_message(reply)
        calls = list(getattr(message, "tool_calls", None) or [])
        content = getattr(message, "content", None)
        text = content.strip() if isinstance(content, str) else ""
        if message is None or not (calls or text):
            run.fail("UnparseableReply")
            break
        if not calls or final:
            run.answer = text
            run.stop = "max_turns" if calls else "answer"
            break
        assistant = _assistant_turn(message, calls, turn)
        messages.append(assistant)
        for call, wire in zip(calls, assistant["tool_calls"], strict=True):
            record, payload, result = execute_call(world, call.function.name,
                                                   call.function.arguments)
            run.calls.append(record)
            run.payloads.append(payload)
            if result is not None:
                run.results.append((call.function.name, result))
            messages.append({"role": "tool", "tool_call_id": wire["id"], "content": payload})
    run.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return run
