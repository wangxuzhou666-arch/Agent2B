"""Structural interfaces the orchestration layer needs from the pipeline and guards.

The graph, workflow runner and agent loop depend on these protocols, not on the concrete
pipeline/guard modules, so either side can be tested with small fakes. The concrete
implementations live in ``core.pipeline`` and ``core.guards`` and are resolved lazily.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from .contract import RouteSource, ToolContext, ToolResult, TraceStep, Workflow

WorkflowRunner = Callable[[ToolContext, Workflow, Any], ToolResult]


class GrantLike(Protocol):
    token: str
    expires_at: datetime
    summary: str


class OutcomeLike(Protocol):
    status: Any  # ok | needs_confirmation | blocked | invalid | error (str or StrEnum)
    result: ToolResult | None
    grant: GrantLike | None
    trace: list[TraceStep]
    problem: str | None
    # optional: ids of the audit records the run wrote (read with getattr, default [])


class PipelineLike(Protocol):
    def run(
        self,
        ctx: ToolContext,
        target: str,
        params: dict[str, Any],
        *,
        grant_token: str | None = None,
        workflow_runner: WorkflowRunner | None = None,
        route_source: RouteSource | str | None = None,
        prior_trace: Sequence[TraceStep] = (),
        parent_id: int | None = None,
    ) -> OutcomeLike: ...


def audit_ids_of(outcome: Any) -> list[int]:
    """Audit record ids an outcome reports (pipelines that do not audit report none)."""
    ids = getattr(outcome, "audit_ids", None) or []
    return [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]


class LockOutcome(Protocol):
    text: str
    stripped_count: int


NumericLock = Callable[[str, set[float]], LockOutcome]


def status_of(outcome: OutcomeLike) -> str:
    status = outcome.status
    return str(getattr(status, "value", status))


def default_lock() -> NumericLock:
    """The production numeric lock (imported lazily so this layer has no hard dependency)."""
    from .guards.numeric_lock import lock_narrative

    return lock_narrative
