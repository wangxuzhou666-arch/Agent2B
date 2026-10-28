"""Core contract: the types every plugin, router and pipeline stage agree on.

Thesis: LLMs route and narrate; code computes. These types keep the two apart:

* a Tool or Workflow is code; the numbers it computes live in ``ToolResult.aggregates``;
* the router only emits a ``RouteDecision`` naming a target from the candidate set;
* the narrator only sees ``ToolResult.narrator_view()``, never the raw rows.

Types that the API or frontend serialize are Pydantic models (JSON schema / OpenAPI ready).
Types that carry callables or classes (Tool, Workflow, Step, Precondition, Plugin, ToolContext)
are frozen dataclasses. Their constructors stay permissive about semantic problems on purpose:
each exposes ``problems()`` and the registry reports every problem of every plugin at once.
"""

import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .i18n import DEFAULT_LANGUAGE, LANGUAGES, localized

ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_ID_RE = re.compile(ID_PATTERN)

# row key naming the (category) entity a row belongs to; the store writes it and the result
# projection reads it to apply that category's field visibility
ENTITY_KEY = "_entity"


class Role(StrEnum):
    """Fixed v0.1 role set. ``admin`` passes every role allow-list."""

    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLES: frozenset[str] = frozenset(r.value for r in Role)


class ResourceEffect(StrEnum):
    READ = "read"
    WRITE = "write"  # persistent change; P1c requires a one-time confirmation grant
    EXTERNAL = "external"  # leaves the deployment (outbound call)


class Approval(StrEnum):
    """Whether a run waits for a person's one-time approval (a grant) before it executes.

    A target declares it. The registry ties it to the effect (a write always waits, a read never
    does; an external call may be either) and the pipeline issues and consumes grants by this
    field alone."""

    REQUIRED = "required"  # the first call only issues a grant; the run follows its approval
    NONE = "none"  # the call runs at once


_ALL_EFFECTS = frozenset({ResourceEffect.READ, ResourceEffect.WRITE, ResourceEffect.EXTERNAL})

# Core policy, not plugin data: the effects a role may be given. A target's ``roles`` can narrow
# this and never widen it, and the role set is fixed, so installing a plugin grants nobody
# anything new. The registry refuses a target that lists a role its effect is not allowed for;
# ``Routable.allows`` checks the same table on every call. An effect missing from a row is
# denied, so a new effect stays closed to everyone until someone adds it here.
ROLE_EFFECTS: Mapping[Role, frozenset[ResourceEffect]] = MappingProxyType({
    Role.VIEWER: frozenset({ResourceEffect.READ}),
    Role.OPERATOR: _ALL_EFFECTS,
    Role.ADMIN: _ALL_EFFECTS,
})


