"""Constraint vocabulary: the categories and scope-key values a question can name.

Built once per registry (``Registry.vocabulary``) from declarations only; core holds no domain
words. A *binding key* names one kind of constraint:

* ``category:<entity>``: the entities that extend ``<entity>`` (directly or not). Each is named
  by its id, its label in every language and its ``EntitySpec.synonyms``, and is shown by its
  label in the reply language.
* ``field:<entity>.<field>``: an enum field declared with ``scope_key=True`` by ``<entity>``.
  Each value is named by its ``FieldSpec.value_synonyms`` and, when it looks like a code
  (``WH-EAST``, ``EOL``: a digit, a separator, or all capitals), by itself. A value that is a
  plain word (``open``) counts only through declared phrases ("open orders"), because the bare
  word is too often something else ("open the report").

Matching (``Vocabulary.find``) is conservative on purpose, because a false mention makes the
console ask instead of answer:

* text is NFKC folded and punctuation becomes a space, so "Spare-Part", "spare part" and
  "spare_part" read the same; a trailing plural "s" is folded on both sides ("spare parts"),
  except in a one-word value synonym, which matches only as written: folded, "drafts" would be
  the bare value "draft", and "draft a reorder plan" would name a PO status;
* Latin phrases match whole words in sequence, case-insensitively, never inside a word. A
  phrase is never a mention if its words are all shorter than three letters (the article "a",
  the class "A", "wh") or are all stopwords ("is", "the"), so a short value needs a declared
  phrase such as "class A". Identifiers ("SKU-10042") never match: no phrase is a bare code;
* a phrase with Chinese characters matches as a substring (Chinese has no word breaks), never
  with a Latin letter or digit glued to its Latin end ("data类" does not hold "a类"), and needs
  at least two letters;
* overlapping matches keep the longest; one phrase may name values of two keys (kept as
  alternatives, routing picks the one the target applies), never two values of one key (a
  registry problem);
* a mention right after an exclusion word ("except", "excluding", "other than", "除了", "非")
  or before "以外"/"之外" is *negated*: no target applies exclusions, so routing asks.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contract import FieldSpec, FieldType, ResolvedEntity
from .errors import InputProblem
from .i18n import LANGUAGES, as_language, say
from .narration import humanize_key
from .params import binds, field_name, param_label, params_model
from .text import STOPWORDS, fold

CATEGORY_PREFIX = "category:"
FIELD_PREFIX = "field:"
MIN_WORD = 3  # a Latin phrase needs a word at least this long
MIN_CJK = 2  # a phrase with Chinese characters needs at least this many letters

_CJK = "㐀-䶿一-鿿豈-﫿"
_CJK_RE = re.compile(f"[{_CJK}]")
_LATIN = f"[^\\W_{_CJK}]"  # a letter or digit that is not CJK
_RUN = re.compile(r"[^\W_]+")
_SEGMENT = re.compile(f"[{_CJK}]+|[^{_CJK}]+")
_NON_WORD = re.compile(r"[\W_]+")

_NEGATE_WORDS = frozenset({"not", "non", "except", "excluding", "exclude", "without", "besides"})
_NEGATE_PAIRS = frozenset({("other", "than"), ("apart", "from"), ("aside", "from")})
_GLUE = frozenset({"for", "in", "of", "from", "the", "a", "an", "any", "our"})  # "not for the"
_NEGATE_CJK_BEFORE = ("除了", "除", "非", "不含", "不包括", "排除", "不是")
_NEGATE_CJK_AFTER = ("以外", "之外")


def category_key(entity_id: str) -> str:
    return f"{CATEGORY_PREFIX}{entity_id}"


def field_key(entity_id: str, field_id: str) -> str:
    return f"{FIELD_PREFIX}{entity_id}.{field_id}"


def _flat(text: str) -> str:
    """NFKC text with every run of punctuation, space or underscore turned into one space.
    Case is kept (Latin words are folded per token; CJK patterns ignore case)."""
    return _NON_WORD.sub(" ", unicodedata.normalize("NFKC", text or ""))


def code_like(value: str) -> bool:
    """A value that reads as a code rather than a word: it has a digit or a separator, or is
    all capitals ("WH-EAST", "EOL"). Only such values name themselves in a question."""
    return any(c.isdigit() or c in "-_./" for c in value) or (value.isalpha() and
                                                               value.isupper())


def _lower_first(text: str) -> str:
    """'Warehouse' -> 'warehouse', but 'SKU class' stays (an acronym keeps its case)."""
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def plural(word: str) -> str:
    """English plural of a kind name, for "show all categories"."""
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


# ---------------------------------------------------------------- vocabulary


@dataclass(frozen=True)
class Kind:
    """One kind of constraint: the values a question may name for one binding key."""

    key: str
    names: Mapping[str, str]  # language -> display name of the constraint ("category", "仓库")
    labels: Mapping[str, Mapping[str, str]]  # value -> language -> display name of the value

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(self.labels)

    def name(self, language: str | None) -> str:
        return self.names.get(as_language(language) or "en") or self.names.get("en", self.key)

    def label(self, value: str, language: str | None) -> str:
        table = self.labels.get(value, {})
        return table.get(as_language(language) or "en") or table.get("en", value)


@dataclass(frozen=True)
class Mention:
    """Words of a question that name a constraint. ``options`` are the (key, value) pairs those
    words name, in declaration order."""

    text: str
    start: int
    end: int
    options: tuple[tuple[str, str], ...]
    negated: bool = False


@dataclass(frozen=True)
class _Term:
    key: str
    value: str
    words: tuple[str, ...] = ()  # folded Latin words; () for a phrase with CJK
    pattern: re.Pattern[str] | None = None
    exact: bool = False  # ``words`` are as written (case folded only) and match only so

    def matches(self, window: list["_Token"]) -> bool:
        return len(window) == len(self.words) and all(
            not t.cjk and (t.raw.casefold() if self.exact else t.text) == w
            for t, w in zip(window, self.words, strict=True))


@dataclass(frozen=True)
class _Token:
    text: str  # folded and case folded for Latin, as written for CJK
    raw: str
    start: int
    end: int
    cjk: bool


def _tokens(flat: str) -> list[_Token]:
    out = []
    for run in _RUN.finditer(flat):
        for seg in _SEGMENT.finditer(run.group()):
            start, end, text = run.start() + seg.start(), run.start() + seg.end(), seg.group()
            cjk = bool(_CJK_RE.match(text))
            out.append(_Token(text if cjk else fold(text.casefold()), text, start, end, cjk))
    return out


def _compile(phrase: str, *, exact: bool = False
             ) -> tuple[tuple[str, ...], re.Pattern[str] | None] | None:
    """A phrase as a matcher, or ``None`` when it could never be a safe mention. ``exact``:
    Latin words are kept as written (case folded), not plural folded."""
    flat = _flat(phrase).strip()
    if not flat:
        return None
    if _CJK_RE.search(flat):
        if sum(1 for c in flat if not c.isspace()) < MIN_CJK:
            return None
        segments = _SEGMENT.findall(flat.replace(" ", ""))
        body = r"\s?".join(re.escape(s) for s in segments)
        if not _CJK_RE.match(segments[0]):
            body = f"(?<!{_LATIN})" + body
        if not _CJK_RE.match(segments[-1][-1]):
            body += f"(?!{_LATIN})"
        return (), re.compile(body, re.IGNORECASE)
    words = flat.split()
    if max(len(w) for w in words) < MIN_WORD or all(w.casefold() in STOPWORDS for w in words):
        return None
    return tuple(w.casefold() if exact else fold(w.casefold()) for w in words), None


class Vocabulary:
    """Every phrase that names a constraint, with the kinds they belong to."""

    def __init__(self, kinds: Mapping[str, Kind], terms: Iterable[_Term]) -> None:
        self.kinds: Mapping[str, Kind] = MappingProxyType(dict(kinds))
        self._latin: dict[str, list[_Term]] = {}  # by first folded word
        self._exact: dict[str, list[_Term]] = {}  # by first word as written (case folded)
        self._cjk: list[_Term] = []
        for term in terms:
            if term.pattern is not None:
                self._cjk.append(term)
            else:
                index = self._exact if term.exact else self._latin
                index.setdefault(term.words[0], []).append(term)

    def __len__(self) -> int:
        return len(self._cjk) + sum(len(t) for t in [*self._latin.values(),
                                                     *self._exact.values()])

    def values(self, key: str) -> tuple[str, ...]:
        kind = self.kinds.get(key)
        return kind.values if kind else ()

    def name(self, key: str, language: str | None) -> str:
        kind = self.kinds.get(key)
        return kind.name(language) if kind else key

    def label(self, key: str, value: str, language: str | None) -> str:
        kind = self.kinds.get(key)
        return kind.label(value, language) if kind else value

    def find(self, text: str) -> list[Mention]:
        """Mentions in order of appearance (longest match wins an overlap)."""
        flat = _flat(text)
        tokens = _tokens(flat)
        hits: dict[tuple[int, int], list[tuple[str, str]]] = {}

        def hit(span: tuple[int, int], option: tuple[str, str]) -> None:
            options = hits.setdefault(span, [])
            if option not in options:
                options.append(option)

        for i, token in enumerate(tokens):
            if token.cjk:
                continue
            for term in [*self._latin.get(token.text, ()),
                         *self._exact.get(token.raw.casefold(), ())]:
                window = tokens[i:i + len(term.words)]
                if term.matches(window):
                    hit((token.start, window[-1].end), (term.key, term.value))
        for term in self._cjk:
            for match in term.pattern.finditer(flat):
                hit(match.span(), (term.key, term.value))
        chosen: list[tuple[int, int]] = []
        for start, end in sorted(hits, key=lambda s: (s[0] - s[1], s[0])):
            if not any(start < e and s < end for s, e in chosen):
                chosen.append((start, end))
        return [Mention(text=flat[s:e], start=s, end=e, options=tuple(hits[s, e]),
                        negated=_negated(flat, tokens, s, e)) for s, e in sorted(chosen)]

    def canonical(self, key: str, raw: Any) -> str | None:
        """The value of ``key`` that ``raw`` names as a whole (an id, a label in any case, or a
        synonym), else ``None``. Turns "Packaging" or "spare parts" into the category id."""
        kind = self.kinds.get(key)
        if kind is None or not isinstance(raw, str) or not raw.strip():
            return None
        text = raw.strip()
        for value in kind.values:
            if value == text or value.casefold() == text.casefold():
                return value
        whole = _flat(text).strip()
        for mention in self.find(text):
            if mention.text == whole and not mention.negated:
                for option_key, value in mention.options:
                    if option_key == key:
                        return value
        return None


def _negated(flat: str, tokens: list[_Token], start: int, end: int) -> bool:
    words: list[str] = []  # the Latin words right before the mention, nearest first
    for token in reversed([t for t in tokens if t.end <= start]):
        if token.cjk:
            break
        words.append(token.raw.casefold())
        if len(words) == 4:
            break
    while len(words) > 1 and words[0] in _GLUE:  # "not for the packaging"
        words.pop(0)
    if words and (words[0] in _NEGATE_WORDS or tuple(reversed(words[:2])) in _NEGATE_PAIRS):
        return True
    head = flat[:start].replace(" ", "")
    tail = flat[end:].replace(" ", "")
    return head.endswith(_NEGATE_CJK_BEFORE) or tail.startswith(_NEGATE_CJK_AFTER)


def _display(base: str, phrases: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """English: ``base``; another language: its first declared phrase, else ``base``."""
    out = {}
    for language in LANGUAGES:
        declared = list(phrases.get(language, ())) if language != "en" else []
        out[language] = declared[0] if declared else base
    return out


def own_fields(entity: ResolvedEntity,
               entities: Mapping[str, ResolvedEntity]) -> tuple[FieldSpec, ...]:
    """The fields ``entity`` declares itself (a child adds attrs after its parent's)."""
    if entity.extends is None:
        return entity.all_fields
    parent = entities.get(entity.extends)
    return entity.attrs[len(parent.attrs):] if parent is not None else entity.attrs


def build_vocabulary(entities: Mapping[str, ResolvedEntity]) -> tuple["Vocabulary", list[str]]:
    """The vocabulary of ``entities`` and the problems of its declared phrases."""
    problems: list[str] = []
    names: dict[str, dict[str, str]] = {}
    labels: dict[str, dict[str, dict[str, str]]] = {}
    terms: dict[tuple[str, str, Any], _Term] = {}
    owners: dict[Any, tuple[str, dict[str, set[str]]]] = {}  # matcher -> (phrase, key -> values)

    def add(key: str, value: str, phrase: str, declared_by: str | None, *,
            as_written: bool = False) -> None:
        """``as_written``: a one-word Latin phrase matches only as written (no plural
        folding)."""
        exact = as_written and len(_flat(phrase).split()) == 1
        compiled = _compile(phrase, exact=exact)
        if compiled is None:
            if declared_by is not None:
                problems.append(f"{declared_by}: phrase {phrase!r} can never be matched (words "
                                "of one or two letters, stopwords only, or one Chinese "
                                "character)")
            return
        words, pattern = compiled
        exact = exact and pattern is None
        identity = (words, exact) if words else pattern.pattern
        owners.setdefault(identity, (phrase, {}))[1].setdefault(key, set()).add(value)
        terms.setdefault((key, value, identity), _Term(key, value, words, pattern, exact))

    category_name = {lang: say("scope_kind_category", lang) for lang in LANGUAGES}
    for entity in entities.values():
        for ancestor in entity.lineage[:-1]:
            key = category_key(ancestor)
            names.setdefault(key, category_name)
            # a category reads as the console names it: its label in each language
            labels.setdefault(key, {})[entity.id] = {lang: entity.label_in(lang)
                                                     for lang in LANGUAGES}
            for phrase in dict.fromkeys((entity.id.replace("_", " "), entity.label,
                                         *entity.labels.values())):
                add(key, entity.id, phrase, None)
            for phrases in entity.synonyms.values():
                for phrase in phrases:
                    add(key, entity.id, phrase, f"entity '{entity.id}' synonym")
    for entity in entities.values():
        for spec in own_fields(entity, entities):
            if not spec.scope_key or spec.type is not FieldType.ENUM:
                continue
            key = field_key(entity.id, spec.id)
            names[key] = {lang: _lower_first(field_name(spec, lang)) for lang in LANGUAGES}
            table = labels.setdefault(key, {})
            for value in spec.enum_values:
                synonyms = spec.value_synonyms.get(value, {})
                table[value] = _display(value, synonyms)
                if code_like(value):
                    add(key, value, value, None)
                for phrases in synonyms.values():
                    for phrase in phrases:
                        add(key, value, phrase,
                            f"field '{entity.id}.{spec.id}' value {value!r} synonym",
                            as_written=True)
    for phrase, by_key in owners.values():
        for key, values in by_key.items():
            if len(values) > 1:
                problems.append(f"scope phrase {phrase!r} names more than one value of {key}: "
                                f"{sorted(values)}")
    kinds = {key: Kind(key, names[key], labels[key]) for key in labels}
    return Vocabulary(kinds, terms.values()), list(dict.fromkeys(problems))


# ---------------------------------------------------------------- bindings


def valid_values(key: str, entities: Mapping[str, ResolvedEntity]) -> tuple[str, ...]:
    """Every value the constraint ``key`` has (sorted category ids, or enum values)."""
    if key.startswith(CATEGORY_PREFIX):
        root = key[len(CATEGORY_PREFIX):]
        return tuple(sorted(e.id for e in entities.values() if root in e.lineage[:-1]))
    entity_id, _, field_id = key[len(FIELD_PREFIX):].partition(".")
    entity = entities.get(entity_id)
    for spec in (entity.all_fields if entity else ()):
        if spec.id == field_id:
            return spec.enum_values
    return ()


def parse_binding(raw: Any, entities: Mapping[str, ResolvedEntity]) -> tuple[str | None, str]:
    """(binding key, "") for a valid declaration, else (None, problem)."""
    shape = "a string 'category:<entity>' or 'field:<entity>.<field>'"
    if not isinstance(raw, str):
        return None, f"binds must be {shape}"
    if raw.startswith(CATEGORY_PREFIX):
        entity_id = raw[len(CATEGORY_PREFIX):]
        if entity_id not in entities:
            return None, f"binds {raw!r}: unknown entity '{entity_id}'"
        return raw, ""
    if not raw.startswith(FIELD_PREFIX) or "." not in raw:
        return None, f"binds {raw!r}: must be {shape}"
    entity_id, _, field_id = raw[len(FIELD_PREFIX):].partition(".")
    entity = entities.get(entity_id)
    if entity is None:
        return None, f"binds {raw!r}: unknown entity '{entity_id}'"
    spec = next((f for f in own_fields(entity, entities) if f.id == field_id), None)
    if spec is None:
        owner = next((a for a in entity.lineage if a in entities and any(
            f.id == field_id for f in own_fields(entities[a], entities))), None)
        hint = f"; bind '{field_key(owner, field_id)}'" if owner else ""
        return None, f"binds {raw!r}: '{entity_id}' declares no field '{field_id}'{hint}"
    if not spec.scope_key:
        return None, f"binds {raw!r}: field '{field_id}' is not declared with scope_key=True"
    return raw, ""


def target_bindings(target: Any, entities: Mapping[str, ResolvedEntity]
                    ) -> tuple[dict[str, str], list[str]]:
    """(param -> binding key, problems) of one target, its ``implied_scope`` checked too."""
    where = f"{target.route_kind.value} '{target.name}'"
    found: dict[str, str] = {}
    problems = []
    if params_model(target) is not None:
        for param, raw in binds(target).items():
            key, problem = parse_binding(raw, entities)
            if key is None:
                problems.append(f"{where}: param '{param}' {problem}")
            elif key in found.values():
                other = next(p for p, k in found.items() if k == key)
                problems.append(f"{where}: params '{other}' and '{param}' both bind {key}")
            else:
                found[param] = key
    implied = target.implied_scope if isinstance(target.implied_scope, Mapping) else {}
    for raw, value in implied.items():
        key, problem = parse_binding(raw, entities)
        if key is None:
            problems.append(f"{where}: implied_scope {problem}")
        elif value not in valid_values(key, entities):
            problems.append(f"{where}: implied_scope {key} has no value {value!r}")
        elif key in found.values():
            problems.append(f"{where}: implied_scope {key} is also bound by a param")
    return found, problems


def describe_filters(registry: Any, target: str, values: Mapping[str, Any],
                     language: str | None) -> str:
    """The params a target ran with, as a reader says them: "Category: Packaging, Warehouse:
    WH-EAST" / "类别：包装材料，仓库：WH-EAST" ("" when none is set). For a provenance scope.

    A bound param is named by its constraint and its value by the value's label (a category's
    display name; an enum value's label, except a code such as WH-EAST or A, which stays as
    is). Any other param is named by its field label and keeps its value verbatim (ids). With
    no registry (a bare context outside the pipeline) names are humanized keys."""
    zh = as_language(language) == "zh"
    bindings = dict(getattr(registry, "bindings", {}).get(target, {}))
    vocabulary: Vocabulary | None = getattr(registry, "vocabulary", None)
    known = {*getattr(registry, "tools", {}), *getattr(registry, "workflows", {})}
    routable = registry.target(target) if target in known else None
    parts = []
    for param, value in values.items():
        if value is None:
            continue
        key = bindings.get(param)
        if key is not None and vocabulary is not None:
            name = vocabulary.name(key, language)
            shown = str(value) if key.startswith(FIELD_PREFIX) and code_like(str(value)) \
                else vocabulary.label(key, str(value), language)
        else:
            name = humanize_key(param) if routable is None else param_label(
                routable, param, language, getattr(registry, "entities", None))
            shown = ", ".join(map(str, value)) if isinstance(value, list | tuple) else str(value)
        if not zh:
            name = name[:1].upper() + name[1:]
        parts.append(f"{name}：{shown}" if zh else f"{name}: {shown}")
    return ("，" if zh else ", ").join(parts)


def check_bound_values(registry: Any, target: Any, values: Mapping[str, Any],
                       language: str | None) -> None:
    """Raise InputProblem when a bound param holds a value its constraint does not have (an
    unknown category, a category of another family). The message lists the known values."""
    bindings = getattr(registry, "bindings", {}).get(target.name, {})
    vocabulary = getattr(registry, "vocabulary", None)
    for param, key in bindings.items():
        value = values.get(param)
        if value is None:
            continue
        allowed = valid_values(key, registry.entities)
        if value in allowed:
            continue
        kind = vocabulary.name(key, language) if vocabulary else key
        known = ", ".join(f"{v} ({vocabulary.label(key, v, language)})" if vocabulary else v
                          for v in allowed) or say("none", language)
        raise InputProblem(say("scope_unknown_value", language, value=value, kind=kind,
                               known=known))
