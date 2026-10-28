"""The query graph: ingress -> router -> {clarify | execute | open_task | chat} -> narrate
-> respond -> record.

    ingress --error--> respond
    router  --clarify--> clarify -------------------------------> respond
            --tool/workflow--> execute --ok--> narrate --> respond
                                       --else (grant, blocked, invalid, error)--> respond
            --open_task--> open_task --needs confirmation--> respond
                                     --else--> narrate
            --chat--> chat --> narrate
    respond --> record --> END

``router`` includes the scope guard (core.routing.scope_guard): a target that cannot apply a
constraint the question names becomes a ``clarify`` decision whose choices ``clarify`` renders;
an acknowledged unfiltered run reaches ``execute`` and its result's provenance says so.

Nodes are pure with respect to graph state: each reads the state and returns a typed update.
Their only side channel is the trace sink (``ctx.emit``): every step is appended to the
node's ``trace`` update, forwarded to the caller's own sink, and pushed to the LangGraph
custom stream so ``stream_query`` can relay it live. Request-scoped dependencies travel in the
LangGraph runtime context (``RunContext``), never in state.

Two nodes have effects, at explicit boundaries: ``execute``/``open_task`` run targets through
the pipeline (which audits them), and ``record`` completes the audit trail. ``open_task`` hands
the run to the engine's explorer (``core.explore``: the built-in loop, or Pi in a worker
process) with the exposed tools only (``explore.exposure``: explorable, and admitted by the
deployment's allowlist), and records them on the step that starts the run; either way each call
goes through the same pipeline, and a call the explorer refuses before the pipeline is audited
as a refusal. The pipeline is
handed the routing steps as ``prior_trace``; ``record`` appends the steps that came after the
last pipeline run (narrate, respond) to that run's record, or, for a query that never reached
the pipeline (clarify, chat), writes one record with the whole trace. Either way the last
record of a query replays its full decision ladder. ``record`` never changes the answer: a
failed append is logged, not raised (the run itself was already audited).

A model call that fails never passes silently: the request's guarded provider records it (the
call, the error class, the HTTP status), ``narrate`` names the failure its template text
stands in for (``model_error``), ``respond`` lists every one (``model_errors``), and so does
the response.
"""

import dataclasses
import functools
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import JsonValue

from twotrack.providers.base import (
    ChatMessage,
    GuardedProvider,
    Provider,
    Scrubber,
    guarded,
    provider_info,
)

from ..agent_loop import DEFAULT_MAX_TOOL_CALLS, DEFAULT_MAX_TURNS
from ..audit import AuditError, AuditRecorder, AuditSink, AuditStatus
from ..canonical import jsonable, mask_restricted, restricted_field_ids
from ..contract import RouteKind, RouteSource, ToolContext, TraceStatus, TraceStep
from ..explore.calls import RejectedCall, record_rejected_call
from ..explore.explorer import (
    BUILTIN,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    ExploreBudget,
    Explorer,
    ExploreRequest,
)
from ..explore.exposure import exposed
from ..explore.handover import HANDOVER_REFUSED, Refusal, refusal
from ..i18n import as_language, detect_language, language_of, say
from ..narration import labels_for, narrate_result, result_view, template_narration
from ..params import target_title
from ..ports import (
    NumericLock,
    PipelineLike,
    WorkflowRunner,
    audit_ids_of,
    default_lock,
    status_of,
)
from ..registry import Registry
from ..routing import decide_route, mark_unfiltered
from ..workflow import WorkflowInputError, run_workflow
from .state import ClarifyOption, GrantView, GraphState, QueryResponse

log = logging.getLogger("twotrack.graph")

CHAT_SYSTEM = """\
You are the assistant of a business data console. In this conversation you have no access to
the company's data, so never state figures, quantities, prices or dates as facts. Answer
briefly, in the language the user writes in. If the user wants data, point them to one of
these capabilities:
{capabilities}"""

