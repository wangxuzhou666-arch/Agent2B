"""Confirmations for writes.

A write the engine routes, and an import the user submits, stop at a one-time grant. This
module lists those waiting for the signed-in user, runs one when its requester approves it,
and cancels one when its requester (or an admin) rejects it. Only the requester can approve:
the stored request replays as the stored user and the core's grant check binds the grant to
that same user, so nobody confirms a write on someone else's behalf.

Every record written here links back to the record that asked for the confirmation
(``parent_id``) and shares its ``request_id``, so the audit replays request -> confirmation
-> execution (or refusal) as one chain. Refusing an approval writes such a record, under the
confirmation's ``grant_id``, whatever the reason: someone else's request, a confirmation that
already ran or that a concurrent approval has taken, expired or was never submitted. A 404 writes
none: the id was never issued, or the rejection (or the refused run) that closed it has its own
record.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Path

from twotrack.core import Principal, Role, TraceStatus, TraceStep
from twotrack.core.audit import AuditEntry, AuditQuery, AuditRecorder, AuditStatus
from twotrack.core.grants import GrantState
from twotrack.core.graph import RunContext
from twotrack.core.graph.builder import narrate_facts, respond_facts
from twotrack.core.guards import UploadRejected
from twotrack.core.i18n import Language, detect_language, say
from twotrack.core.narration import Narration, labels_for, narrate_result
from twotrack.core.pipeline import PipelineOutcome, PipelineStatus
from twotrack.core.ports import default_lock
from twotrack.providers.base import guarded
from twotrack.runtime import Runtime

from ..approval_views import approval_label, approval_summary, seconds_left
from ..approvals import PendingRequest
from ..context import request_context
from ..deps import Session, authenticated, get_runtime, reply_language
from ..errors import ApiError, errors, not_found
from ..projection import project_result
from ..schemas import ApprovalItem, ApprovalList, ApprovalOutcome, ImportResultOut, RejectOut

router = APIRouter(tags=["approvals"])
log = logging.getLogger("twotrack.server")

GrantId = Path(pattern=r"^[0-9a-f]{16}$", description="Public id of the pending confirmation")


def request_record(runtime: Runtime, grant_id: str) -> AuditEntry | None:
    """The record that asked for this confirmation: the request's routing ladder and gate."""
    found = runtime.audit.list(
        AuditQuery(grant_id=grant_id, status=AuditStatus.NEEDS_CONFIRMATION), limit=1)
    return found.items[0] if found.items else None


def _links(origin: AuditEntry | None) -> dict:
    return {"parent_id": origin.id if origin else None,
            "request_id": origin.request_id if origin else None}


def _record(runtime: Runtime, session: Session, target: str | None, status: AuditStatus,
            grant_id: str, problem: str) -> None:
    """Audit an approval-side decision (a refusal or a rejection) made by the signed-in user:
    one ``gate`` step, the reason as ``problem``, in the chain of the request that asked for
    the confirmation."""
    origin = request_record(runtime, grant_id)
    record = AuditRecorder(
        runtime.audit, runtime.clock, user_id=session.user_id,
        roles=sorted(r.value for r in session.principal.roles), target=target,
        effect=origin.effect if origin and origin.effect else "write",  # the target's own effect
        route_source=None)
    step = TraceStep(step="gate", status=TraceStatus.BLOCKED,
                     facts={"stage": "approval", "problem": problem})
    record(status, grant_id=grant_id, problem=problem, trace=[step], **_links(origin))


def _refuse(runtime: Runtime, session: Session, target: str | None, grant_id: str,
            error: ApiError) -> ApiError:
    """``error``, once its refusal is in the audit log under the confirmation's grant id."""
    _record(runtime, session, target, AuditStatus.BLOCKED, grant_id, error.code)
    return error


def _expired() -> ApiError:
    return ApiError(410, "approval_expired", "This confirmation has expired; ask again.")


def _settled(runtime: Runtime, grant_id: str) -> tuple[GrantState | None, str | None]:
    """What became of a confirmation no request waits on any more, and its target: the grant
    store's word while it remembers the grant; after that, the audit log's (the store only
    forgets a grant once it has expired, and a write records ``started`` only after it has
    consumed its grant). (None, None) for an id that was never issued."""
    found = runtime.grants.find(grant_id)
    if found is not None:
        return found.state, found.target
    origin = request_record(runtime, grant_id)
    if origin is None:
        return None, None
    ran = runtime.audit.list(AuditQuery(grant_id=grant_id, status=AuditStatus.STARTED),
                             limit=1).total
    return ("used" if ran else "expired"), origin.target


