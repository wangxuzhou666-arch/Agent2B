"""Natural-language queries, as a live trace stream or a single JSON answer."""

import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from twotrack.core import Principal, TraceStep
from twotrack.core.grants import grant_id_for
from twotrack.core.graph import QueryResponse, run_query, stream_query
from twotrack.core.params import registry_title
from twotrack.runtime import Runtime

from ..approval_views import approval_ref
from ..context import request_context
from ..deps import Session, authenticated, get_runtime
from ..errors import errors
from ..projection import project_result
from ..schemas import ApprovalRef, QueryOut, QueryRequest
from ..sse import frame, relay

router = APIRouter()
log = logging.getLogger("twotrack.server")

SSE_DOC = {
    200: {
        "description": (
            "`text/event-stream` by default: `trace` frames (TraceStep), then one `response` "
            "frame (QueryOut) or one `error` frame (ErrorEnvelope); `: heartbeat` comment "
            "lines every 15 s while the server works. With `?stream=false`: a QueryOut."),
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
}


def _stored_target(runtime: Runtime, grant_id: str) -> str | None:
    pending = runtime.approvals.get(grant_id)
    return pending.target if pending else None


def _approval(runtime: Runtime, response: QueryResponse) -> ApprovalRef | None:
    """The write waiting for confirmation, by its public grant id (the token stays in the
    approvals table), named by its target and titled and worded in the language of the
    question."""
    grant = response.grant
    if grant is None:
        return None
    grant_id = grant_id_for(grant.token)
    decided = response.decision.target if response.decision else None
    # a write the open-task agent planned has no decided target: the stored request knows it
    target = decided if decided is not None else _stored_target(runtime, grant_id)
    if target is None:
        log.warning("approval %s: no routed target and no stored request to name it", grant_id)
    label = registry_title(runtime.registry, target, response.language) if target else ""
    return approval_ref(runtime, grant_id=grant_id, target=target, summary=grant.summary,
                        label=label, expires_at=grant.expires_at)


def to_query_out(runtime: Runtime, principal: Principal, response: QueryResponse,
                 conversation_id: str) -> QueryOut:
    """The engine's response for this user: rows projected for their role, and a pending
    write described by its public grant id (the token stays in the approvals table)."""
    target = response.decision.target if response.decision else None
    columns, rows = project_result(principal, runtime.registry, target, response.columns,
                                   response.rows)
    approval = _approval(runtime, response)
    return QueryOut(
        conversation_id=conversation_id, query=response.query, decision=response.decision,
        answer=response.answer, answer_source=response.answer_source, columns=columns,
        rows=rows, aggregates=response.aggregates, provenance=response.provenance,
        narrative_stripped=response.narrative_stripped, approval=approval,
        clarify_options=response.clarify_options, trace=response.trace, error=response.error,
        audit_ids=response.audit_ids, model_errors=response.model_errors)


@router.post("/query", response_model=QueryOut, tags=["query"], responses={
    **SSE_DOC, **errors(401, 422)}, openapi_extra={"x-sse-events": {
        "trace": {"$ref": "#/components/schemas/TraceStep"},
        "response": {"$ref": "#/components/schemas/QueryOut"},
        "error": {"$ref": "#/components/schemas/ErrorEnvelope"}}})
async def query(body: QueryRequest, stream: bool = True,
                session: Session = Depends(authenticated),
                runtime: Runtime = Depends(get_runtime)) -> Any:
    """Route a question to a registered tool or workflow and answer it.

    The language model only routes and narrates; every number comes from code, and the rows
    in the answer are never shown to a model.

    The language of the answer and of every label in it (the tool's `label` in `provenance`,
    in `clarify_options` and in `approval`) is detected from the text of the question, not
    from `Accept-Language`: a Chinese question gets a Chinese answer.
    """
    conversation_id = body.conversation_id or uuid.uuid4().hex
    ctx = request_context(runtime, session.principal, conversation_id=conversation_id)
    call: dict[str, Any] = dict(explicit_target=body.explicit_target,
                                explicit_params=body.explicit_params)
    if not stream:
        response = await run_in_threadpool(run_query, runtime.engine, ctx, body.query, **call)
        return to_query_out(runtime, session.principal, response, conversation_id)

    def produce() -> Iterator[TraceStep | QueryResponse]:
        return stream_query(runtime.engine, ctx, body.query, **call)

    def render(item: TraceStep | QueryResponse) -> bytes:
        if isinstance(item, TraceStep):
            return frame("trace", item)
        return frame("response", to_query_out(runtime, session.principal, item, conversation_id))

    return StreamingResponse(
        relay(produce, render, runtime.settings.sse_heartbeat_seconds),
        media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