CHAT_UNAVAILABLE = say("chat_unavailable")
DRAFT_STRIPPED = say("draft_stripped")


@dataclass(frozen=True)
class GraphSettings:
    allow_open_task: bool = False
    max_turns: int = DEFAULT_MAX_TURNS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_query_chars: int = 2000
    explore_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    explore_tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    explore_startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
    # the deployment's allowlist of exposed targets; None: every explorable one but external
    # targets (core.explore.exposure)
    explore_tools: tuple[str, ...] | None = None

    @property
    def explore_budget(self) -> ExploreBudget:
        return ExploreBudget(max_turns=self.max_turns, max_tool_calls=self.max_tool_calls,
                             timeout_seconds=self.explore_timeout_seconds,
                             tool_timeout_seconds=self.explore_tool_timeout_seconds,
                             startup_timeout_seconds=self.explore_startup_timeout_seconds)


@dataclass(frozen=True)
class Engine:
    """Application-scoped dependencies. Build once; reuse for every request."""

    registry: Registry
    provider: Provider
    pipeline: PipelineLike
    scrubber: Scrubber | None = None  # egress guard; None = pass-through
    lock: NumericLock | None = None  # numeric lock; None = core.guards.numeric_lock
    settings: GraphSettings = GraphSettings()
    audit: AuditSink | None = None  # completes the trail (see ``record``); None = no trail
    explorer: Explorer | None = None  # runs ``open_task``; None = the built-in loop


@dataclass(frozen=True)
class RunContext:
    """Request-scoped LangGraph runtime context."""

    engine: Engine
    ctx: ToolContext
    provider: GuardedProvider
    lock: NumericLock

    @property
    def language(self) -> str:
        return language_of(self.ctx.facts)

    def workflow_runner(self, narrations: list | None = None,
                        input_errors: list | None = None) -> WorkflowRunner:
        """A runner bound to this request's guarded provider and lock. Narrations and input
        errors are captured so the graph sees them even though the pipeline wraps the call."""
        def run(ctx: ToolContext, workflow: Any, params: Any):
            try:
                return run_workflow(ctx, workflow, params, self.provider, lock=self.lock,
                                    on_narration=None if narrations is None
                                    else narrations.append)
            except WorkflowInputError as exc:
                if input_errors is not None:
                    input_errors.append(exc)
                raise
        return run


def _recording(runtime: Runtime[RunContext]) -> tuple[ToolContext, list[TraceStep]]:
    """A ctx whose sink records this node's steps (deduped by identity), forwards them to the
    caller's sink and streams them live."""
    steps: list[TraceStep] = []
    base = runtime.context.ctx
    writer = runtime.stream_writer

    def sink(step: TraceStep) -> None:
        if any(s is step for s in steps):
            return
        steps.append(step)
        base.trace(step)
        writer(step)

    return dataclasses.replace(base, trace=sink), steps


@dataclass
class _Ledger:
    """What a node's pipeline runs left in the audit log: record ids, and how many leading steps
    of the graph trace the last record holds (its prior trace plus its own steps)."""

    before: list[TraceStep]  # graph trace when the node started
    steps: list[TraceStep]  # the node's own steps, filled live by its recording sink
    audit_ids: list[int] = field(default_factory=list)
    mark: int = 0

    def update(self) -> dict[str, Any]:
        return {"audit_ids": self.audit_ids, "audit_mark": self.mark} if self.audit_ids else {}


@dataclass
class _LedgerPipeline:
    """The engine's pipeline as one node uses it: every run gets the graph trace so far as its
    ``prior_trace`` and reports its audit ids to the ledger."""

    inner: PipelineLike
    ledger: _Ledger

    def run(self, ctx: ToolContext, target: str, params: Any, **kwargs: Any) -> Any:
        prior = [*self.ledger.before, *self.ledger.steps]
        outcome = self.inner.run(ctx, target, params, prior_trace=prior, **kwargs)
        ids = audit_ids_of(outcome)
        if ids:
            self.ledger.audit_ids.extend(ids)
            # the run's steps reached the node's sink live, so they are in ``steps`` already
            self.ledger.mark = len(self.ledger.before) + len(self.ledger.steps)
        return outcome


