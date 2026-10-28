"""twotrack as an MCP server: the registry's tools and workflows, callable by an MCP client.

Identity. The server acts as one twotrack user for its whole life: ``--user <name>`` (an
existing, enabled account; its roles are re-read on every call, so disabling it takes effect
at once) or, in demo mode only, ``--role admin|operator|viewer`` (the built-in demo user of
that role). Without an identity it does not start.

What the client sees. The tool list is the registry's candidate set for that user (role and
preconditions applied), one MCP tool per tool or workflow, with the params model's JSON
schema. A call goes through ``Pipeline.run`` with route source ``mcp`` and is audited like any
other run. The result holds what a narrator would see (aggregates with labels, column names,
row count, provenance) plus a short deterministic summary. It holds no rows: the client is a
language model, and pillar one is that a model never sees row data. ``--rows`` opts in to at
most ``--max-rows`` rows, projected for the user and with every restricted field removed
whatever the user's role. Each record ends with an ``export`` step that says how many rows
left for the client.

Writes. A write tool never writes from here. It stops at the pipeline's confirmation grant;
the request is stored with the console's pending approvals (``/api/approvals``) and the user
approves it in the console. The grant token never leaves the server and there is no approve
tool.

``ask`` (unless a registry target has that name) runs a natural-language question through the
engine, so the console's own routing, narration and numeric lock apply; the same rows policy
holds for its result.

Failed model calls. As in the REST API, a result names every model call that failed while it
was produced (``model_errors``: the call, the error class, the HTTP status; never the message):
a workflow's narration or understand step, and for ``ask`` the routing opinion too. The answer
fell back without them (template narration, rule routing). The audited ``narrate`` step names
the failure its template text stands in for (``model_error``), the ``respond`` step lists them
all. A plain tool makes no model call, so its list is empty.
"""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from twotrack import __version__
from twotrack.core import (
    Approval,
    Principal,
    ResourceEffect,
    Role,
    Routable,
    RouteSource,
    ToolContext,
    TraceStatus,
    TraceStep,
)
from twotrack.core.canonical import jsonable, restricted_field_ids
from twotrack.core.grants import grant_id_for
from twotrack.core.graph import QueryResponse, RunContext, run_query
from twotrack.core.graph.builder import narrate_facts, respond_facts
from twotrack.core.i18n import detect_language
from twotrack.core.narration import (
    Narration,
    aggregate_label,
    humanize_key,
    labels_for,
    result_view,
    template_narration,
)
from twotrack.core.pipeline import PipelineStatus
from twotrack.core.ports import audit_ids_of, default_lock
from twotrack.core.routing.llm_arm import tool_card
from twotrack.core.routing.router import unavailable_reason
from twotrack.providers.base import GuardedProvider, ModelCallError, guarded
from twotrack.runtime import Runtime
from twotrack.server.auth import DEMO_USERS, demo_user_id
from twotrack.server.context import request_context
from twotrack.server.projection import project_result
from twotrack.settings import _flag, _number

log = logging.getLogger("twotrack.mcp")

ASK = "ask"
ROWS_ENV = "TWOTRACK_MCP_ROWS"
MAX_ROWS_ENV = "TWOTRACK_MCP_MAX_ROWS"
DEFAULT_MAX_ROWS = 50
CONSOLE_APPROVALS = "the twotrack console, Approvals"


class IdentityError(ValueError):
    """No usable identity: the server must not start (or a call must not run)."""


# ---------------------------------------------------------------- identity and options


@dataclass(frozen=True)
class Identity:
    user_id: str
    demo: bool = False

    def principal(self, runtime: Runtime) -> Principal:
        """The user's current principal. Raises IdentityError if it is gone or disabled."""
        if self.demo:
            principal = DEMO_USERS.get(self.user_id) if runtime.settings.demo_mode else None
            if principal is None:
                raise IdentityError("demo identities exist only in demo mode")
            return principal
        user = runtime.users.get(self.user_id)
        if user is None or user.disabled:
            raise IdentityError(f"user '{self.user_id}' does not exist or is disabled")
        return user.principal


def resolve_identity(runtime: Runtime, *, user: str | None = None,
                     role: str | None = None) -> Identity:
    """Check the identity once at start. Raises IdentityError with a message for the operator."""
    if user and role:
        raise IdentityError("give --user or --role, not both")
    if role:
        if not runtime.settings.demo_mode:
            raise IdentityError("--role is only accepted in demo mode (TWOTRACK_DEMO_MODE=1); "
                                "outside it use --user <username>")
        try:
            identity = Identity(demo_user_id(Role(role)), demo=True)
        except ValueError:
            raise IdentityError(f"unknown role {role!r}") from None
    elif user:
        identity = Identity(user)
    else:
        raise IdentityError("an identity is required: --user <username> "
                            "(or --role admin|operator|viewer in demo mode)")
    identity.principal(runtime)
    return identity


