"""Graph state and the API response model.

``GraphState`` is the only thing that flows between nodes; every field is typed (no bare
dicts of unknown shape). ``trace`` uses an append reducer so each node returns only the steps
it emitted. ``QueryResponse`` is what the API returns: it may carry rows for the authorized
user's table, but rows never reach a model (narration and the agent loop see only
``narrator_view()``).
"""

import operator
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, JsonValue

from twotrack.providers.base import ModelCallError

from ..contract import Provenance, RouteDecision, ToolResult, TraceStep
from ..narration import Narration

AnswerSource = Literal["model", "template", "system"]


class GrantView(BaseModel):
    """A pending write: the client re-sends ``token`` as ``grant_token`` to confirm."""

    token: str
    expires_at: datetime
    summary: str

    @classmethod
    def from_grant(cls, grant: Any) -> "GrantView | None":
        if grant is None:
            return None
        return cls(token=str(grant.token), expires_at=grant.expires_at,
                   summary=str(grant.summary))


class ClarifyOption(BaseModel):
    """A target the user may pick. The client re-sends the question with ``explicit_target=
    name`` and, when set, ``explicit_params`` exactly as given here."""

    name: str
    description: str  # the target's description, in the reply language
    effect: str
    # a short display label for this choice, in the reply language: what a scope clarification
    # says it does ("Show all categories", "Packaging"), else the target's title ("Low stock").
    # The engine always sets it; None only for an option built elsewhere without one.
    label: str | None = None
    # send as explicit_params with this choice; None: send none (the server reads the question)
    explicit_params: dict[str, JsonValue] | None = None
    # the choice runs the target without a constraint the question named (scope guard): the
    # answer then says it is unfiltered
    unfiltered: bool = False


class QueryResponse(BaseModel):
    query: str
    language: str = "en"  # the reply language of this query, decided from its text
    decision: RouteDecision | None = None
    answer: str = ""
    answer_source: AnswerSource = "system"  # model text (locked), template, or system text
    columns: list[str] = []
    rows: list[dict[str, Any]] = []  # UI table only; never sent to a model
    aggregates: dict[str, int | float] = {}
    provenance: Provenance | None = None
    narrative_stripped: int = 0  # sentences the numeric lock removed from model text
    grant: GrantView | None = None  # set when a write needs confirmation
    clarify_options: list[ClarifyOption] = []
    trace: list[TraceStep] = []
    error: str | None = None
    audit_ids: list[int] = []  # audit records of this query; the last one replays it all
    # model calls that failed while answering, in order: the answer fell back without them
    # (routing to rules and BM25, narration to the template)
    model_errors: list[ModelCallError] = []


class GraphState(BaseModel):
    query: str
    explicit_target: str | None = None
    explicit_params: dict[str, JsonValue] | None = None
    grant_token: str | None = None

    decision: RouteDecision | None = None
    result: ToolResult | None = None
    narration: Narration | None = None  # a workflow's own narration, already locked
    draft: str | None = None  # open_task / chat text still to be locked
    draft_source: Literal["model", "template"] | None = None
    allowed: list[float] = []  # numbers the draft may contain

    answer: str = ""
    answer_source: AnswerSource = "system"
    narrative_stripped: int = 0
    grant: GrantView | None = None
    clarify_options: list[ClarifyOption] = []
    error: str | None = None

    # audit records the pipeline wrote for this query, and how many leading steps of ``trace``
    # the last of them already holds (the ``record`` node appends the rest)
    audit_ids: list[int] = []
    audit_mark: int = 0

    trace: Annotated[list[TraceStep], operator.add] = []
    response: QueryResponse | None = None