@dataclass
class _LedgerRejections:
    """Calls an explorer refused before the pipeline (an unknown tool, arguments that fail the
    schema, a spent budget), recorded like a pipeline refusal: one audit record holding the
    graph trace so far, reported to the ledger. Without an audit sink there is no record."""

    engine: Engine
    ledger: _Ledger

    def __call__(self, ctx: ToolContext, call: RejectedCall) -> list[int]:
        sink = self.engine.audit
        if sink is None:
            return []
        entry_id = record_rejected_call(sink, self.engine.registry, ctx, call,
                                        trace=[*self.ledger.before, *self.ledger.steps])
        self.ledger.audit_ids.append(entry_id)
        self.ledger.mark = len(self.ledger.before) + len(self.ledger.steps)
        return [entry_id]


# ---------------------------------------------------------------- nodes


def ingress(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    query = state.query.strip()
    limit = runtime.context.engine.settings.max_query_chars
    language = runtime.context.language
    if not query and state.explicit_target is None:
        return {"query": query, "error": say("query_empty", language)}
    if len(query) > limit:
        return {"query": query, "error": say("query_too_long", language, limit=limit)}
    return {"query": query}


def router(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx, steps = _recording(runtime)
    engine = runtime.context.engine
    decision = decide_route(
        state.query, ctx, engine.registry, runtime.context.provider,
        explicit_target=state.explicit_target, explicit_params=state.explicit_params,
        allow_open_task=engine.settings.allow_open_task,
    )
    return {"decision": decision, "trace": steps}


def clarify(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Offer the decision's options. A scope clarification (the routed target cannot apply a
    constraint the question names) explains the gap and offers its own choices, each with the
    explicit params to send."""
    ctx, steps = _recording(runtime)
    registry = runtime.context.engine.registry
    language = runtime.context.language
    decision = state.decision
    scope = decision.scope
    options = []
    if scope is not None and scope.choices:
        for choice in scope.choices:
            target = registry.target(choice.target)
            options.append(ClarifyOption(name=choice.target,
                                         description=target.description_in(language),
                                         effect=str(target.effect),
                                         label=choice.label or target_title(target, language),
                                         explicit_params=choice.params,
                                         unfiltered=choice.unfiltered))
    else:
        for name in decision.clarify_options:
            target = registry.target(name)
            options.append(ClarifyOption(name=name, description=target.description_in(language),
                                         effect=str(target.effect),
                                         label=target_title(target, language)))
    if not options:
        answer = decision.reason
    else:
        ctx.emit(TraceStep(step="gate", status=TraceStatus.SKIPPED, source=decision.source,
                           facts={"clarify": [o.name for o in options]}))
        answer = decision.reason if scope is not None and scope.choices else say(
            "confirm_one" if len(options) == 1 else "confirm_many", language)
    return {"answer": answer, "answer_source": "system", "clarify_options": options,
            "trace": steps}


def execute(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx, steps = _recording(runtime)
    ledger = _Ledger(before=list(state.trace), steps=steps)
    language = runtime.context.language
    narrations: list = []
    input_errors: list[WorkflowInputError] = []
    decision = state.decision
    try:
        outcome = _LedgerPipeline(runtime.context.engine.pipeline, ledger).run(
            ctx, decision.target, dict(decision.params), grant_token=state.grant_token,
            workflow_runner=runtime.context.workflow_runner(narrations, input_errors),
            route_source=decision.source,
        )
    except WorkflowInputError as exc:
        input_errors.append(exc)
        outcome = None
    except Exception as exc:
        ctx.emit(TraceStep(step="execute", status=TraceStatus.ERROR,
                           facts={"target": decision.target, "error": type(exc).__name__}))
        return {"error": say("run_failed", language), "trace": steps, **ledger.update()}
    for step in outcome.trace if outcome is not None else ():
        ctx.emit(step)
    done = {"trace": steps, **ledger.update()}
    if input_errors:  # a first llm step could not produce valid input: ask, do not fail
        ctx.emit(TraceStep(step="execute", status=TraceStatus.BLOCKED,
                           facts={"target": decision.target, "needs_input": True}))
        return {"answer": str(input_errors[-1]), **done}
    status = status_of(outcome)
    if status == "ok" and outcome.result is not None:
        # an acknowledged run without a filter the question named says so in its scope
        result = mark_unfiltered(outcome.result, decision.scope, language)
        return {"result": result, "narration": narrations[-1] if narrations else None,
                **done}
    if status == "needs_confirmation":
        grant = GrantView.from_grant(outcome.grant)
        summary = grant.summary if grant else decision.target
        return {"grant": grant, "answer": say("needs_confirmation", language, summary=summary),
                **done}
    problem = outcome.problem or say("could_not_run", language, status=status)
    if status == "error":
        return {"error": problem, **done}
    return {"answer": problem, **done}


def _refuse_handover(state: GraphState, rc: RunContext, ctx: ToolContext, ledger: _Ledger,
                     runtime_name: str, refused: Refusal) -> dict[str, Any]:
    """The explore node's guard said no: nothing runs. The refusal is traced (an ``execute``
    ERROR step with stop reason ``handover_refused``), audited (status error, route source
    ``agent``) and stated in the answer."""
    ctx.emit(TraceStep(step="execute", status=TraceStatus.ERROR, source=RouteSource.AGENT, facts={
        "loop": "refused", "runtime": runtime_name, "stop_reason": HANDOVER_REFUSED,
        "error": "HandoverRefused", "refused": refused.reason, "outcome": refused.outcome}))
    sink = rc.engine.audit
    if sink is not None:
        record = AuditRecorder(sink, ctx.clock, user_id=ctx.principal.user_id,
                               roles=sorted(r.value for r in ctx.principal.roles), target=None,
                               effect=None, route_source=RouteSource.AGENT.value)
        request_id = ctx.facts.get("request_id")
        problem = f"{HANDOVER_REFUSED}: {refused.reason}" + (
            f" ({refused.outcome})" if refused.outcome else "")
        try:
            ledger.audit_ids.append(record(
                AuditStatus.ERROR, problem=problem, trace=[*ledger.before, *ledger.steps],
                params={}, request_id=request_id if isinstance(request_id, str) else None))
            ledger.mark = len(ledger.before) + len(ledger.steps)
        except AuditError as exc:  # nothing ran; the gap must still be visible
            log.warning("audit: a refused hand-over could not be recorded (%s)",
                        type(exc).__name__)
    update: dict[str, Any] = {"trace": ledger.steps, "draft_source": "template", "allowed": [],
                              "draft": say(f"explore_refused_{refused.reason}", rc.language)}
    if ledger.audit_ids:
        update.update(audit_ids=[*state.audit_ids, *ledger.audit_ids], audit_mark=ledger.mark)
    return update


def open_task(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx, steps = _recording(runtime)
    ledger = _Ledger(before=list(state.trace), steps=steps)
    rc = runtime.context
    explorer = rc.engine.explorer or BUILTIN
    # the hard guard: exploration runs only for a request handed over by the router, with no
    # explicit target and no earlier outcome (core.explore.handover)
    refused = refusal(explicit_target=state.explicit_target, trace=state.trace,
                      error=state.error, grant=state.grant, result=state.result,
                      audit_ids=state.audit_ids)
    if refused is not None:
        return _refuse_handover(state, rc, ctx, ledger, explorer.name, refused)
    tools = exposed(rc.engine.registry.candidates(ctx), rc.engine.settings.explore_tools)
    ctx.emit(TraceStep(step="execute", status=TraceStatus.OK, facts={
        "loop": "start", "runtime": explorer.name, "exposed": sorted(tools.names())}))
    outcome = explorer.explore(ExploreRequest(
        query=state.query, ctx=ctx, candidates=tools,
        provider=rc.provider, pipeline=_LedgerPipeline(rc.engine.pipeline, ledger),
        workflow_runner=rc.workflow_runner(), budget=rc.engine.settings.explore_budget,
        reject=_LedgerRejections(rc.engine, ledger),
    ))
    update: dict[str, Any] = {"trace": steps, **ledger.update(),
                              "result": outcome.results[-1] if outcome.results else None}
    if outcome.status == "needs_confirmation":
        update.update(grant=GrantView.from_grant(outcome.grant), answer=outcome.text)
        return update
    update.update(draft=outcome.text, draft_source=outcome.text_source,
                  allowed=sorted(outcome.allowed))
    return update


def chat(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx, steps = _recording(runtime)
    candidates = runtime.context.engine.registry.candidates(ctx)
    listing = "\n".join(f"- {candidates.get(n).description}" for n in sorted(candidates.names()))
    messages = [ChatMessage(role="system",
                            content=CHAT_SYSTEM.format(capabilities=listing or "- (none)")),
                ChatMessage(role="user", content=state.query)]
    try:
        text = runtime.context.provider.chat(messages)
        return {"draft": text, "draft_source": "model", "allowed": [], "trace": steps}
    except Exception:
        return {"draft": say("chat_unavailable", runtime.context.language),
                "draft_source": "template", "trace": steps}


def narrate(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx, steps = _recording(runtime)
    rc = runtime.context
    language = rc.language
    result = state.result
    labels = labels_for(rc.engine.registry, result.provenance.tool if result else None)
    stripped = 0
    if state.draft is not None and state.draft_source == "model":
        locked = rc.lock(state.draft, set(state.allowed))
        stripped = locked.stripped_count
        text, source = locked.text, "model"
        if not text.strip():
            text = (template_narration(result_view(result, language=language, labels=labels))
                    if result else say("draft_stripped", language))
            source = "template"
    elif state.draft is not None:
        text, source = state.draft, "template"
    else:
        narration = state.narration or narrate_result(rc.provider, state.query, result,
                                                      rc.lock, language=language, labels=labels)
        text, source, stripped = narration.text, narration.source, narration.stripped
    ctx.emit(TraceStep(step="narrate", status=TraceStatus.OK,
                       facts=narrate_facts(rc.provider, source, stripped, language)))
    return {"answer": text, "answer_source": source, "narrative_stripped": stripped,
            "trace": steps}


def narrate_facts(provider: GuardedProvider, source: str, stripped: int,
                  language: str) -> dict[str, JsonValue]:
    """The ``narrate`` step's facts: which provider stood behind a "model" source (the zero-key
    mock writes template text), and, when template text stands in for a model call that
    failed, that failure (``model_error``), so "no model" and "a failing model" read apart."""
    info = provider_info(provider)
    facts: dict[str, JsonValue] = {"source": source, "stripped": stripped, "language": language,
                                   "provider": info.name, "mock": info.mock}
    failure = provider.text_failure() if source == "template" else None
    if failure is not None:
        facts["model_error"] = failure.model_dump(mode="json")
    return facts


def respond_facts(provider: GuardedProvider, answer_source: str,
                  rows: int) -> dict[str, JsonValue]:
    """The ``respond`` step's facts; ``model_errors`` lists every failed model call of the
    request (routing included), only when there was one."""
    facts: dict[str, JsonValue] = {"answer_source": answer_source, "rows": rows,
                                   "redacted": provider.redacted_count}
    if provider.failures:
        facts["model_errors"] = [f.model_dump(mode="json") for f in provider.failures]
    return facts


def respond(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx, steps = _recording(runtime)
    result = state.result
    facts = respond_facts(runtime.context.provider, state.answer_source,
                          len(result.rows) if result else 0)
    ctx.emit(TraceStep(step="respond",
                       status=TraceStatus.ERROR if state.error else TraceStatus.OK, facts=facts))
    response = QueryResponse(
        query=state.query, language=runtime.context.language, decision=state.decision,
        answer=state.answer, answer_source=state.answer_source,
        columns=list(result.columns) if result else [],
        rows=list(result.rows) if result else [],
        aggregates=dict(result.aggregates) if result else {},
        provenance=result.provenance if result else None,
        narrative_stripped=state.narrative_stripped, grant=state.grant,
        clarify_options=state.clarify_options, trace=[*state.trace, *steps], error=state.error,
        audit_ids=list(state.audit_ids), model_errors=list(runtime.context.provider.failures),
    )
    return {"response": response, "trace": steps}


def _decision_outcome(state: GraphState) -> tuple[AuditStatus, str | None, str | None]:
    """(status, target, problem) of a query whose pipeline wrote no record."""
    decision = state.decision
    if decision.kind is RouteKind.CLARIFY:
        if decision.clarify_options:
            return AuditStatus.CLARIFY, decision.target, None
        # an explicit target outside the candidate set: blocked before the pipeline
        return AuditStatus.BLOCKED, state.explicit_target or decision.target, decision.reason
    if decision.kind in (RouteKind.CHAT, RouteKind.OPEN_TASK):
        if state.error:
            return AuditStatus.ERROR, None, state.error
        return AuditStatus.CHAT, None, None
    # a tool or workflow whose pipeline raised, or a pipeline that does not audit
    if state.error:
        return AuditStatus.ERROR, decision.target, state.error
    if state.grant is not None:
        return AuditStatus.NEEDS_CONFIRMATION, decision.target, None
    if state.result is not None:
        return AuditStatus.OK, decision.target, None
    return AuditStatus.INVALID, decision.target, state.answer or None


def _record_decision(state: GraphState, rc: RunContext, sink: AuditSink) -> int | None:
    ctx, registry, decision = rc.ctx, rc.engine.registry, state.decision
    status, target, problem = _decision_outcome(state)
    effect = None
    if target is not None and (target in registry.tools or target in registry.workflows):
        effect = registry.target(target).effect.value
    record = AuditRecorder(sink, ctx.clock, user_id=ctx.principal.user_id,
                           roles=sorted(r.value for r in ctx.principal.roles), target=target,
                           effect=effect, route_source=decision.source.value)
    request_id = ctx.facts.get("request_id")
    try:
        return record(status, problem=problem, trace=list(state.trace),
                      params=mask_restricted(jsonable(dict(decision.params)),
                                             restricted_field_ids(registry)),
                      request_id=request_id if isinstance(request_id, str) else None)
    except AuditError as exc:
        # nothing was read or changed, so the answer stands; the gap must still be visible
        log.warning("audit: the %s decision of a query could not be recorded (%s)",
                    decision.kind.value, type(exc).__name__)
        return None


def record(state: GraphState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Complete the audit trail of this query (see the module docstring). Emits no step."""
    rc = runtime.context
    sink = rc.engine.audit
    if sink is None or state.response is None:
        return {}
    ids = list(state.audit_ids)
    if ids:
        tail = state.trace[state.audit_mark:]
        try:
            sink.append_trace(ids[-1], tail)
        except Exception as exc:  # the run is audited; only its replay tail is missing
            log.warning("audit: %d trace step(s) could not be attached to record %s (%s)",
                        len(tail), ids[-1], type(exc).__name__)
        return {}
    if state.decision is None:  # rejected at ingress: no routing decision to record
        return {}
    entry_id = _record_decision(state, rc, sink)
    if entry_id is None:
        return {}
    return {"audit_ids": [entry_id],
            "response": state.response.model_copy(update={"audit_ids": [entry_id]})}