def _not_waiting(runtime: Runtime, session: Session, grant_id: str,
                 language: Language) -> ApiError:
    """The refusal for approving a confirmation that waits no more: it already ran, or another
    approval has taken it and is running it (409 ``grant_used``), or it expired (410), both
    audited; never issued, or rejected, 404.

    A request that is gone while its grant is still open was taken by an approval that has not
    used the grant yet: ``claim`` deletes the request first and the run consumes the grant a
    moment later. A rejection never looks like that: it takes the request and closes the grant
    in one transaction (``reject``). So the loser of two concurrent approvals hears what a late
    duplicate hears, whichever of the two windows of the race it fell in (its read after the
    other's claim, or its claim after the other's)."""
    state, target = _settled(runtime, grant_id)
    if state in ("used", "open"):
        return _refuse(runtime, session, target, grant_id,
                       ApiError(409, "grant_used", say("approval_used", language)))
    if state == "expired":
        return _refuse(runtime, session, target, grant_id, _expired())
    return not_found("This confirmation")


def _pending_or_404(runtime: Runtime, grant_id: str) -> PendingRequest:
    pending = runtime.approvals.get(grant_id)
    if pending is None:
        raise not_found("This confirmation")
    return pending


@router.get("/approvals", response_model=ApprovalList, responses=errors(401))
def list_approvals(session: Session = Depends(authenticated),
                   runtime: Runtime = Depends(get_runtime),
                   language: Language = Depends(reply_language)) -> ApprovalList:
    """Confirmations waiting to be approved: the caller's own; an admin sees everyone's.

    `label` and `summary` are worded in the language `Accept-Language` asks for (`zh`, `zh-CN`
    ...: Chinese; anything else, or no header: English), whatever language the question was
    asked in. `expires_in_s` is the time left on the server's clock: count down from it as of
    receipt instead of comparing `expires_at` with the browser's clock.
    """
    infos = runtime.grants.pending(session.principal)
    submitted = runtime.approvals.awaiting([i.grant_id for i in infos])
    now = runtime.clock()
    items = []
    for info in infos:
        pending = submitted.get(info.grant_id)
        if pending is None:  # a staged upload that was never submitted is not an approval
            continue
        items.append(ApprovalItem(
            grant_id=info.grant_id, kind=pending.kind, user_id=info.user_id, target=info.target,
            label=approval_label(runtime, pending, language),
            summary=approval_summary(runtime, pending, language),
            route_source=pending.route_source, created_at=info.created_at,
            expires_at=info.expires_at, expires_in_s=seconds_left(info.expires_at, now),
            mine=info.user_id == session.user_id))
    return ApprovalList(items=items)


def run_query_approval(runtime: Runtime, principal: Principal, claimed: PendingRequest
                       ) -> ApprovalOutcome:
    """Run a claimed query confirmation once, as its requester, linked to its request."""
    origin = request_record(runtime, claimed.grant_id)
    language = detect_language(claimed.query)
    ctx = request_context(runtime, principal, query=claimed.query, language=language,
                          request_id=(origin.request_id if origin and origin.request_id
                                      else uuid.uuid4().hex))
    run = RunContext(engine=runtime.engine, ctx=ctx,
                     provider=guarded(runtime.provider, runtime.egress),
                     lock=runtime.engine.lock or default_lock())
    narrations: list[Narration] = []
    outcome = runtime.pipeline.run(
        ctx, claimed.target, claimed.params, grant_token=claimed.token,
        workflow_runner=run.workflow_runner(narrations), route_source=claimed.route_source,
        parent_id=origin.id if origin else None)
    if outcome.status is not PipelineStatus.OK:
        runtime.grants.reject(claimed.token, principal)  # nothing ran: close the grant too
    return _query_outcome(runtime, principal, claimed, outcome, run, narrations)


def _attach(runtime: Runtime, audit_ids: list[int], steps: list[TraceStep]) -> None:
    """Add the narration steps to the execution's record; the write is already audited."""
    if not audit_ids:
        return
    try:
        runtime.audit.append_trace(audit_ids[-1], steps)
    except Exception as exc:
        log.warning("audit: narration steps could not be attached to record %s (%s)",
                    audit_ids[-1], type(exc).__name__)


def _query_outcome(runtime: Runtime, principal: Principal, claimed: PendingRequest,
                   outcome: PipelineOutcome, run: RunContext, narrations: list[Narration]
                   ) -> ApprovalOutcome:
    common = dict(grant_id=claimed.grant_id, kind="query", target=claimed.target,
                  audit_ids=outcome.audit_ids, trace=outcome.trace, problem=outcome.problem)
    result = outcome.result
    if outcome.status is not PipelineStatus.OK or result is None:
        status = outcome.status.value
        if status not in ("blocked", "invalid"):
            status = "error"
        return ApprovalOutcome(status=status, model_errors=list(run.provider.failures),
                               **common)
    language = run.language
    narration = narrations[-1] if narrations else narrate_result(
        run.provider, claimed.query, result, run.lock, language=language,
        labels=labels_for(runtime.registry, claimed.target))
    columns, rows = project_result(principal, runtime.registry, claimed.target, result.columns,
                                   result.rows)
    _attach(runtime, outcome.audit_ids, [
        TraceStep(step="narrate", status=TraceStatus.OK, facts=narrate_facts(
            run.provider, narration.source, narration.stripped, language)),
        TraceStep(step="respond", status=TraceStatus.OK, facts=respond_facts(
            run.provider, narration.source, len(rows)))])
    return ApprovalOutcome(
        status="ok", answer=narration.text, answer_source=narration.source,
        narrative_stripped=narration.stripped, columns=columns, rows=rows,
        aggregates=result.aggregates, provenance=result.provenance,
        model_errors=list(run.provider.failures), **common)