class FieldType(StrEnum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    ENUM = "enum"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class StepKind(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class RouteSource(StrEnum):
    EXPLICIT = "explicit"
    RULE = "rule"
    LLM = "llm"
    LLM_CONFIRM = "llm_confirm"
    SEMANTIC = "semantic"
    AGENT = "agent"
    MCP = "mcp"  # an MCP client's model picked the target and called it directly
    FALLBACK = "fallback"  # no arm picked anything: the query fell through to chat


class RouteKind(StrEnum):
    TOOL = "tool"
    WORKFLOW = "workflow"
    CLARIFY = "clarify"
    OPEN_TASK = "open_task"
    CHAT = "chat"


class TraceStatus(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    ERROR = "error"


TRACE_STEPS = ("understand", "scope", "candidates", "gate", "execute", "narrate", "respond")

# Reserved key of ``explicit_params``: the scope constraints (``ScopeMention.id``) the user chose
# to run without, after a scope clarification offered the unfiltered run (core.routing.scope_guard)
SCOPE_ACK_PARAM = "_scope_ack"


def _values(enum: type[StrEnum]) -> frozenset[str]:
    return frozenset(m.value for m in enum)


def normalize_header(text: str) -> str:
    """Import-header key: width/case folded, whitespace, '_' and '-' removed."""
    return re.sub(r"[\s_\-]+", "", unicodedata.normalize("NFKC", text).casefold())


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    roles: frozenset[Role] = frozenset()

    def can_access(self, allowed: Iterable[str] | None) -> bool:
        """Role allow-list check. ``None`` means open to all; admin passes every list."""
        if allowed is None:
            return True
        return Role.ADMIN in self.roles or not self.roles.isdisjoint(allowed)


# ---------------------------------------------------------------- entities


def _phrase_table_problems(where: str, table: Mapping[str, Iterable[str]]) -> list[str]:
    """Problems of a ``{language: phrases}`` table (synonyms of an entity or an enum value)."""
    out = []
    for language, phrases in table.items():
        if language not in LANGUAGES:
            out.append(f"{where}: language {language!r} is not supported; use one of "
                       f"{list(LANGUAGES)}")
        if any(not phrase.strip() for phrase in phrases):
            out.append(f"{where}: phrases must be non-empty")
    return out


def _text_tables(data: Any) -> Any:
    """Before an entity or field is validated: ``label`` and ``description`` as a plugin writes
    them become the English text they keep, and the table of every language beside it
    (``labels``, ``descriptions``).

    A plain string is the text of every language, an empty one no text at all. A mapping is
    kept as written (``_text_problems`` judges it). An object that already holds both forms,
    like a model read back from its own dump, is left as it is."""
    if not isinstance(data, Mapping):
        return data
    out = dict(data)
    for name, plural in (("label", "labels"), ("description", "descriptions")):
        value = out.get(name)
        if isinstance(value, Mapping):
            out[plural], out[name] = dict(value), value.get(DEFAULT_LANGUAGE, "")
        elif isinstance(value, str) and plural not in out:
            text = value.strip()
            table = {language: text for language in LANGUAGES} if text else {}
            out[plural], out[name] = table, table.get(DEFAULT_LANGUAGE, "")
    return out


def _text_problems(where: str, what: str, table: Mapping[str, str], *, required: bool
                   ) -> list[str]:
    """Problems of a label or description table. English is the fallback of every language, so
    a table that has any text must have it; only a description may have none."""
    out = []
    for language, text in table.items():
        if language not in LANGUAGES:
            out.append(f"{where}: {what} language {language!r} is not supported; use one of "
                       f"{list(LANGUAGES)}")
        elif not text.strip():
            out.append(f"{where}: {what} for {language!r} is empty")
    if (required or table) and DEFAULT_LANGUAGE not in table:
        out.append(f"{where}: {what} needs a {DEFAULT_LANGUAGE!r} text (the fallback of every "
                   "other language)")
    return out


class FieldSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=ID_PATTERN)
    # Display name and explanation. A plugin writes each as a plain string (the same in every
    # language) or {"en": ..., "zh": ...}, like a tool's ``title``; ``label`` and
    # ``description`` then hold the English text and ``labels`` and ``descriptions`` the text of
    # every language (read one with ``label_in`` / ``description_in``).
    label: str
    labels: dict[str, str] = {}
    description: str = ""
    descriptions: dict[str, str] = {}
    type: FieldType = FieldType.STR
    required: bool = False
    aliases: tuple[str, ...] = ()  # alternate import headers, matched via normalize_header
    enum_values: tuple[str, ...] = ()
    unit: str | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    visible_roles: frozenset[Role] | None = None  # None = visible to every role
    # An enum field a question can name as a constraint ("in WH-EAST", "class A"): routing binds
    # it to a param that filters by it, or asks before running a target that cannot (core.scope).
    scope_key: bool = False
    # value -> language -> other phrases for that value ("class A", "A类"); the first phrase of
    # a non-English language is the value's display name in it. A one-word phrase ("drafts")
    # matches only as written; a longer one also matches with its words' plural "s" folded.
    value_synonyms: dict[str, dict[str, tuple[str, ...]]] = {}

    @model_validator(mode="before")
    @classmethod
    def _tables(cls, data: Any) -> Any:
        return _text_tables(data)

    @model_validator(mode="after")
    def _check(self) -> "FieldSpec":
        where = f"field '{self.id}'"
        texts = _text_problems(where, "label", self.labels, required=True)
        texts += _text_problems(where, "description", self.descriptions, required=False)
        if texts:
            raise ValueError("; ".join(texts))
        if (self.type is FieldType.ENUM) != bool(self.enum_values):
            raise ValueError(f"field '{self.id}': enum_values is required for, and only for, enum")
        if self.sensitivity is Sensitivity.RESTRICTED and self.visible_roles is None:
            raise ValueError(f"field '{self.id}': restricted fields must declare visible_roles")
        if self.scope_key and self.type is not FieldType.ENUM:
            raise ValueError(f"field '{self.id}': scope_key is only for enum fields")
        if self.value_synonyms and not self.scope_key:
            raise ValueError(f"field '{self.id}': value_synonyms need scope_key=True")
        unknown = sorted(set(self.value_synonyms) - set(self.enum_values))
        if unknown:
            raise ValueError(f"field '{self.id}': value_synonyms for unknown value(s) {unknown}")
        problems = [p for value, table in self.value_synonyms.items()
                    for p in _phrase_table_problems(f"field '{self.id}' value {value!r}", table)]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def label_in(self, language: str | None) -> str:
        """The label in ``language``; the English one when it has none there."""
        return localized(self.labels, language) or self.label

    def description_in(self, language: str | None) -> str:
        """The description in ``language`` (English when it has none there); "" if undeclared."""
        return localized(self.descriptions, language) or self.description

    def header_texts(self) -> tuple[str, ...]:
        """Every header text that maps to this field on import: id, the label in every language
        (a Chinese sheet may use the Chinese label), aliases."""
        return tuple(dict.fromkeys((self.id, self.label, *self.labels.values(), *self.aliases)))

    def visible_to(self, principal: Principal) -> bool:
        return principal.can_access(self.visible_roles)


class EntitySpec(BaseModel):
    """A business object as a plugin declares it.

    ``fields`` are core, strongly typed columns. ``attrs`` are category-specific fields stored
    as JSON. An entity that ``extends`` a parent inherits the parent's core fields and attrs and
    may only add attrs (so a new category never needs new core columns).

    ``label`` and ``description`` are written as plain strings (the same text in every
    language) or ``{"en": ..., "zh": ...}``, like a tool's ``title``; they then hold the English
    text and ``labels`` / ``descriptions`` the text of every language. A category's label is
    how the console and the scope guard name it, in each language (core.scope).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=ID_PATTERN)
    label: str
    labels: dict[str, str] = {}
    description: str = ""
    descriptions: dict[str, str] = {}
    primary_key: str | None = None  # required on a root entity; a child inherits it
    label_field: str | None = None  # defaults to the parent's, then to primary_key
    fields: tuple[FieldSpec, ...] = ()
    attrs: tuple[FieldSpec, ...] = ()
    extends: str | None = None
    # language -> other names of a category ("spare parts", "备品备件"): with its id and its
    # label in every language they are how a question names it (core.scope); the label is how
    # it is shown. Not inherited.
    synonyms: dict[str, tuple[str, ...]] = {}

    @model_validator(mode="before")
    @classmethod
    def _tables(cls, data: Any) -> Any:
        return _text_tables(data)

    @model_validator(mode="after")
    def _check(self) -> "EntitySpec":
        where = f"entity '{self.id}'"
        problems = _text_problems(where, "label", self.labels, required=True)
        problems += _text_problems(where, "description", self.descriptions, required=False)
        problems += _phrase_table_problems(f"{where} synonyms", self.synonyms)
        if problems:
            raise ValueError("; ".join(problems))
        return self


class ResolvedEntity(BaseModel):
    """An entity with its parent chain merged in. This is what the API and storage consume.

    ``label`` and ``description`` are the English texts, for code that does not care about the
    reply language; ``label_in`` and ``description_in`` pick another language.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    description: str
    labels: dict[str, str] = {}  # the label in every language that has one (``label`` is "en")
    descriptions: dict[str, str] = {}  # likewise; empty when the entity has no description
    primary_key: str
    label_field: str
    fields: tuple[FieldSpec, ...]
    attrs: tuple[FieldSpec, ...]
    extends: str | None
    lineage: tuple[str, ...]  # root first, this entity last
    synonyms: dict[str, tuple[str, ...]] = {}  # the entity's own, see EntitySpec.synonyms

    @property
    def all_fields(self) -> tuple[FieldSpec, ...]:
        return self.fields + self.attrs

    def label_in(self, language: str | None) -> str:
        """The label in ``language``; the English one when it has none there."""
        return localized(self.labels, language) or self.label

    def description_in(self, language: str | None) -> str:
        """The description in ``language`` (English when it has none there); "" if undeclared."""
        return localized(self.descriptions, language) or self.description

    def field(self, field_id: str) -> FieldSpec:
        for f in self.all_fields:
            if f.id == field_id:
                return f
        raise KeyError(f"entity '{self.id}' has no field '{field_id}'")

    def match_header(self, header: str) -> FieldSpec | None:
        key = normalize_header(header)
        for f in self.all_fields:
            if key in {normalize_header(t) for t in f.header_texts()}:
                return f
        return None

    def visible_fields(self, principal: Principal) -> tuple[FieldSpec, ...]:
        return tuple(f for f in self.all_fields if f.visible_to(principal))


def resolve_entity(spec: EntitySpec, parent: ResolvedEntity | None) -> ResolvedEntity:
    """Merge ``spec`` onto its resolved parent. Pure; validation lives in the registry."""
    primary_key = spec.primary_key or (parent.primary_key if parent else "")
    return ResolvedEntity(
        id=spec.id,
        label=spec.label,
        description=spec.description,
        labels=dict(spec.labels),
        descriptions=dict(spec.descriptions),
        primary_key=primary_key,
        label_field=spec.label_field or (parent.label_field if parent else primary_key),
        fields=(parent.fields if parent else ()) + spec.fields,
        attrs=(parent.attrs if parent else ()) + spec.attrs,
        extends=spec.extends,
        lineage=(parent.lineage if parent else ()) + (spec.id,),
        synonyms=dict(spec.synonyms),
    )


# ---------------------------------------------------------------- results and routing


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str  # the id of the target that ran
    row_count: int = Field(ge=0)
    as_of: datetime
    scope: str = ""  # plain-language data scope, e.g. "all sites" or "rows visible to you"
    # the target's display name in the reply language ("Low stock"); the pipeline fills it in
    # (from ``Routable.title``) when a tool leaves it empty
    label: str = ""


class ToolResult(BaseModel):
    rows: list[dict[str, Any]] = []  # rendered as a table; never sent to an LLM
    aggregates: dict[str, int | float] = {}  # computed numbers the narrator may cite
    columns: list[str] = []
    provenance: Provenance
    message: str = ""

    @field_validator("aggregates", mode="before")
    @classmethod
    def _numbers_only(cls, value: Any) -> Any:
        for key, number in dict(value).items():
            if isinstance(number, bool) or not isinstance(number, int | float):
                raise ValueError(f"aggregate '{key}' must be a number")
            if not math.isfinite(number):
                raise ValueError(f"aggregate '{key}' must be finite")
        return value

    def allowed_numbers(self) -> frozenset[int | float]:
        """Numbers the narration may contain; the numeric lock checks narration against this."""
        return frozenset(
            [*self.aggregates.values(), self.provenance.row_count, len(self.rows)]
        )

    def narrator_view(self) -> dict[str, JsonValue]:
        """The only part of a result an LLM narrator may see."""
        return {
            "aggregates": dict(self.aggregates),
            "row_count": self.provenance.row_count,
            "columns": list(self.columns),
            "scope": self.provenance.scope,
            "message": self.message,
        }


class ScopeMention(BaseModel):
    """A constraint the question names, e.g. category *packaging* or warehouse *WH-EAST*."""

    key: str  # binding key: "category:<entity>" or "field:<entity>.<field>" (core.scope)
    value: str  # canonical value: a category's entity id, or an enum value
    text: str  # the words of the question that named it (normalized)
    kind: str = ""  # display name of the constraint in the reply language ("category")
    label: str = ""  # display name of the value in the reply language ("Packaging")
    param: str | None = None  # the target param that applies it
    implied: bool = False  # the target always applies it (``Routable.implied_scope``)
    negated: bool = False  # the question excludes it ("except packaging"); no target can

    @property
    def id(self) -> str:
        """What ``SCOPE_ACK_PARAM`` lists to acknowledge running without this constraint."""
        return f"{self.key}={self.value}"


class ScopeChoice(BaseModel):
    """One way to continue after a scope clarification: run ``target`` explicitly."""

    target: str
    label: str | None = None  # short display label ("Show all categories"); None: description
    params: dict[str, JsonValue] | None = None  # send as explicit_params; None: send none
    unfiltered: bool = False  # runs without the constraints the target cannot apply


class ScopeCheck(BaseModel):
    """What routing found about the constraints a question names, for one routed target."""

    target: str
    mentioned: list[ScopeMention] = []
    bound: list[ScopeMention] = []  # applied: by a param (given or filled now), or implied
    unbound: list[ScopeMention] = []  # the target cannot apply these
    conflicts: list[ScopeMention] = []  # several values named for a param that takes one
    filled: dict[str, JsonValue] = {}  # params filled from the question
    acknowledged: bool = False  # every unbound constraint was acknowledged: runs unfiltered
    choices: list[ScopeChoice] = []  # offered instead of running (unbound or conflicts)


class RouteDecision(BaseModel):
    kind: RouteKind
    target: str | None = None  # tool or workflow name
    params: dict[str, JsonValue] = {}  # raw params; validated later against params_model
    confidence: float = Field(ge=0.0, le=1.0)
    source: RouteSource
    clarify_options: list[str] = []  # target names offered to the user
    reason: str = ""
    scope: ScopeCheck | None = None  # set when the question names a constraint

    @model_validator(mode="after")
    def _check(self) -> "RouteDecision":
        if self.kind in (RouteKind.TOOL, RouteKind.WORKFLOW) and not self.target:
            raise ValueError(f"a {self.kind.value} decision needs a target")
        if self.clarify_options and self.kind is not RouteKind.CLARIFY:
            raise ValueError("clarify_options are only valid on a clarify decision")
        return self


class TraceStep(BaseModel):
    step: str = Field(min_length=1)  # usually one of TRACE_STEPS
    status: TraceStatus
    source: RouteSource | None = None
    facts: dict[str, JsonValue] = {}
    duration_ms: float = Field(default=0.0, ge=0.0)


# ---------------------------------------------------------------- executable declarations

TraceSink = Callable[[TraceStep], None]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _drop(_step: TraceStep) -> None:
    return None


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    principal: Principal
    store: Any = None  # data-access handle; the storage layer defines its protocol
    facts: Mapping[str, Any] = field(default_factory=dict)  # request facts preconditions read
    clock: Callable[[], datetime] = _utc_now
    trace: TraceSink = _drop
    # The loaded registry, read-only, so a target can name its filters in plain words
    # (``core.scope.describe_filters``). The pipeline sets it when it runs a target.
    registry: Any = None

    def now(self) -> datetime:
        return self.clock()

    def emit(self, step: TraceStep) -> None:
        self.trace(step)

    @property
    def language(self) -> str:
        """Reply language of this request, ``en`` or ``zh`` (``facts["language"]``, set by the
        engine from the query). Tools may use it to phrase ``message`` and ``scope``; numbers
        never depend on it."""
        return "zh" if self.facts.get("language") == "zh" else "en"


def _as_tuple(obj: Any, *names: str) -> None:
    for name in names:
        object.__setattr__(obj, name, tuple(getattr(obj, name)))


@dataclass(frozen=True, kw_only=True)
class ToolOutputs:
    """What a target may put in its result: the aggregate keys and the row columns. Both are
    required, so "no rows" is written ``columns=()`` and not left to a default.

    The pipeline compares every result with this declaration and turns any other aggregate key,
    column or row key into an error; nothing the target did not declare reaches the narrator,
    the console or the audit log. Rows stay out of every model's view either way."""

    aggregates: tuple[str, ...]
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        # a plain string stays a string, so ``problems`` can name the missing comma in
        # ``aggregates=("total")`` instead of treating each letter as a key
        for name in ("aggregates", "columns"):
            value = getattr(self, name)
            if isinstance(value, list):
                object.__setattr__(self, name, tuple(value))

    def problems(self, where: str) -> list[str]:
        return [f"{where}: outputs.{kind} must be a tuple of non-empty names"
                for kind, names in (("aggregates", self.aggregates), ("columns", self.columns))
                if not isinstance(names, tuple)
                or not all(isinstance(n, str) and n.strip() for n in names)]

    def undeclared(self, result: ToolResult) -> list[str]:
        """What ``result`` carries beyond this declaration, one phrase per kind (empty when it
        stays inside). Names only, never values. The row key ``ENTITY_KEY`` is the framework's,
        not a column, so it needs no declaration."""
        row_keys = {key for row in result.rows for key in row} - {ENTITY_KEY}
        found = {"aggregates": set(result.aggregates) - set(self.aggregates),
                 "columns": (set(result.columns) | row_keys) - set(self.columns)}
        return [f"{kind} {sorted(names)}" for kind, names in found.items() if names]


@dataclass(frozen=True, kw_only=True)
class Routable:
    """What the router sees: a Tool or a Workflow. Names share one namespace.

    ``description`` says what the target does. A plugin writes it as a plain string (the same
    text in every language) or ``{"en": ..., "zh": ...}``, like an entity's description. After
    construction ``description`` holds the English text, for code that does not care about the
    language, and ``descriptions`` the text of every language: routing evidence reads them all
    (core.routing.semantic), a confirmation's summary reads the request's (``description_in``).

    ``roles``, ``effect``, ``approval``, ``outputs`` and ``explorable`` have no default, so a
    plugin cannot take one by omission; the inputs are the params model of a Tool (the input
    model of a Workflow's first step). The core owns the policy that ties them together
    (``problems``): the role/effect table, the approval an effect needs, and the outputs the
    pipeline enforces on every result.
    """

    route_kind: ClassVar[RouteKind]

    name: str
    description: str
    # every language's description; filled from ``description``, so a plugin rarely sets it
    descriptions: Mapping[str, str] = field(default_factory=dict)
    roles: tuple[str, ...]  # must be non-empty: no open default
    effect: ResourceEffect  # required: a forgotten 'write' must not default to 'read'
    # Required, like ``effect``: whether a run first waits for a person's one-time approval, a
    # grant bound to the user, the target and the params. The pipeline issues and consumes grants
    # by this field. A write must say "required" and a read "none"; an external call says which.
    approval: Approval
    # Required: the aggregate keys and row columns a result may carry (``ToolOutputs``).
    outputs: ToolOutputs
    # Required: whether the open-analysis branch may offer this target to a model. False keeps it
    # out of every exploration run, whatever the deployment's allowlist says
    # (core.explore.exposure).
    explorable: bool
    keywords: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()  # natural-language queries; feed rule arm, BM25, LLM list
    route_hint: str = ""  # disambiguation note for the LLM arm
    requires: tuple[str, ...] = ()  # precondition names
    # Deterministic params from the raw query, used when the rule arm (or an explicit call
    # without params) picks this target. Output is raw; the pipeline still validates it.
    param_extractor: Callable[[str], dict[str, Any]] | None = None
    # Human names for aggregate keys, per reply language: {"en": {"late_count": "Late orders"},
    # "zh": {...}}. Template narration and the narrator's view use them; a key without a label
    # is humanized ("class_a_count" -> "Class A count").
    aggregate_labels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    # Constraints this target always applies, keyed like a param binding (core.params):
    # {"category:sku": "cold_chain_item"}. A question naming that value needs no filter; one
    # naming another value of the same kind is a scope gap (core.routing.scope_guard).
    implied_scope: Mapping[str, str] = field(default_factory=dict)
    # Display name per reply language, for sentences the system writes about this target
    # ({"en": "Low stock", "zh": "低库存"}). Unset: the humanized name ("low_stock" ->
    # "Low stock"). Never used for routing.
    title: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _as_tuple(self, "roles", "keywords", "examples", "requires")
        if self.effect in _values(ResourceEffect):
            object.__setattr__(self, "effect", ResourceEffect(self.effect))
        if isinstance(self.approval, str) and self.approval in _values(Approval):
            object.__setattr__(self, "approval", Approval(self.approval))
        labels = self.aggregate_labels
        if isinstance(labels, Mapping) and all(isinstance(t, Mapping) for t in labels.values()):
            object.__setattr__(self, "aggregate_labels",
                               {lang: dict(table) for lang, table in labels.items()})
        for name in ("implied_scope", "title"):
            if isinstance(getattr(self, name), Mapping):
                object.__setattr__(self, name, dict(getattr(self, name)))
        self._split_description()

    def _split_description(self) -> None:
        """``description`` as written becomes the English text it keeps and the table of every
        language beside it: a mapping is the table; a plain string is the text of every
        language (see ``_own_table`` for a string that comes with a table)."""
        text, table = self.description, self.descriptions
        if isinstance(text, Mapping):
            english = text.get(DEFAULT_LANGUAGE)
            text, table = (english if isinstance(english, str) else ""), dict(text)
        elif isinstance(text, str) and _own_table(text, table):
            table = {lang: text.strip() for lang in LANGUAGES} if text.strip() else {}
        object.__setattr__(self, "description", text)
        if isinstance(table, Mapping):
            object.__setattr__(self, "descriptions", _DescriptionTable(table))

    def description_in(self, language: str | None) -> str:
        """The description in ``language``; the English one when it has none there."""
        return localized(self.descriptions, language) or self.description

    def allows(self, principal: Principal) -> bool:
        """Whether one of the principal's roles is listed in ``roles`` (admin always is) and
        ``ROLE_EFFECTS`` lets that same role run this target's effect."""
        return any((role is Role.ADMIN or role in self.roles) and self.effect in ROLE_EFFECTS[role]
                   for role in principal.roles)

    def problems(self) -> list[str]:
        where = f"{self.route_kind.value} '{self.name}'"
        out = []
        if not _ID_RE.match(self.name):
            out.append(f"{where}: name must match {ID_PATTERN}")
        out.extend(_description_problems(where, self.description, self.descriptions))
        if not self.roles:
            out.append(f"{where}: roles is empty; every target must declare who may use it")
        unknown = sorted(str(r) for r in set(self.roles) - ROLES)
        if unknown:
            out.append(f"{where}: unknown role(s) {unknown}; known roles are {sorted(ROLES)}")
        if self.effect not in _values(ResourceEffect):
            out.append(f"{where}: unknown effect {self.effect!r}")
        out.extend(_policy_problems(self, where))
        if self.param_extractor is not None and not callable(self.param_extractor):
            out.append(f"{where}: param_extractor is not callable")
        out.extend(_label_problems(where, self.aggregate_labels))
        implied = self.implied_scope
        if not isinstance(implied, Mapping) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in implied.items()):
            out.append(f"{where}: implied_scope must map a binding key to a value")
        title = self.title
        if not isinstance(title, Mapping) or not all(
                lang in LANGUAGES and isinstance(text, str) and text.strip()
                for lang, text in title.items()):
            out.append(f"{where}: title must map a language ({list(LANGUAGES)}) to a non-empty "
                       "display name")
        return out


def _policy_problems(target: Routable, where: str) -> list[str]:
    """Problems of what the core owns policy for: which roles an effect allows, which approval
    an effect needs, what a result may carry, who may offer the target to a model."""
    effect, approval = target.effect, target.approval
    out = []
    if effect in _values(ResourceEffect):
        for role in sorted(r for r in set(target.roles) & ROLES
                           if effect not in ROLE_EFFECTS[Role(r)]):
            allowed = sorted(e.value for e in ROLE_EFFECTS[Role(role)])
            out.append(f"{where}: role {role!r} may not run a {effect.value} target; "
                       f"core policy lets {role} run only {allowed}")
    if approval not in _values(Approval):
        out.append(f"{where}: unknown approval {approval!r}; use one of "
                   f"{sorted(_values(Approval))}")
    elif effect is ResourceEffect.WRITE and approval is not Approval.REQUIRED:
        out.append(f"{where}: a write target must declare approval='required': it always "
                   "waits for a person's one-time approval")
    elif effect is ResourceEffect.READ and approval is not Approval.NONE:
        out.append(f"{where}: a read target must declare approval='none': it changes nothing, "
                   "so there is nothing to approve")
    if not isinstance(target.explorable, bool):
        out.append(f"{where}: explorable must be True or False")
    elif (target.explorable and effect is ResourceEffect.EXTERNAL
          and approval is not Approval.REQUIRED):
        out.append(f"{where}: an explorable external target must declare approval='required': "
                   "open analysis may not make an outbound call on its own")
    if not isinstance(target.outputs, ToolOutputs):
        out.append(f"{where}: outputs must be ToolOutputs(aggregates=(...), columns=(...))")
    else:
        out.extend(target.outputs.problems(where))
        out.extend(_unlisted_label_problems(where, target.aggregate_labels,
                                            target.outputs.aggregates))
    return out


def _unlisted_label_problems(where: str, labels: Any, declared: Any) -> list[str]:
    """A label for an aggregate the target does not declare names a figure it can never emit.
    ``_label_problems`` reports a malformed table, so this one only reads a well-formed one."""
    if not isinstance(labels, Mapping) or not isinstance(declared, tuple):
        return []
    named = {key for table in labels.values() if isinstance(table, Mapping) for key in table}
    return [f"{where}: aggregate_labels names {key!r}, which outputs.aggregates does not declare"
            for key in sorted(named - set(declared))]


def _extra_key_problems(where: str, model: Any, what: str) -> list[str]:
    """A params model that ignores or keeps unknown keys lets a misspelled filter run anyway,
    wider than the user asked: it must forbid them."""
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return []  # not a model: a Tool reports that on its own
    if model.model_config.get("extra") == "forbid":
        return []
    return [f"{where}: {what} {model.__name__} accepts unknown keys; add "
            "model_config = ConfigDict(extra='forbid') so a misspelled param is refused "
            "instead of ignored"]


class _DescriptionTable(dict):
    """A target's description in every language, as ``Routable`` filled it in. Its type tells
    a copy made with ``dataclasses.replace`` (which hands this table back) from a table a
    plugin declared."""


def _own_table(text: str, table: Any) -> bool:
    """Whether a plain-string description makes its table: unless a table comes with it that
    a plugin declared (kept; ``problems`` checks that the two agree) or that a copy handed
    back with the same English text. A copy given a new string (``replace(tool,
    description=...)``) gets that string in every language, as any plain string does."""
    if not table:
        return True
    if isinstance(table, _DescriptionTable):
        english = table.get(DEFAULT_LANGUAGE)
        return (english.strip() if isinstance(english, str) else "") != text.strip()
    return False


def _description_problems(where: str, english: Any, table: Any) -> list[str]:
    """Problems of a target's description: like an entity's, except that a target must have
    one (English, the fallback of every other language)."""
    if not isinstance(english, str) or not isinstance(table, Mapping) or not all(
            isinstance(text, str) for text in table.values()):
        return [f"{where}: description must be a string or map a language ({list(LANGUAGES)}) "
                "to a text"]
    if not english.strip() and not table:
        return [f"{where}: description is empty"]
    out = _text_problems(where, "description", table, required=True)
    declared = table.get(DEFAULT_LANGUAGE)
    if isinstance(declared, str) and declared.strip() != english.strip():
        out.append(f"{where}: description and descriptions[{DEFAULT_LANGUAGE!r}] differ; "
                   "declare the description once, as a string or per language")
    return out


def _label_problems(where: str, labels: Any) -> list[str]:
    if not isinstance(labels, Mapping):
        return [f"{where}: aggregate_labels must map a language to {{aggregate key: label}}"]
    out = []
    for language, table in labels.items():
        if language not in LANGUAGES:
            out.append(f"{where}: aggregate_labels language {language!r} is not supported; "
                       f"use one of {list(LANGUAGES)}")
        elif not isinstance(table, Mapping) or not all(
                isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in table.items()):
            out.append(f"{where}: aggregate_labels[{language!r}] must map aggregate keys to "
                       "non-empty labels")
    return out


@dataclass(frozen=True, kw_only=True)
class Tool(Routable):
    route_kind: ClassVar[RouteKind] = RouteKind.TOOL

    params_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], ToolResult]  # (ctx, params_model instance)
    entity: str | None = None  # entity id this tool operates on

    def parse_params(self, raw: Mapping[str, Any]) -> BaseModel:
        return self.params_model.model_validate(dict(raw))

    def problems(self) -> list[str]:
        out = super().problems()
        if not (isinstance(self.params_model, type) and issubclass(self.params_model, BaseModel)):
            out.append(f"tool '{self.name}': params_model must be a Pydantic model class")
        out.extend(_extra_key_problems(f"tool '{self.name}'", self.params_model, "params_model"))
        if not callable(self.handler):
            out.append(f"tool '{self.name}': handler is not callable")
        return out


