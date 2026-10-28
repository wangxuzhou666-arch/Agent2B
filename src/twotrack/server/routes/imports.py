"""Spreadsheet import in two phases: preview (validate, nothing written), then commit.

The preview parses the upload and returns the mapping and per-row problems; the server stages
the file under the preview's grant. Committing names the preview by its digest, so the bytes
that were validated are the bytes that get written: the core re-parses the staged file and
refuses if anything changed. A commit without ``grant_id`` registers an approval the user
then approves; with the ``grant_id`` it runs now.
"""

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.concurrency import run_in_threadpool

from twotrack.core.entities import ImportPreview, PreviewRow, import_label
from twotrack.core.i18n import Language
from twotrack.runtime import Runtime

from ..approval_views import approval_ref, pending_ref
from ..deps import Session, authenticated, get_runtime, reply_language
from ..errors import ApiError, errors, not_found
from ..schemas import (
    ImportCommitOut,
    ImportCommitRequest,
    ImportPreviewOut,
)
from .approvals import run_import
from .entities import entity_or_404

router = APIRouter(tags=["import"])
FILENAME_MAX = 120


def _clean_filename(name: str | None) -> str:
    """Base name only, printable, short: it ends up in audit records and grant summaries."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    printable = "".join(c for c in base if c.isprintable())
    return printable[:FILENAME_MAX] or "upload"


async def read_capped(upload: UploadFile, limit: int) -> bytes:
    """At most ``limit + 1`` bytes: one byte over lets the core reject the file as too large
    (and audit it) without this process ever holding the rest."""
    data = await upload.read(limit + 1)
    await upload.close()
    return data


def _visible_values(session: Session, runtime: Runtime, entity_id: str,
                    preview: ImportPreview) -> list[PreviewRow]:
    """Preview rows with the values of fields this role may not see removed."""
    visible = {f.id for f in runtime.registry.entity(entity_id).visible_fields(session.principal)}
    return [row.model_copy(update={"values": {k: v for k, v in row.values.items()
                                              if k in visible}}) for row in preview.rows]


@router.post("/entities/{entity_id}/import/preview", response_model=ImportPreviewOut,
             responses=errors(400, 401, 403, 404, 413, 422))
async def preview_import(
    entity_id: str,
    file: UploadFile = File(description="CSV or XLSX; the type is detected from the content"),
    session: Session = Depends(authenticated),
    runtime: Runtime = Depends(get_runtime),
    language: Language = Depends(reply_language),
) -> ImportPreviewOut:
    """Validate an upload against the entity. Nothing is written.

    Everything the response says in words is in the language `Accept-Language` asks for (`zh`,
    `zh-CN` ...: Chinese; anything else, or no header: English): the label of each mapped
    column, the problems of each row, the confirmation's label and summary, and the message
    of a file that is refused (an empty file, one named `.xlsx` that is no workbook, missing or
    colliding columns ...).
    """
    entity = entity_or_404(runtime.registry, entity_id)
    raw = await read_capped(file, runtime.settings.max_upload_bytes)
    return await run_in_threadpool(_preview, session, runtime, entity.id,
                                   _clean_filename(file.filename), raw, language)


def _preview(session: Session, runtime: Runtime, entity_id: str, filename: str,
             raw: bytes, language: Language) -> ImportPreviewOut:
    """Parse, validate and stage (CPU and database work: runs in a worker thread)."""
    preview = runtime.importer.preview(session.principal, entity_id, filename, raw,
                                       language=language)
    grant = preview.grant
    if grant is None:
        approval = None
    else:
        stale = runtime.approvals.add_staged_import(
            session.principal, grant, target=grant.target, entity_id=entity_id,
            filename=filename, digest=preview.digest, payload=raw,
            valid_count=preview.valid_count)
        for token in stale:
            runtime.grants.reject(token, session.principal)
        approval = approval_ref(
            runtime, grant_id=grant.grant_id, target=grant.target, summary=grant.summary,
            label=import_label(runtime.registry.entity(entity_id), language),
            expires_at=grant.expires_at)
    return ImportPreviewOut(
        entity=preview.entity, file_type=preview.file_type, encoding=preview.encoding,
        mapping=preview.mapping, unmapped=preview.unmapped,
        rows=_visible_values(session, runtime, entity_id, preview),
        valid_count=preview.valid_count, error_count=preview.error_count,
        digest=preview.digest, grant=approval)


@router.post("/entities/{entity_id}/import/commit", response_model=ImportCommitOut,
             responses=errors(401, 403, 404, 409, 410, 422))
def commit_import(entity_id: str, body: ImportCommitRequest,
                  session: Session = Depends(authenticated),
                  runtime: Runtime = Depends(get_runtime),
                  language: Language = Depends(reply_language)) -> ImportCommitOut:
    """Write the valid rows of a previewed upload.

    Without `grant_id` this asks for confirmation (`needs_confirmation`, and the approval
    appears in `/api/approvals`). With the `grant_id` from the preview or that approval it
    writes now. Either way only the user who previewed can commit.

    The approval's `label` and `summary`, and the message of a refusal, are worded in the
    language `Accept-Language` asks for (`zh`, `zh-CN` ...: Chinese; anything else, or no
    header: English).
    """
    entity = entity_or_404(runtime.registry, entity_id)
    staged = runtime.approvals.find_staged(session.user_id, entity.id, body.digest,
                                           body.grant_id)
    if staged is None:
        raise not_found("A previewed upload with this digest (it may have expired)")
    if body.grant_id is None:
        if staged.state == "staged" and not runtime.approvals.submit(staged.grant_id,
                                                                     session.user_id):
            raise ApiError(410, "approval_expired", "The preview has expired; upload again.")
        return ImportCommitOut(status="needs_confirmation",
                               approval=pending_ref(runtime, staged, language))
    claimed = runtime.approvals.claim(staged.grant_id, user_id=session.user_id,
                                      states=("staged", "awaiting"))
    if claimed is None:
        raise not_found("A previewed upload with this digest")
    if claimed.expires_at <= runtime.clock():
        runtime.grants.reject(claimed.token, session.principal)
        raise ApiError(410, "approval_expired", "The preview has expired; upload again.")
    outcome = run_import(runtime, session.principal, claimed, language)
    return ImportCommitOut(status="ok", result=outcome.import_result)