@dataclass(frozen=True)
class McpOptions:
    include_rows: bool = False  # opt-in: rows reach the client (restricted fields removed)
    max_rows: int = DEFAULT_MAX_ROWS
    ask_tool: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "McpOptions":
        """``TWOTRACK_MCP_ROWS`` (flag) and ``TWOTRACK_MCP_MAX_ROWS`` (at least 1)."""
        return cls(include_rows=_flag(env, ROWS_ENV),
                   max_rows=int(_number(env, MAX_ROWS_ENV, DEFAULT_MAX_ROWS, minimum=1)))


# ---------------------------------------------------------------- results


def _problem(problem: str, status: str) -> dict[str, Any]:
    return {"status": status, "problem": problem}


def _model_errors(failures: list[ModelCallError]) -> list[dict[str, Any]]:
    """The failed model calls as the REST API sends them (``model_errors``)."""
    return [failure.model_dump(mode="json") for failure in failures]


@dataclass(frozen=True)
class _Rows:
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int

    @property
    def sent(self) -> int:
        return len(self.rows)


class TwotrackMcp:
    """Everything the MCP handlers do, synchronously (the handlers run it in a worker thread)."""

    def __init__(self, runtime: Runtime, identity: Identity,
                 options: McpOptions | None = None) -> None:
        self.runtime, self.identity = runtime, identity
        self.options = options or McpOptions()
        self.registry = runtime.registry
        self.restricted = restricted_field_ids(runtime.registry)
        taken = ASK in self.registry.tools or ASK in self.registry.workflows
        if self.options.ask_tool and taken:
            log.warning("mcp: a registry target is named '%s'; the question tool is off", ASK)
        self.ask_enabled = self.options.ask_tool and not taken

    # ------------------------------------------------------------ plumbing

    def _ctx(self, principal: Principal, **facts: Any) -> ToolContext:
        return request_context(self.runtime, principal, channel="mcp", **facts)

    def _run_context(self, ctx: ToolContext) -> RunContext:
        return RunContext(engine=self.runtime.engine, ctx=ctx,
                          provider=guarded(self.runtime.provider, self.runtime.egress),
                          lock=self.runtime.engine.lock or default_lock())

    def _rows_policy(self) -> str:
        if not self.options.include_rows:
            return ("Rows stay in the twotrack console: the result carries the computed "
                    "figures, column names, row count and provenance, never table rows.")
        return (f"The result carries the computed figures and at most "
                f"{self.options.max_rows} table rows, with restricted fields removed.")

    def _project(self, principal: Principal, target: str | None, columns: list[str],
                 rows: list[dict[str, Any]]) -> _Rows:
        """Rows for the client: the user's projection, minus every restricted field, capped."""
        shown, projected = project_result(principal, self.registry, target, columns, rows)
        keep = [c for c in shown if c not in self.restricted]
        capped = [{k: v for k, v in row.items() if k not in self.restricted}
                  for row in projected[:self.options.max_rows]]
        return _Rows(columns=keep, rows=capped, total=len(rows))

    def _columns(self, principal: Principal, target: str | None, columns: list[str]
                 ) -> list[str]:
        """Column names the user may see, minus restricted ones (metadata, never values)."""
        shown, _ = project_result(principal, self.registry, target, columns, [])
        return [c for c in shown if c not in self.restricted]

    def _append(self, audit_ids: list[int], steps: list[TraceStep]) -> None:
        if not audit_ids:
            return
        try:
            self.runtime.audit.append_trace(audit_ids[-1], steps)
        except Exception as exc:  # the run itself is audited; only the tail is missing
            log.warning("mcp: trace steps could not be attached to audit record %s (%s)",
                        audit_ids[-1], type(exc).__name__)

    def _deliver(self, text: str, payload: dict[str, Any], *, audit_ids: list[int],
                 steps: tuple[TraceStep, ...] = (), rows: _Rows | None = None, total: int = 0,
                 error: bool = False) -> types.CallToolResult:
        """Everything for the client passes the egress guard first, as any model payload does
        (restricted values redacted; if they cannot be determined, nothing is sent). The
        audit trail then records what left: an ``export`` step closes the call's record."""
        sent = rows.sent if rows else 0
        facts: dict[str, Any] = {"channel": "mcp", "rows_sent": sent,
                                 "rows_withheld": max(0, total - sent)}
        try:
            (text, payload), redacted = self.runtime.egress.scrub_value([text,
                                                                          jsonable(payload)])
        except Exception as exc:
            log.warning("mcp: result withheld, the egress check failed (%s)",
                        type(exc).__name__)
            self._append(audit_ids, [*steps, TraceStep(
                step="export", status=TraceStatus.BLOCKED, source=RouteSource.MCP,
                facts={**facts, "rows_sent": 0, "rows_withheld": total})])
            problem = "the result could not be checked for restricted values, so it was withheld"
            return types.CallToolResult(content=[types.TextContent(text=problem)],
                                        structured_content=_problem(problem, "error"),
                                        is_error=True)
        self._append(audit_ids, [*steps, TraceStep(
            step="export", status=TraceStatus.OK, source=RouteSource.MCP,
            facts={**facts, "redacted": redacted})])
        return types.CallToolResult(content=[types.TextContent(text=text)],
                                    structured_content=payload, is_error=error)

    def _refuse(self, problem: str, status: str, audit_ids: list[int] | None = None, *,
                failures: list[ModelCallError] | None = None) -> types.CallToolResult:
        """A tool error. ``failures`` (a run that reached a model) adds ``model_errors``."""
        payload = _problem(problem, status)
        if failures is not None:
            payload["model_errors"] = _model_errors(failures)
        return self._deliver(problem, payload, audit_ids=audit_ids or [], error=True)

    def _rows_note(self, rows: _Rows | None, total: int) -> str:
        if rows is None:
            return (f"{total} row(s) stay in the twotrack console (rows are not shared over "
                    "MCP)." if total else "")
        more = f" of {rows.total}" if rows.total > rows.sent else ""
        return f"Included {rows.sent}{more} row(s); restricted fields removed."

    # ------------------------------------------------------------ listing

    def instructions(self) -> str:
        principal = self.identity.principal(self.runtime)
        roles = ", ".join(sorted(r.value for r in principal.roles))
        return (
            "twotrack is a supply-chain data console whose code computes every number. "
            f"You act as {principal.user_id} ({roles}); the tools are what that user may run. "
            f"{self._rows_policy()} Tools that change data never change it from here: they "
            f"return a pending confirmation that {principal.user_id} approves in "
            f"{CONSOLE_APPROVALS}. Every call is audited. Cite figures exactly as returned.")

    def _describe(self, target: Routable, principal: Principal) -> str:
        parts = [target.description.strip()]
        if target.route_hint.strip():
            parts.append(target.route_hint.strip())
        if target.effect is ResourceEffect.READ:
            parts.append("Effect: read (changes no data).")
        elif target.approval is Approval.REQUIRED:
            parts.append(f"Effect: {target.effect.value}. Calling it changes nothing yet: "
                         f"twotrack records a pending confirmation that {principal.user_id} "
                         f"approves in {CONSOLE_APPROVALS}. It cannot be approved over MCP.")
        else:
            parts.append(f"Effect: {target.effect.value}. It needs no approval: calling it "
                         "runs it at once.")
        parts.append(self._rows_policy())
        return " ".join(parts)

    def _tool(self, target: Routable, principal: Principal) -> types.Tool:
        read = target.effect is ResourceEffect.READ
        title = humanize_key(target.name)
        return types.Tool(
            name=target.name, title=title, description=self._describe(target, principal),
            input_schema=dict(tool_card(target).params_schema),
            annotations=types.ToolAnnotations(
                title=title, read_only_hint=read, idempotent_hint=True if read else None,
                open_world_hint=target.effect is ResourceEffect.EXTERNAL))

    def _ask_tool(self) -> types.Tool:
        return types.Tool(
            name=ASK, title="Ask twotrack",
            description=("Ask a question in plain language (English or Chinese). twotrack's "
                         "own router picks the report, its code computes the figures and its "
                         "answer cites only those figures. A request that would change data "
                         "returns a pending confirmation for the console. "
                         + self._rows_policy()),
            input_schema={"type": "object", "additionalProperties": False,
                          "required": ["query"],
                          "properties": {"query": {"type": "string", "minLength": 1,
                                                   "maxLength": 2000,
                                                   "description": "The question"}}},
            annotations=types.ToolAnnotations(title="Ask twotrack", read_only_hint=False,
                                              open_world_hint=False))

    def tools(self) -> list[types.Tool]:
        try:
            principal = self.identity.principal(self.runtime)
        except IdentityError as exc:
            log.warning("mcp: listing no tools: %s", exc)
            return []
        candidates = self.registry.candidates(self._ctx(principal))
        listed = [self._tool(candidates.get(n), principal) for n in sorted(candidates.names())]
        return [*listed, self._ask_tool()] if self.ask_enabled else listed

    # ------------------------------------------------------------ calls

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> types.CallToolResult:
        try:
            principal = self.identity.principal(self.runtime)
        except IdentityError as exc:
            return self._refuse(str(exc), "blocked")
        if self.ask_enabled and name == ASK:
            return self._ask(principal, dict(arguments or {}))
        return self._run(principal, name, dict(arguments or {}))

    def _run(self, principal: Principal, name: str, params: dict[str, Any]
             ) -> types.CallToolResult:
        ctx = self._ctx(principal, query=name, language="en", request_id=uuid.uuid4().hex)
        candidates = self.registry.candidates(ctx)
        known = name in self.registry.tools or name in self.registry.workflows
        kind = self.registry.target(name).route_kind.value if known else "unknown"
        prior = [
            TraceStep(step="candidates", status=TraceStatus.OK,
                      facts={"count": len(candidates.names()),
                             "names": sorted(candidates.names())}),
            TraceStep(step="understand", status=TraceStatus.OK, source=RouteSource.MCP,
                      facts={"kind": kind, "target": name if known else "unknown",
                             "reason": "called directly by an MCP client"}),
        ]
        # the pipeline runs (and audits) even a call outside the candidate set: its gate
        # refuses it, and the attempt is on record
        narrations: list[Narration] = []
        # one guarded provider for the call: it keeps the model calls that failed
        run = self._run_context(ctx)
        outcome = self.runtime.engine.pipeline.run(
            ctx, name, params, workflow_runner=run.workflow_runner(narrations),
            route_source=RouteSource.MCP, prior_trace=prior)
        ids = audit_ids_of(outcome)
        if name not in candidates:  # unknown and forbidden read the same
            return self._refuse(unavailable_reason(name, self.registry, ctx), "blocked", ids)
        if outcome.status is PipelineStatus.NEEDS_CONFIRMATION and outcome.grant is not None:
            return self._confirmation(principal, name, outcome.grant.token,
                                      outcome.grant.summary, outcome.grant.expires_at, ids)
        if outcome.status is not PipelineStatus.OK or outcome.result is None:
            status = outcome.status.value
            # e.g. a workflow whose understand step got no usable answer from the model
            return self._refuse(outcome.problem or f"the call did not run ({status})", status,
                                ids, failures=run.provider.failures)
        return self._ok(principal, name, outcome.result, ids, narrations, run.provider)

    def _ok(self, principal: Principal, name: str, result: Any, ids: list[int],
            narrations: list[Narration], provider: GuardedProvider) -> types.CallToolResult:
        view = result_view(result, language="en", labels=labels_for(self.registry, name))
        # a workflow that narrates (behind the numeric lock) already wrote its summary
        narration = narrations[-1] if narrations else None
        summary = narration.text if narration else template_narration(view)
        source = narration.source if narration else "template"
        total = result.provenance.row_count
        rows = self._project(principal, name, list(result.columns), list(result.rows)) \
            if self.options.include_rows else None
        # the console's own step facts: the provider, and the failed call template text stands
        # in for (``model_error``) or every failed call (``model_errors``)
        steps = (
            TraceStep(step="narrate", status=TraceStatus.OK, facts=narrate_facts(
                provider, source, narration.stripped if narration else 0, "en")),
            TraceStep(step="respond", status=TraceStatus.OK, facts=respond_facts(
                provider, source, len(result.rows))))
        payload: dict[str, Any] = {
            "status": "ok", "target": name, "summary": summary,
            "aggregates": view["aggregates"], "labels": view["labels"],
            "columns": self._columns(principal, name, list(result.columns)),
            "row_count": total, "message": result.message,
            "provenance": result.provenance.model_dump(mode="json"),
            "rows_included": rows is not None, "audit_id": ids[-1] if ids else None,
            "model_errors": _model_errors(provider.failures)}
        if rows is not None:
            payload.update(columns=rows.columns, rows=rows.rows,
                           rows_truncated=rows.total > rows.sent)
        note = self._rows_note(rows, len(result.rows))
        return self._deliver(f"{summary}\n{note}".strip(), payload, audit_ids=ids, steps=steps,
                             rows=rows, total=len(result.rows))

    def _confirmation(self, principal: Principal, name: str, token: str, summary: str,
                      expires_at: Any, ids: list[int]) -> types.CallToolResult:
        expires = expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at)
        text = (f"Needs confirmation: {summary}. Nothing has been changed. {principal.user_id} "
                f"can approve it in {CONSOLE_APPROVALS} until {expires}; it cannot be "
                "approved over MCP.")
        return self._deliver(text, {
            "status": "needs_confirmation", "target": name, "summary": summary,
            "grant_id": grant_id_for(token), "expires_at": expires,
            "approve_as": principal.user_id, "approve_in": CONSOLE_APPROVALS,
            "audit_id": ids[-1] if ids else None}, audit_ids=ids)

    def _ask(self, principal: Principal, arguments: dict[str, Any]) -> types.CallToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._refuse("'query' must be a non-empty string", "invalid")
        response = run_query(self.runtime.engine, self._ctx(principal), query.strip())
        target = response.decision.target if response.decision else None
        rows = self._project(principal, target, response.columns, response.rows) \
            if self.options.include_rows and response.rows else None
        text, payload = self._ask_result(principal, response, rows, detect_language(query))
        return self._deliver(text, payload, audit_ids=response.audit_ids, rows=rows,
                             total=len(response.rows), error=response.error is not None)

    def _ask_result(self, principal: Principal, response: QueryResponse,
                    rows: _Rows | None, language: str) -> tuple[str, dict[str, Any]]:
        decision = response.decision
        tool = response.provenance.tool if response.provenance else None
        declared = labels_for(self.registry, tool)
        payload: dict[str, Any] = {
            "status": "error" if response.error else "ok", "answer": response.answer,
            "answer_source": response.answer_source,
            "decision": None if decision is None else {
                "kind": decision.kind.value, "target": decision.target,
                "source": decision.source.value, "confidence": decision.confidence},
            "aggregates": response.aggregates,
            "labels": {k: aggregate_label(k, language, declared) for k in response.aggregates},
            "columns": self._columns(principal, decision.target if decision else None,
                                     response.columns),
            "row_count": response.provenance.row_count if response.provenance else 0,
            "provenance": (response.provenance.model_dump(mode="json")
                           if response.provenance else None),
            "clarify_options": [{"name": o.name, "description": o.description}
                                for o in response.clarify_options],
            "rows_included": rows is not None, "audit_ids": response.audit_ids,
            "model_errors": _model_errors(response.model_errors)}
        lines = [response.error or response.answer]
        if response.grant is not None:
            grant = response.grant
            payload["status"] = "needs_confirmation"
            payload["approval"] = {"grant_id": grant_id_for(grant.token),
                                   "summary": grant.summary,
                                   "expires_at": grant.expires_at.isoformat(),
                                   "approve_as": principal.user_id,
                                   "approve_in": CONSOLE_APPROVALS}
            lines.append(f"Nothing has been changed. {principal.user_id} can approve it in "
                         f"{CONSOLE_APPROVALS}; it cannot be approved over MCP.")
        if response.clarify_options:
            payload["status"] = "clarify"
            lines.append("Options: " + "; ".join(f"{o.name} ({o.description})"
                                                 for o in response.clarify_options))
        if rows is not None:
            payload.update(columns=rows.columns, rows=rows.rows,
                           rows_truncated=rows.total > rows.sent)
        lines.append(self._rows_note(rows, len(response.rows)))
        return "\n".join(line for line in lines if line), payload


# ---------------------------------------------------------------- server


def build_server(app: TwotrackMcp) -> Server:
    """The low-level MCP server. Tool listing and calls run in a worker thread: the pipeline,
    the store and any model call are blocking."""

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await anyio.to_thread.run_sync(app.tools))

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await anyio.to_thread.run_sync(app.call, params.name, params.arguments or {})

    return Server("twotrack", version=__version__, title="twotrack",
                  instructions=app.instructions(), on_list_tools=list_tools,
                  on_call_tool=call_tool)


async def serve_stdio(app: TwotrackMcp) -> None:
    """Serve one client over stdin/stdout until it disconnects. Logs go to stderr only."""
    server = build_server(app)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


__all__ = [
    "ASK",
    "Identity",
    "IdentityError",
    "McpOptions",
    "TwotrackMcp",
    "build_server",
    "resolve_identity",
    "serve_stdio",
]