def run_import(runtime: Runtime, principal: Principal, claimed: PendingRequest,
               language: str | None = None) -> ApprovalOutcome:
    """Commit a staged upload. Shared by approve and by the commit endpoint. ``language``
    words the outcome's answer (English when not given)."""
    try:
        result = runtime.importer.commit(
            principal, claimed.entity_id or "", claimed.filename or "upload",
            claimed.payload or b"", expected_digest=claimed.digest or "",
            grant_token=claimed.token, **_links(request_record(runtime, claimed.grant_id)))
    except UploadRejected:
        runtime.grants.reject(claimed.token, principal)  # refused before the grant was used
        raise
    return ApprovalOutcome(
        grant_id=claimed.grant_id, kind="import", target=claimed.target, status="ok",
        audit_ids=[result.audit_id], import_result=ImportResultOut(**result.model_dump()),
        answer=say("import_done", language, written=result.written, skipped=result.skipped))


@router.post("/approvals/{grant_id}/approve", response_model=ApprovalOutcome,
             responses=errors(401, 403, 404, 409, 410, 422))
def approve(grant_id: str = GrantId, session: Session = Depends(authenticated),
            runtime: Runtime = Depends(get_runtime),
            language: Language = Depends(reply_language)) -> ApprovalOutcome:
    """Run the request this confirmation guards, once. Only its requester may approve.

    The outcome of an import is worded in the language `Accept-Language` asks for (`zh`,
    `zh-CN` ...: Chinese; anything else, or no header: English). A confirmed question runs and
    is narrated in the language the question was asked in, like the answer it continues.

    Refusals: 403 `not_requester`; 409 `not_submitted` (an upload not yet committed for
    approval) or `grant_used` (it already ran, or another approval has taken it and is running
    it: approving again never runs it twice, however close the two approvals are); 410
    `approval_expired`; an import's own (such as 409 `digest_mismatch`); 404 for an id that
    was never issued or a confirmation that was rejected. Each 403, 409 and 410 leaves an audit
    record under the grant id (`GET /api/audit?grant_id=`) with the reason in `problem`; a
    rejected confirmation already has the record that says why.
    """
    pending = runtime.approvals.get(grant_id)
    if pending is None:
        raise _not_waiting(runtime, session, grant_id, language)
    if pending.user_id != session.user_id:
        raise _refuse(runtime, session, pending.target, grant_id, ApiError(
            403, "not_requester", "Only the person who made the request can approve it."))
    if pending.state != "awaiting":
        raise _refuse(runtime, session, pending.target, grant_id, ApiError(
            409, "not_submitted", "This upload has not been submitted for approval; commit it "
            "first."))
    claimed = runtime.approvals.claim(grant_id, user_id=session.user_id, states=("awaiting",))
    if claimed is None:  # a concurrent approve or reject took it between the read and the claim
        raise _not_waiting(runtime, session, grant_id, language)
    if claimed.expires_at <= runtime.clock():
        runtime.grants.reject(claimed.token, session.principal)
        raise _refuse(runtime, session, claimed.target, grant_id, _expired())
    if claimed.kind == "import":
        return run_import(runtime, session.principal, claimed, language)
    return run_query_approval(runtime, session.principal, claimed)


@router.post("/approvals/{grant_id}/reject", response_model=RejectOut,
             responses=errors(401, 403, 404, 422))
def reject(grant_id: str = GrantId, session: Session = Depends(authenticated),
           runtime: Runtime = Depends(get_runtime)) -> RejectOut:
    """Cancel a pending confirmation. Its requester can; so can an admin."""
    pending = _pending_or_404(runtime, grant_id)
    is_admin = Role.ADMIN in session.principal.roles
    if pending.user_id != session.user_id and not is_admin:
        _record(runtime, session, pending.target, AuditStatus.BLOCKED, grant_id, "not_requester")
        raise ApiError(403, "not_requester", "Only the requester or an admin can reject it.")
    # one transaction: a concurrent approval must never find the request gone with its grant
    # still open, which is how it tells an approval in flight from a rejection (``_not_waiting``)
    with runtime.database.transaction():
        claimed = runtime.approvals.claim(grant_id, user_id=None, states=("awaiting", "staged"))
        if claimed is None:
            raise not_found("This confirmation")
        runtime.grants.reject(claimed.token, session.principal)
    problem = "rejected" if claimed.user_id == session.user_id \
        else f"rejected_for:{claimed.user_id}"
    _record(runtime, session, claimed.target, AuditStatus.BLOCKED, grant_id, problem)
    return RejectOut(grant_id=grant_id, status="rejected")