@dataclass(frozen=True, kw_only=True)
class Step:
    id: str
    kind: StepKind
    description: str
    fn: Callable[[ToolContext, Any], Any]  # (ctx, previous step output or workflow input)
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None


@dataclass(frozen=True, kw_only=True)
class Workflow(Routable):
    """Ordered steps. Step-kind rule (an LLM never produces the numbers):

    * at least one step is deterministic;
    * an ``llm`` step may only be the first step (understand the request) or the last step
      (narrate the result); every middle step is deterministic;
    * a first ``llm`` step that is followed by more steps must declare ``output_model`` so its
      output is schema-gated before deterministic code consumes it.
    """

    route_kind: ClassVar[RouteKind] = RouteKind.WORKFLOW

    steps: tuple[Step, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        _as_tuple(self, "steps")

    def problems(self) -> list[str]:
        out = super().problems()
        where = f"workflow '{self.name}'"
        if StepKind.DETERMINISTIC not in [s.kind for s in self.steps]:
            out.append(f"{where}: needs at least one deterministic step")
        last = len(self.steps) - 1
        seen: set[str] = set()
        for i, step in enumerate(self.steps):
            if step.id in seen or not _ID_RE.match(step.id):
                out.append(f"{where}: step id '{step.id}' is invalid or duplicated")
            seen.add(step.id)
            is_llm = step.kind == StepKind.LLM
            if step.kind not in _values(StepKind):
                out.append(f"{where}: step '{step.id}' has unknown kind {step.kind!r}")
            elif is_llm and 0 < i < last:
                out.append(f"{where}: llm step '{step.id}' is a middle step; llm steps may "
                           "only be first or last")
            elif is_llm and i == 0 and last > 0 and step.output_model is None:
                out.append(f"{where}: first llm step '{step.id}' must declare output_model")
            if not callable(step.fn):
                out.append(f"{where}: step '{step.id}' fn is not callable")
        first = self.steps[0].input_model if self.steps else None  # the workflow's params
        if first is not None:
            out.extend(_extra_key_problems(where, first, "input_model of the first step"))
        return out


@dataclass(frozen=True, kw_only=True)
class Precondition:
    name: str
    description: str  # user-facing: what is missing and how to get it
    check: Callable[[ToolContext], bool]

    def problems(self) -> list[str]:
        out = []
        if not _ID_RE.match(self.name):
            out.append(f"precondition '{self.name}': name must match {ID_PATTERN}")
        if not callable(self.check):
            out.append(f"precondition '{self.name}': check is not callable")
        return out


@dataclass(frozen=True, kw_only=True)
class Plugin:
    name: str
    version: str
    entities: tuple[EntitySpec, ...] = ()
    tools: tuple[Tool, ...] = ()
    workflows: tuple[Workflow, ...] = ()
    preconditions: tuple[Precondition, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _as_tuple(self, "entities", "tools", "workflows", "preconditions")

    def problems(self) -> list[str]:
        out = []
        if not _ID_RE.match(self.name):
            out.append(f"plugin '{self.name}': name must match {ID_PATTERN}")
        if not self.version.strip():
            out.append(f"plugin '{self.name}': version is empty")
        return out