# ---------------------------------------------------------------- edges and assembly

_BRANCH = {RouteKind.TOOL: "execute", RouteKind.WORKFLOW: "execute",
           RouteKind.CLARIFY: "clarify", RouteKind.OPEN_TASK: "open_task",
           RouteKind.CHAT: "chat"}


def _after_ingress(state: GraphState) -> str:
    return "respond" if state.error else "router"


def _after_router(state: GraphState) -> str:
    return _BRANCH[state.decision.kind]


def _after_execute(state: GraphState) -> str:
    return "narrate" if state.result is not None else "respond"


def _after_open_task(state: GraphState) -> str:
    return "respond" if state.grant is not None else "narrate"


NODES = {"ingress": ingress, "router": router, "clarify": clarify, "execute": execute,
         "open_task": open_task, "chat": chat, "narrate": narrate, "respond": respond,
         "record": record}


def build_graph():
    g = StateGraph(GraphState, context_schema=RunContext)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_edge(START, "ingress")
    # Branch maps are spelled out so a missing key fails at compile time, not on a rare path.
    g.add_conditional_edges("ingress", _after_ingress, {"router": "router", "respond": "respond"})
    g.add_conditional_edges("router", _after_router, {v: v for v in set(_BRANCH.values())})
    g.add_conditional_edges("execute", _after_execute,
                            {"narrate": "narrate", "respond": "respond"})
    g.add_conditional_edges("open_task", _after_open_task,
                            {"narrate": "narrate", "respond": "respond"})
    g.add_edge("chat", "narrate")
    g.add_edge("clarify", "respond")
    g.add_edge("narrate", "respond")
    g.add_edge("respond", "record")
    g.add_edge("record", END)
    return g.compile()


