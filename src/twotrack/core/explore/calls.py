"""One exploration tool call, the same for every explorer runtime.

``run_tool_call`` is the gate a call the model makes goes through, whether the built-in loop or
the Pi worker planned it: a call outside the exposed tools or with malformed arguments is refused
before the pipeline; anything else runs through ``pipeline.run`` with route source ``agent``
(lookup, authorization, preconditions, validation, write grants, audit). The model is told only
the fenced narrator view of a result (``narration.fenced_view``), or ``status: problem``. A
problem the pipeline reports goes inside the same kind of fence as a result's scope and message
(``fenced_problem``), since a plugin may word it from stored data (an ``InputProblem`` that lists
the known values); a refusal made before the pipeline is the platform's own wording about the
model's own call, and is sent as it is.

Every refusal is final and on record. One the pipeline makes (denied, invalid, a failed
precondition) is audited by the pipeline. One made before it (``refuse_call``: an unknown or
unexposed tool, malformed arguments, Pi's own schema check, a spent budget) is traced as a
``gate`` BLOCKED step naming the runtime that refused it, and ``record_rejected_call`` writes its
audit record, the same way in both runtimes. A refused call never runs later in the run: the
model may make another call, within the run's budget, and that call meets the same gate.

``run_facts`` is what both runtimes add to the trace step that ends a run: the runtime, its
version, the model, the turns, the stop reason and each call's tool and outcome.
"""

import json
from collections.abc import Callable
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, JsonValue

from twotrack.providers.base import ToolCall, provider_info

from ..audit import AuditRecorder, AuditSink, AuditStatus
from ..canonical import jsonable, mask_restricted, restricted_field_ids
from ..contract import RouteSource, ToolContext, TraceStatus, TraceStep
from ..guards.egress import wrap_untrusted
from ..narration import fenced_view
from ..ports import PipelineLike, WorkflowRunner, audit_ids_of, status_of
from ..registry import Candidates

INVALID_NAME = "invalid"  # how the trace names a tool the model made up or may not see
PROBLEM_FENCE = "problem"  # the label of the fence a problem the pipeline reports goes in

# a refusal before the pipeline -> (status, stage) of its record
REFUSALS = {
    "unknown_tool": ("blocked", "lookup"),
    "invalid_args": ("invalid", "params"),
    "truncated": ("invalid", "params"),
    "max_turns": ("blocked", "budget"),
    "max_tool_calls": ("blocked", "budget"),
}


class CallReport(NamedTuple):
    status: str  # ok | needs_confirmation | blocked | invalid | error
    problem: str | None  # as the trace, the audit and the answer show it: never fenced
    reply: str  # the text the model sees: the fenced view, or "<status>: <told>"
    outcome: Any  # the pipeline's outcome; None when the call never reached the pipeline
    audit_ids: tuple[int, ...] = ()  # the records the call left: the pipeline's or a refusal's

    @property
    def told(self) -> str:
        """What the model is told after ``<status>: `` about a call that returned no result
        (the problem, fenced when the pipeline reported it). The Pi worker writes the
        ``<status>: `` itself, from the result line's ``code``."""
        return self.reply.removeprefix(f"{self.status}: ")


def fenced_problem(problem: str) -> str:
    """A problem the pipeline reported, as the model is told it: inside an untrusted-content
    fence with a fresh nonce (``guards.egress.wrap_untrusted``), as a result's scope and message
    are, since a plugin may word it from stored data."""
    return wrap_untrusted(problem, PROBLEM_FENCE)


def run_tool_call(call: ToolCall, ctx: ToolContext, candidates: Candidates,
                  pipeline: PipelineLike, workflow_runner: WorkflowRunner | None, *,
                  runtime: str, reject: "RejectCall | None" = None) -> CallReport:
    """Run one model-requested call through the gate. The pipeline's steps are emitted to
    ``ctx`` as they come. A call refused before the pipeline is traced and recorded through
    ``reject`` (``refuse_call``), which raises ``AuditError`` when the record cannot be written:
    the runtime then stops the run (fail closed). Nothing else here raises."""
    if call.malformed:
        problem = "arguments must be a JSON object"
        ids = refuse_call(ctx, candidates, reject, name=call.name, args=None,
                          reason="invalid_args", problem=problem, runtime=runtime)
        return CallReport("invalid", problem, f"invalid: {problem}", None, tuple(ids))
    if call.name not in candidates:
        problem = refusal_problem("unknown_tool", call.name)
        ids = refuse_call(ctx, candidates, reject, name=call.name, args=dict(call.arguments),
                          reason="unknown_tool", problem=problem, runtime=runtime)
        return CallReport("blocked", problem, f"blocked: {problem}", None, tuple(ids))
    try:
        outcome = pipeline.run(ctx, call.name, dict(call.arguments),
                               workflow_runner=workflow_runner, route_source=RouteSource.AGENT)
    except Exception as exc:  # the pipeline reports failures itself; this is a last guard
        problem = f"execution failed ({type(exc).__name__})"
        return CallReport("error", problem, f"error: {problem}", None)
    for traced in outcome.trace:
        ctx.emit(traced)
    status = status_of(outcome)
    ids = tuple(audit_ids_of(outcome))
    if status == "ok" and outcome.result is not None:
        return CallReport(status, None, json.dumps(fenced_view(outcome.result.narrator_view())),
                          outcome, ids)
    if status == "ok":
        status = "error"
    problem = outcome.problem or "no details"
    return CallReport(status, problem, f"{status}: {fenced_problem(problem)}", outcome, ids)


