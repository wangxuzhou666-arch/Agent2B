"""Request and response models: the API contract the console's types are generated from.

Core models that are already safe to serialize (TraceStep, RouteDecision, Provenance,
ColumnMapping, PreviewRow) are reused as they are. Models that would carry a bearer secret
(``GrantView.token``, ``Grant.token``) are not: the API hands out ``ApprovalRef`` (a public
grant id) and keeps every token on the server.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue

from twotrack.core import (
    FieldType,
    Provenance,
    Role,
    RouteDecision,
    Sensitivity,
    TraceStep,
)
from twotrack.core.audit import AuditStatus
from twotrack.core.entities import ColumnMapping, PreviewRow
from twotrack.core.graph.state import AnswerSource, ClarifyOption
from twotrack.providers.base import ModelCallError, ProviderInfo


def _model_errors() -> Any:
    # A factory default leaves the field out of the schema's defaults, so generated clients see
    # it as optional and code written before it still compiles; the server always sends it.
    return Field(default_factory=list, description=(
        "Model calls that failed while answering, in order (the call, the error class, the "
        "HTTP status). The answer fell back without them: routing to rules and BM25, "
        "narration to the template (answer_source `template`). Empty when every call "
        "succeeded or no model call was made."))


# ---------------------------------------------------------------- health and auth


class UploadLimits(BaseModel):
    """What an import accepts (the upload guard's settings): a larger file or more data rows
    is refused before any row is read."""

    max_upload_bytes: int
    max_rows: int


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    demo_mode: bool
    # the configured language model; ``mock`` means answers are template text (no API key)
    provider: ProviderInfo
    limits: UploadLimits


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class DemoRequest(BaseModel):
    role: Role


class UserOut(BaseModel):
    user_id: str
    roles: list[Role]


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut
    demo_mode: bool


class MeOut(BaseModel):
    user_id: str
    roles: list[Role]
    demo_mode: bool
    expires_at: datetime


class LogoutOut(BaseModel):
    """The caller's token is revoked: it is refused from now on (``token_revoked``)."""

    status: Literal["signed_out"]


# ---------------------------------------------------------------- approvals


class ApprovalRef(BaseModel):
    """A pending one-time confirmation. ``grant_id`` is public; the token never leaves the
    server. Approve it with ``POST /api/approvals/{grant_id}/approve`` before ``expires_at``.

    ``expires_in_s`` is the time left when the response was made, on the server's clock and
    never below 0: count down from it as of receipt rather than comparing ``expires_at`` with
    the browser's clock, which may be off. ``label`` names what is confirmed (the tool's
    title, or the import) in the reply language.

    ``target`` is the id of what is confirmed, the one the audit log records: the tool or
    workflow the grant is bound to (``create_po``), or ``import:<entity>`` for an import. A
    write the exploration agent proposed has no routed target (``decision.target`` is null), so
    this is where its id is. Null only when the request is no longer stored and nothing else
    names it."""

    grant_id: str
    target: str | None
    summary: str
    label: str
    expires_at: datetime
    expires_in_s: float


class ApprovalItem(BaseModel):
    """A confirmation waiting in the list. ``label``, ``summary`` and ``expires_in_s`` are
    worded for this request (``Accept-Language``); see ``ApprovalRef``."""

    grant_id: str
    kind: Literal["query", "import"]
    user_id: str
    target: str
    label: str
    summary: str
    route_source: str | None
    created_at: datetime
    expires_at: datetime
    expires_in_s: float
    mine: bool


class ApprovalList(BaseModel):
    items: list[ApprovalItem]


class ImportResultOut(BaseModel):
    entity: str
    written: int
    skipped: int
    digest: str
    audit_id: int


class ApprovalOutcome(BaseModel):
    """What running an approved request produced. ``status`` is the pipeline's verdict:
    only ``ok`` means the write happened."""

    grant_id: str
    kind: Literal["query", "import"]
    target: str
    status: Literal["ok", "blocked", "invalid", "error"]
    problem: str | None = None
    answer: str = ""
    answer_source: AnswerSource = "system"
    narrative_stripped: int = 0
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    aggregates: dict[str, int | float] = {}
    provenance: Provenance | None = None
    audit_ids: list[int] = []
    import_result: ImportResultOut | None = None
    trace: list[TraceStep] = []
    model_errors: list[ModelCallError] = _model_errors()


class RejectOut(BaseModel):
    grant_id: str
    status: Literal["rejected"]


# ---------------------------------------------------------------- query


class QueryRequest(BaseModel):
    query: str = Field(max_length=10_000)
    explicit_target: str | None = Field(default=None, max_length=128)
    explicit_params: dict[str, JsonValue] | None = None
    conversation_id: str | None = Field(default=None, max_length=64)


class QueryOut(BaseModel):
    """The engine's answer. ``rows`` are the table the signed-in user may see (projected for
    their role); the language model never receives them."""

    conversation_id: str
    query: str
    decision: RouteDecision | None = None
    answer: str = ""
    answer_source: AnswerSource = "system"
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    aggregates: dict[str, int | float] = {}
    provenance: Provenance | None = None
    narrative_stripped: int = 0
    approval: ApprovalRef | None = None  # set when a write waits for confirmation
    clarify_options: list[ClarifyOption] = []
    trace: list[TraceStep] = []
    error: str | None = None
    # audit records this answer left (open them with GET /api/audit/{id}); the last one
    # replays the whole decision ladder
    audit_ids: list[int] = []
    model_errors: list[ModelCallError] = _model_errors()


# ---------------------------------------------------------------- audit


class AuditEntryOut(BaseModel):
    id: int
    ts: datetime
    user_id: str
    roles: list[str]
    target: str | None  # null: the query was answered without naming one target
    effect: str | None
    status: AuditStatus
    route_source: str | None
    params_digest: str | None
    params: dict[str, JsonValue]  # restricted values are masked at write time
    row_count: int | None
    grant_id: str | None
    problem: str | None
    request_id: str | None = None  # shared by every record of one request (list ?request_id=)
    parent_id: int | None = None  # an approved write's execution -> the record that asked
    duration_ms: float | None  # the record's trace step durations, summed; None: no steps


class AuditDetailOut(AuditEntryOut):
    # the full step list for replay: candidates, understand, gate, execute, narrate, respond
    trace: list[TraceStep]


class AuditPageOut(BaseModel):
    items: list[AuditEntryOut]
    total: int
    page: int
    page_size: int


class StatBucket(BaseModel):
    route_source: str | None
    status: AuditStatus
    count: int


class AuditStatsOut(BaseModel):
    """Records per route source and outcome in a window: pipeline runs, plus the queries that
    were answered without one (``clarify``, ``chat``), so the shares cover every routing
    decision. ``started`` intent records are left out, so each pipeline run counts once. A
    query that no arm could place (the chat fallback) counts under ``fallback``, not under
    ``semantic`` (a BM25 pick). Records written without a route source (an approval-side
    refusal) count under ``none``, the value ``GET /api/audit?route_source=none`` lists them
    by."""

    since: datetime
    until: datetime | None
    total: int
    by_route_source: dict[str, int]  # records with no route source are keyed "none"
    by_status: dict[str, int]
    buckets: list[StatBucket]


# ---------------------------------------------------------------- entities


class FieldOut(BaseModel):
    id: str
    label: str  # in the reply language (Accept-Language)
    description: str  # likewise; "" when the field has none
    type: FieldType
    required: bool
    aliases: list[str]
    enum_values: list[str]
    unit: str | None
    sensitivity: Sensitivity


class CategoryOut(BaseModel):
    id: str
    label: str  # label and description are in the reply language (Accept-Language)
    description: str
    extends: str | None
    attrs: list[FieldOut]  # inherited and own, visible to the caller


class EntityOut(BaseModel):
    id: str
    label: str  # label and description are in the reply language (Accept-Language)
    description: str
    primary_key: str
    label_field: str
    fields: list[FieldOut]
    attrs: list[FieldOut]
    categories: list[CategoryOut]
    # how many of this entity's fields and attrs the caller's roles may not see (0 for an
    # admin); only the count, never which ones
    hidden_fields: int


class EntityList(BaseModel):
    items: list[EntityOut]


class RecordsPage(BaseModel):
    entity: str
    items: list[dict[str, Any]]  # rows projected for the caller; `_entity` names the category
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- import


class ImportPreviewOut(BaseModel):
    entity: str
    file_type: str
    encoding: str | None
    mapping: list[ColumnMapping]
    unmapped: list[str]
    rows: list[PreviewRow]
    valid_count: int
    error_count: int
    digest: str
    grant: ApprovalRef | None  # present when at least one row is valid


class ImportCommitRequest(BaseModel):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    grant_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class ImportCommitOut(BaseModel):
    status: Literal["needs_confirmation", "ok"]
    approval: ApprovalRef | None = None  # status needs_confirmation
    result: ImportResultOut | None = None  # status ok
