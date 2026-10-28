"""The hand-over from the fixed path to exploration, and the guard that holds it to its terms.

Exploration (the ``open_task`` node) is the route of last resort: it runs only when no fixed
flow covers the request. Two halves keep that explicit:

* ``handover_step`` - the router emits a ``handover`` step whenever it routes to ``open_task``,
  with reason ``intent_not_covered`` and its evidence: what each routing arm said (both silent),
  the best semantic score against its threshold, and how many candidates the user has;
* ``refusal`` - the explore node's hard guard, run before any runtime starts. It refuses (stop
  reason ``handover_refused``) a request that names an explicit target, that already reached a
  terminal outcome on the fixed path (denied, invalid, blocked, error, a spent budget, a pending
  approval, a result), or that was never handed over. Today no graph edge leads anywhere but the
  router into ``open_task``; the guard keeps a future edge from turning a refusal into a second
  try by a model.
"""

from collections.abc import Sequence
from typing import Any, NamedTuple

from pydantic import JsonValue

from ..contract import RouteSource, TraceStatus, TraceStep

HANDOVER_STEP = "handover"
INTENT_NOT_COVERED = "intent_not_covered"
HANDOVER_REFUSED = "handover_refused"  # the explore node's stop reason when the guard refuses

_BUDGETS = ("max_turns", "max_tool_calls")


def handover_step(*, rule_target: str | None, llm_target: str | None, semantic_best: str | None,
                  semantic_score: float, threshold: float, candidates: int) -> TraceStep:
    """The router's ``handover`` step. ``*_target`` are the arms' picks (None: silent)."""
    facts: dict[str, JsonValue] = {
        "reason": INTENT_NOT_COVERED, "to": "open_task",
        "evidence": {"rule": rule_target, "llm": llm_target,
                     "arms_silent": rule_target is None and llm_target is None,
                     "semantic": {"best": semantic_best, "score": round(semantic_score, 3),
                                  "threshold": threshold,
                                  "below": semantic_score < threshold},
                     "candidates": candidates}}
    return TraceStep(step=HANDOVER_STEP, status=TraceStatus.OK, source=RouteSource.AGENT,
                     facts=facts)


class Refusal(NamedTuple):
    reason: str  # explicit_target | earlier_outcome | no_handover
    outcome: str | None = None  # the earlier outcome: denied, invalid, blocked, error, ...


def _outcome_of(step: TraceStep) -> str | None:
    """The terminal outcome a fixed-path step records, if it records one."""
    if step.status not in (TraceStatus.BLOCKED, TraceStatus.ERROR):
        return None
    facts = step.facts
    if step.status is TraceStatus.ERROR and step.step in ("gate", "execute"):
        return "error"
    if step.step == "gate":
        stage = facts.get("stage")
        if stage == "authz":
            return "denied"
        if stage in ("params", "lookup"):
            return "invalid"
        return "budget" if stage == "budget" else "blocked"
    if step.step == "execute":
        if facts.get("reason") in _BUDGETS:
            return "budget"
        return "invalid" if facts.get("needs_input") else "blocked"
    return None


def earlier_outcome(trace: Sequence[TraceStep], *, error: str | None = None, grant: Any = None,
                    result: Any = None, audit_ids: Sequence[int] = ()) -> str | None:
    """The first terminal outcome this request already reached, or None."""
    for step in trace:
        if (outcome := _outcome_of(step)) is not None:
            return outcome
    if error:
        return "error"
    if grant is not None:
        return "needs_confirmation"
    if result is not None:
        return "answered"
    if audit_ids:
        return "executed"
    return None


def handed_over(trace: Sequence[TraceStep]) -> bool:
    return any(s.step == HANDOVER_STEP and s.status is TraceStatus.OK
               and s.facts.get("reason") == INTENT_NOT_COVERED for s in trace)


def refusal(*, explicit_target: str | None, trace: Sequence[TraceStep], error: str | None = None,
            grant: Any = None, result: Any = None,
            audit_ids: Sequence[int] = ()) -> Refusal | None:
    """Why the explore node must not run for this request, or None when it may."""
    if explicit_target is not None:
        return Refusal("explicit_target")
    outcome = earlier_outcome(trace, error=error, grant=grant, result=result, audit_ids=audit_ids)
    if outcome is not None:
        return Refusal("earlier_outcome", outcome)
    if not handed_over(trace):
        return Refusal("no_handover")
    return None