def traced_name(name: str, candidates: Candidates) -> str:
    """A tool name as the trace may show it: a raw string the model made up never gets there."""
    return name if name in candidates else INVALID_NAME


# ---------------------------------------------------------------- refused before the pipeline


class RejectedCall(BaseModel):
    """A call an explorer refused before the pipeline. ``name`` and ``args`` are the model's;
    the record keeps the name only when it is a registry target and masks restricted args."""

    model_config = ConfigDict(frozen=True)

    name: str
    args: dict[str, JsonValue] | None = None
    status: Literal["blocked", "invalid"]
    stage: Literal["lookup", "params", "budget"]
    reason: str  # unknown_tool | invalid_args | truncated | max_turns | max_tool_calls
    problem: str


# Records a refused call (``record_rejected_call`` behind the node's audit ledger) and returns the
# ids it wrote. Raises ``AuditError``: the runtime then stops the run (fail closed).
RejectCall = Callable[[ToolContext, RejectedCall], list[int]]


def refusal_problem(reason: str, name: str, detail: str | None = None) -> str:
    """What a refusal before the pipeline tells the model and the audit log."""
    if reason == "unknown_tool":
        return f"'{name[:64]}' is not an available tool"
    if reason == "invalid_args":
        return (detail or "").strip() or "the arguments do not match the tool's schema"
    if reason == "truncated":
        return "the model's output was cut off before the call was complete"
    return f"budget: {reason.replace('_', ' ')} reached"


def refuse_call(ctx: ToolContext, candidates: Candidates, reject: RejectCall | None, *,
                name: str, args: Any, reason: str, problem: str, runtime: str,
                emit: Callable[[TraceStep], None] | None = None) -> list[int]:
    """Trace (a ``gate`` BLOCKED step) and record a call refused before the pipeline; returns
    the audit ids written. ``reason`` is a key of ``REFUSALS``. Raises ``AuditError``."""
    status, stage = REFUSALS[reason]
    (emit or ctx.emit)(TraceStep(step="gate", status=TraceStatus.BLOCKED, facts={
        "stage": stage, "target": traced_name(name, candidates), "reason": reason,
        "blocked_by": runtime}))
    if reject is None:
        return []
    return reject(ctx, RejectedCall(name=name, args=args if isinstance(args, dict) else None,
                                    status=status, stage=stage, reason=reason, problem=problem))


def record_rejected_call(sink: AuditSink, registry: Any, ctx: ToolContext, call: RejectedCall,
                         *, trace: list[TraceStep]) -> int:
    """Write the refusal's audit record (route source ``agent``). Raises ``AuditError``."""
    known = call.name in registry.tools or call.name in registry.workflows
    target = registry.target(call.name) if known else None
    params = mask_restricted(jsonable(dict(call.args or {})), restricted_field_ids(registry))
    request_id = ctx.facts.get("request_id")
    record = AuditRecorder(sink, ctx.clock, user_id=ctx.principal.user_id,
                           roles=sorted(r.value for r in ctx.principal.roles),
                           target=call.name if known else None,
                           effect=target.effect.value if target is not None else None,
                           route_source=RouteSource.AGENT.value)
    return record(AuditStatus(call.status), problem=call.problem, trace=list(trace),
                  params=params if isinstance(params, dict) else {},
                  request_id=request_id if isinstance(request_id, str) else None)


# ---------------------------------------------------------------- trace facts


def model_label(provider: Any) -> str:
    """The model behind a provider, for the trace: its model id, else ``mock`` for the zero-key
    mock, else the provider's name."""
    info = provider_info(provider)
    return info.model or ("mock" if info.mock else info.name)


def run_facts(*, runtime: str, version: dict[str, JsonValue], model: str, turns: int,
              stop_reason: str, calls: list[Any], candidates: Candidates) -> dict[str, JsonValue]:
    """Facts of the step that ends a run. ``calls`` are ``AgentCall``s, in call order."""
    return {"runtime": runtime, "runtime_version": version, "model": model, "turns": turns,
            "stop_reason": stop_reason,
            "calls": [{"tool": traced_name(c.name, candidates), "outcome": c.status}
                      for c in calls]}