@functools.cache
def compiled_graph():
    """Compiled once per process; the graph holds no request state."""
    return build_graph()


def _start(engine: Engine, ctx: ToolContext, query: str, explicit_target: str | None,
           explicit_params: dict[str, JsonValue] | None,
           grant_token: str | None) -> tuple[GraphState, RunContext]:
    """Request facts every node and target may read: the query, its reply language (a caller
    may set it; otherwise it is detected from the query) and a request id for the audit."""
    request_id = ctx.facts.get("request_id")
    facts = {**ctx.facts, "query": query,
             "language": as_language(ctx.facts.get("language")) or detect_language(query),
             "request_id": request_id if isinstance(request_id, str) and request_id
             else uuid.uuid4().hex}
    ctx = dataclasses.replace(ctx, facts=facts)
    run = RunContext(engine=engine, ctx=ctx, provider=guarded(engine.provider, engine.scrubber),
                     lock=engine.lock or default_lock())
    state = GraphState(query=query, explicit_target=explicit_target,
                       explicit_params=explicit_params, grant_token=grant_token)
    return state, run


def _response(values: Any) -> QueryResponse:
    state = values if isinstance(values, GraphState) else GraphState.model_validate(values)
    return state.response


def run_query(engine: Engine, ctx: ToolContext, query: str, *,
              explicit_target: str | None = None,
              explicit_params: dict[str, JsonValue] | None = None,
              grant_token: str | None = None) -> QueryResponse:
    state, run = _start(engine, ctx, query, explicit_target, explicit_params, grant_token)
    return _response(compiled_graph().invoke(state, context=run))


def stream_query(engine: Engine, ctx: ToolContext, query: str, *,
                 explicit_target: str | None = None,
                 explicit_params: dict[str, JsonValue] | None = None,
                 grant_token: str | None = None) -> Iterator[TraceStep | QueryResponse]:
    """Yields each TraceStep as it is emitted, then the final QueryResponse."""
    state, run = _start(engine, ctx, query, explicit_target, explicit_params, grant_token)
    last: Any = None
    for mode, chunk in compiled_graph().stream(state, context=run,
                                               stream_mode=["custom", "values"]):
        if mode == "custom" and isinstance(chunk, TraceStep):
            yield chunk
        elif mode == "values":
            last = chunk
    yield _response(last)
