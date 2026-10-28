"""Controlled execution: the only way a routed or explicit call runs a tool or workflow.

Stage order (each stage emits a ``gate`` or ``execute`` TraceStep; the first failure stops):

1. lookup        unknown target                         -> invalid
2. authz         role allow-list                        -> blocked
3. precondition  ``requires`` checks                    -> blocked
4. params        Pydantic validation (Tool.params_model), then every bound param's value
                 against its constraint (an unknown category)  -> invalid
5. confirmation  only a target that declares ``approval="required"`` (whatever its effect):
                 no grant -> issue one                  -> needs_confirmation
                 grant for other user/params, expired, reused -> blocked
                 (a grant's summary names each param in plain words, core.params)
6. execute       read/external tools get a read-only store; a write runs inside one store
                 transaction together with its outcome audit record (every target also gets
                 ``ctx.registry``, to name its filters in plain words). A run that consumed a
                 grant records ``started`` first. The result is then checked against the
                 target's declared ``outputs``: an undeclared aggregate key, column or row key
                 is an error (a write rolls back) and nothing of the result is returned.
7. audit         every run leaves a record; a failed audit write fails the run (fail closed)

The grant digest and the audit ``params_digest`` are the same value, computed from the
validated params, so equivalent raw params ("5" vs 5) confirm the same request. A run that
presents a grant token names that grant (its public ``grant_id``) on a refusal too, one that
stops before the grant is consumed included (wrong user, used, expired, another request, a role
or precondition that no longer holds, params that no longer validate), so the grant's records
always show why nothing ran.

Replay: a caller that decided *how* to get here (the engine's routing) passes those steps as
``prior_trace``; every record of the run stores them ahead of its own steps, so the record
alone shows the whole decision. ``ctx.facts["request_id"]`` and ``parent_id`` are stored as
given to link the records of one request (see ``core.audit``).
"""

import dataclasses
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from .audit import AuditError, AuditRecorder, AuditSink, AuditStatus
from .canonical import jsonable, mask_restricted, request_digest, restricted_field_ids
from .contract import (
    Approval,
    Principal,
    ResourceEffect,
    Routable,
    RouteSource,
    Tool,
    ToolContext,
    ToolResult,
    TraceStatus,
    TraceStep,
    Workflow,
)
from .errors import InputProblem, OutputContractError
from .grants import Grant, GrantStore, grant_id_for
from .params import describe_request, registry_title
from .scope import check_bound_values
from .store.base import ReadOnlyStore, Store

WorkflowRunner = Callable[[ToolContext, Workflow, Any], ToolResult]


class PipelineStatus(StrEnum):
    OK = "ok"
    NEEDS_CONFIRMATION = "needs_confirmation"
    BLOCKED = "blocked"
    INVALID = "invalid"
    ERROR = "error"


class PipelineOutcome(BaseModel):
    status: PipelineStatus
    result: ToolResult | None = None
    grant: Grant | None = None
    trace: list[TraceStep] = []
    problem: str | None = None
    audit_ids: list[int] = []


class _Stop(Exception):  # noqa: N818 - internal control flow, never escapes run()
    def __init__(self, status: PipelineStatus, stage: str, problem: str,
                 facts: Mapping[str, Any] | None = None) -> None:
        super().__init__(problem)
        self.status, self.stage, self.problem = status, stage, problem
        self.facts = dict(facts or {})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validation_problem(exc: ValidationError) -> str:
    # loc + message only: Pydantic's ``input`` could echo a restricted value
    parts = [f"{'.'.join(map(str, e['loc'])) or 'params'}: {e['msg']}" for e in exc.errors()]
    return "invalid params: " + "; ".join(parts)


def parse_params(routable: Routable, raw: Mapping[str, Any]) -> tuple[Any, Any]:
    """(the params as the target takes them, their canonical JSON form) for a request.

    A tool parses with its params model; a workflow gets a plain dict, validated by its first
    step's input_model if any. Raises what validation raises."""
    if isinstance(routable, Tool):
        parsed: Any = routable.parse_params(raw)
        return parsed, parsed.model_dump(mode="json")
    first = routable.steps[0] if routable.steps else None
    model = first.input_model if first else None
    parsed = model.model_validate(dict(raw)).model_dump() if model else dict(raw)
    return parsed, jsonable(parsed)


