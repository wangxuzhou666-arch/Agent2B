"""The explorer contract: what the ``open_task`` node hands a runtime, and what it gets back.

A runtime takes one ``ExploreRequest`` (the query, the request's context, its candidates, its
guarded provider, the node's pipeline, the budget, and where to record a call refused before the
pipeline) and returns the ``AgentOutcome`` the graph already knows. The node does not know which
runtime planned the run; the trace does (``runtime`` in the facts of the step that ends it).

Budgets are the same for both runtimes (``ExploreBudget``, set by ``TWOTRACK_EXPLORE_*``):
``max_turns`` model turns, ``max_tool_calls`` calls the model asks for (refused ones included),
``timeout_seconds`` of wall clock for the whole run and, for a runtime that waits on the host
for each call (Pi), ``tool_timeout_seconds`` for one answer. A runtime that starts a process
(Pi) has ``startup_timeout_seconds`` for it to come up, apart from the run: the run's wall clock
starts once the worker says hello, so a slow start never eats a short run's budget. A spent
turn or call budget stops the run with that reason; a run that outlasts its wall clock is a
failure, and so is a worker that does not come up in time (``worker_unavailable``). Neither
falls back to another runtime.
"""

from dataclasses import dataclass
from typing import Protocol

from twotrack.providers.base import GuardedProvider, ModelCallError

from ..agent_loop import DEFAULT_MAX_TOOL_CALLS, DEFAULT_MAX_TURNS, AgentOutcome, run_agent_loop
from ..contract import ToolContext
from ..ports import PipelineLike, WorkflowRunner
from ..registry import Candidates
from .calls import RejectCall

DEFAULT_TIMEOUT_SECONDS = 120.0  # wall clock of one exploration run
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0  # one host answer to a worker's tool call
DEFAULT_STARTUP_TIMEOUT_SECONDS = 20.0  # a worker's start-up: launch to hello (Node, Pi)

# A runtime failure recorded with the request's model errors: the class names what failed. The
# call is ``agent_step``, the model-call kind exploration uses (no API change).
TIMEOUT_ERROR = "ExplorerTimeout"
# the built-in loop's own failures (a failed model call is recorded by the provider itself)
RUN_FAILURES = {"timeout": TIMEOUT_ERROR, "audit_failed": "AuditError"}


@dataclass(frozen=True)
class ExploreBudget:
    max_turns: int = DEFAULT_MAX_TURNS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ExploreRequest:
    query: str
    ctx: ToolContext
    candidates: Candidates
    provider: GuardedProvider  # scrubs what leaves for a model and keeps the failed calls
    pipeline: PipelineLike
    workflow_runner: WorkflowRunner | None = None
    budget: ExploreBudget = ExploreBudget()
    reject: RejectCall | None = None  # None: refusals are traced, not recorded


class Explorer(Protocol):
    name: str  # builtin | pi

    def explore(self, request: ExploreRequest) -> AgentOutcome: ...


class BuiltinExplorer:
    """The built-in loop (``core.agent_loop``) behind the explorer contract."""

    name = "builtin"

    def explore(self, request: ExploreRequest) -> AgentOutcome:
        budget = request.budget
        outcome = run_agent_loop(
            request.query, request.ctx, request.candidates, request.provider, request.pipeline,
            workflow_runner=request.workflow_runner, max_turns=budget.max_turns,
            max_tool_calls=budget.max_tool_calls, timeout_seconds=budget.timeout_seconds,
            reject=request.reject)
        error = RUN_FAILURES.get(outcome.stop_reason)
        if error is not None:  # a failure, reported like a failed model call
            request.provider.failures.append(ModelCallError(call="agent_step", error=error))
        return outcome


BUILTIN = BuiltinExplorer()