class Pipeline:
    def __init__(self, registry: Any, store: Store, audit: AuditSink, grants: GrantStore, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.registry, self.store, self.audit, self.grants = registry, store, audit, grants
        self.clock = clock or _utc_now
        self.restricted = restricted_field_ids(registry)

    def describe(self, routable: Routable, canonical: Any, language: str | None) -> str:
        """What a confirmation of ``routable`` with these canonical params asks for, in plain
        words of ``language``. Restricted values are masked."""
        shown = mask_restricted(canonical, self.restricted)
        if isinstance(shown, dict):  # an empty restricted param stays empty, not "***"
            shown = {k: None if canonical.get(k) is None else v for k, v in shown.items()}
        text = describe_request(routable, shown if isinstance(shown, dict) else {}, language,
                                getattr(self.registry, "entities", None))
        return text[:500]

    def summarize(self, target: str, params: Mapping[str, Any], language: str | None
                  ) -> str | None:
        """The confirmation text of a request that already waits, worded in ``language``: the
        reader's, which need not be the language the asker used. None when the target or its
        params no longer fit the registry; the caller then keeps the text it has."""
        try:
            routable = self.registry.target(target)
        except KeyError:
            return None
        try:
            _, canonical = parse_params(routable, params)
        except (TypeError, ValueError):
            return None
        return self.describe(routable, canonical, language)

    def run(
        self,
        ctx: ToolContext,
        target: str,
        params: Mapping[str, Any],
        *,
        grant_token: str | None = None,
        workflow_runner: WorkflowRunner | None = None,
        route_source: RouteSource | str | None = None,
        prior_trace: Sequence[TraceStep] = (),
        parent_id: int | None = None,
    ) -> PipelineOutcome:
        """``prior_trace`` is stored ahead of this run's steps in its audit records (not in
        ``PipelineOutcome.trace``, which holds only the steps this run emitted)."""
        run = _Run(self, ctx, target, params, route_source, prior_trace, parent_id)
        return run.go(grant_token, workflow_runner)


class _Run:
    """State of one pipeline run: trace, audit ids, masked params for the record."""

    def __init__(self, pipeline: Pipeline, ctx: ToolContext, target: str,
                 params: Mapping[str, Any], route_source: RouteSource | str | None,
                 prior_trace: Sequence[TraceStep] = (), parent_id: int | None = None) -> None:
        self.p, self.ctx, self.target_name = pipeline, ctx, target
        self.raw_params = params
        self.prior: list[TraceStep] = list(prior_trace)
        self.trace: list[TraceStep] = []
        self.audit_ids: list[int] = []
        self.principal: Principal = ctx.principal
        self.route_source = str(route_source) if route_source is not None else None
        request_id = ctx.facts.get("request_id")
        self.request_id = request_id if isinstance(request_id, str) else None
        self.parent_id = parent_id
        self.canonical: Any = jsonable(dict(params))
        self.grant_id: str | None = None  # the grant this run issued or consumed
        self.presented: str | None = None  # the public id of the grant the caller presented
        self.record = self._recorder(effect=None)

    # ------------------------------------------------------------ plumbing

    def _recorder(self, effect: str | None) -> AuditRecorder:
        return AuditRecorder(self.p.audit, self.p.clock, user_id=self.principal.user_id,
                             roles=sorted(r.value for r in self.principal.roles),
                             target=self.target_name, effect=effect,
                             route_source=self.route_source)

    def emit(self, step: TraceStep) -> None:
        self.trace.append(step)
        self.ctx.emit(step)

    def audit(self, status: AuditStatus, **fields: Any) -> int:
        fields.setdefault("grant_id", self.grant_id)
        entry_id = self.record(
            status, params=mask_restricted(self.canonical, self.p.restricted),
            params_digest=request_digest(self.principal.user_id, self.target_name,
                                         self.canonical),
            trace=[*self.prior, *self.trace], request_id=self.request_id,
            parent_id=self.parent_id, **fields)
        self.audit_ids.append(entry_id)
        return entry_id

    def outcome(self, status: PipelineStatus, **fields: Any) -> PipelineOutcome:
        return PipelineOutcome(status=status, trace=list(self.trace),
                               audit_ids=list(self.audit_ids), **fields)

    def stopped(self, stop: _Stop) -> PipelineOutcome:
        self.emit(TraceStep(step="gate", status=TraceStatus.BLOCKED,
                            facts={"stage": stop.stage, **stop.facts}))
        try:
            # a refusal names the grant the caller presented, though it was never consumed
            self.audit(AuditStatus(stop.status.value), problem=stop.problem,
                       grant_id=self.grant_id or self.presented)
        except AuditError:
            return self.outcome(PipelineStatus.ERROR,
                                problem=f"{stop.problem} (and the audit record failed)")
        return self.outcome(stop.status, problem=stop.problem)

    # ------------------------------------------------------------ stages

    def go(self, grant_token: str | None, runner: WorkflowRunner | None) -> PipelineOutcome:
        started = time.perf_counter()
        self.presented = grant_id_for(grant_token) if grant_token else None
        try:
            target = self.gate(grant_token)
        except _Stop as stop:
            return self.stopped(stop)
        except Exception as exc:  # e.g. the grant store is down: fail closed, still audited
            return self.stopped(_Stop(PipelineStatus.ERROR, "gate",
                                      f"the gate could not complete ({type(exc).__name__})"))
        if isinstance(target, PipelineOutcome):
            return target
        routable, parsed = target
        self.emit(TraceStep(step="gate", status=TraceStatus.OK, facts={
            "target": routable.name, "effect": routable.effect.value,
            "confirmation": "consumed" if self.grant_id else "not_required"},
            duration_ms=(time.perf_counter() - started) * 1000))
        if routable.effect is ResourceEffect.WRITE:
            return self.execute_write(routable, parsed, runner)
        return self.execute_read(routable, parsed, runner)

    def gate(self, grant_token: str | None) -> tuple[Routable, Any] | PipelineOutcome:
        try:
            routable = self.p.registry.target(self.target_name)
        except KeyError:
            raise _Stop(PipelineStatus.INVALID, "lookup",
                        f"unknown target '{self.target_name}'") from None
        self.record = self._recorder(effect=routable.effect.value)
        if not routable.allows(self.principal):
            raise _Stop(PipelineStatus.BLOCKED, "authz",
                        f"your role may not run '{routable.name}'")
        checks_ctx = dataclasses.replace(self.ctx, store=ReadOnlyStore(self.p.store),
                                         trace=self.emit)
        unmet = self.p.registry.unmet(routable, checks_ctx)
        if unmet:
            needs = "; ".join(self.p.registry.preconditions[n].description for n in unmet)
            raise _Stop(PipelineStatus.BLOCKED, "precondition", needs, {"unmet": unmet})
        parsed = self.validate(routable)
        if routable.approval is Approval.REQUIRED:
            if grant_token is None:
                return self.ask_confirmation(routable)
            if not self.p.grants.consume(grant_token, self.principal, routable.name,
                                         self.canonical):
                raise _Stop(PipelineStatus.BLOCKED, "confirmation",
                            "the confirmation is invalid, expired, already used, or was given "
                            "for a different request; confirm again")
            self.grant_id = grant_id_for(grant_token)
        return routable, parsed

    def validate(self, routable: Routable) -> Any:
        try:
            parsed, self.canonical = parse_params(routable, self.raw_params)
            if isinstance(self.canonical, dict):
                check_bound_values(self.p.registry, routable, self.canonical, self.ctx.language)
        except ValidationError as exc:
            raise _Stop(PipelineStatus.INVALID, "params", _validation_problem(exc)) from None
        except InputProblem as exc:  # a value the user can fix; the message lists valid ones
            raise _Stop(PipelineStatus.INVALID, "params", str(exc)) from None
        except (TypeError, ValueError) as exc:
            raise _Stop(PipelineStatus.INVALID, "params", f"invalid params: {exc}") from None
        return parsed

    def ask_confirmation(self, routable: Routable) -> PipelineOutcome:
        summary = self.p.describe(routable, self.canonical, self.ctx.language)
        grant = self.p.grants.issue(self.principal, routable.name, self.canonical, summary)
        self.grant_id = grant.grant_id
        self.emit(TraceStep(step="gate", status=TraceStatus.BLOCKED,
                            facts={"stage": "confirmation", "grant_id": grant.grant_id}))
        try:
            self.audit(AuditStatus.NEEDS_CONFIRMATION)
        except AuditError:
            self.p.grants.reject(grant.token, self.principal)
            return self.outcome(PipelineStatus.ERROR, problem="audit unavailable; nothing ran")
        return self.outcome(PipelineStatus.NEEDS_CONFIRMATION, grant=grant)

    def call(self, routable: Routable, parsed: Any, runner: WorkflowRunner | None,
             store: Any) -> ToolResult:
        ctx = dataclasses.replace(self.ctx, store=store, trace=self.emit,
                                  registry=self.ctx.registry or self.p.registry)
        if isinstance(routable, Tool):
            result = routable.handler(ctx, parsed)
        elif runner is None:
            raise RuntimeError(f"workflow '{routable.name}' needs a workflow_runner")
        else:
            result = runner(ctx, routable, parsed)
        if not isinstance(result, ToolResult):
            raise TypeError(f"'{routable.name}' returned {type(result).__name__}, "
                            "expected ToolResult")
        undeclared = routable.outputs.undeclared(result)
        if undeclared:
            raise OutputContractError(
                f"'{routable.name}' returned output its declaration does not list: "
                + "; ".join(undeclared))
        return self.named(result)

    def named(self, tool_result: ToolResult) -> ToolResult:
        """``tool_result`` with its provenance carrying the target's display name in the
        request language, unless the tool already set one."""
        provenance = tool_result.provenance
        if provenance.label:
            return tool_result
        label = registry_title(self.p.registry, provenance.tool, self.ctx.language)
        return tool_result.model_copy(update={
            "provenance": provenance.model_copy(update={"label": label})})

    def executed(self, result: ToolResult, started: float) -> None:
        self.emit(TraceStep(step="execute", status=TraceStatus.OK, facts={
            "row_count": result.provenance.row_count},
            duration_ms=(time.perf_counter() - started) * 1000))

    def failed(self, exc: BaseException, started: float, rolled_back: bool) -> PipelineOutcome:
        if isinstance(exc, InputProblem):
            # missing user input is fixable by the user: record it as invalid, show the message
            self.emit(TraceStep(step="execute", status=TraceStatus.BLOCKED,
                                facts={"error": type(exc).__name__, "rolled_back": rolled_back},
                                duration_ms=(time.perf_counter() - started) * 1000))
            try:
                self.audit(AuditStatus.INVALID, problem=str(exc)[:300])
            except AuditError:
                pass
            return self.outcome(PipelineStatus.INVALID, problem=str(exc))
        detail = f"{type(exc).__name__}: {exc}"[:300]
        self.emit(TraceStep(step="execute", status=TraceStatus.ERROR,
                            facts={"error": type(exc).__name__, "rolled_back": rolled_back},
                            duration_ms=(time.perf_counter() - started) * 1000))
        shown = "execution failed" + (" and was rolled back" if rolled_back else "")
        try:
            self.audit(AuditStatus.ERROR, problem=detail)
        except AuditError:
            pass  # the run already failed; there is nothing further to withhold
        return self.outcome(PipelineStatus.ERROR, problem=f"{shown} ({type(exc).__name__})")

    def execute_write(self, routable: Routable, parsed: Any,
                      runner: WorkflowRunner | None) -> PipelineOutcome:
        try:
            self.audit(AuditStatus.STARTED)
        except AuditError:
            return self.outcome(PipelineStatus.ERROR,
                                problem="audit unavailable; the write was not executed")
        started = time.perf_counter()
        try:
            with self.p.store.transaction():
                result = self.call(routable, parsed, runner, self.p.store)
                self.executed(result, started)
                # inside the transaction: if this record fails, the data change rolls back
                self.audit(AuditStatus.OK, row_count=result.provenance.row_count)
        except AuditError as exc:
            self.failed(exc, started, rolled_back=True)
            return self.outcome(PipelineStatus.ERROR,
                                problem="audit unavailable; the write was rolled back")
        except Exception as exc:
            return self.failed(exc, started, rolled_back=True)
        return self.outcome(PipelineStatus.OK, result=result)

    def execute_read(self, routable: Routable, parsed: Any,
                     runner: WorkflowRunner | None) -> PipelineOutcome:
        if self.grant_id is not None:  # an approved external call records its start, as a write
            try:
                self.audit(AuditStatus.STARTED)
            except AuditError:
                return self.outcome(PipelineStatus.ERROR,
                                    problem="audit unavailable; the call was not executed")
        started = time.perf_counter()
        try:
            result = self.call(routable, parsed, runner, ReadOnlyStore(self.p.store))
        except Exception as exc:
            return self.failed(exc, started, rolled_back=False)
        self.executed(result, started)
        try:
            self.audit(AuditStatus.OK, row_count=result.provenance.row_count)
        except AuditError:
            return self.outcome(PipelineStatus.ERROR,
                                problem="audit unavailable; the result was withheld")
        return self.outcome(PipelineStatus.OK, result=result)
