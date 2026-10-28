"""Narration: model narration behind the numeric lock, with a deterministic template fallback.

The template is code output built only from the narrator view; every number in it is copied
from the view, so it does not go through the lock. Model narration always does.

The view a narrator gets is ``ToolResult.narrator_view()`` plus two presentation keys:
``language`` (the reply language) and ``labels`` (a readable name for each aggregate key, from
the target's ``aggregate_labels`` or humanized from the key). Neither carries row data.

Two of its strings are written by tool code from stored data and request values
(``DATA_TEXT_KEYS``): ``message`` (create_po's names the supplier) and ``scope`` (it names the
filter values). Everything else is a number or comes from a declaration. A model sees those
two fenced as untrusted data (``fenced_view``); the template and the console show them as they
are.
"""

import math
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, JsonValue

from .contract import ToolResult
from .guards.egress import wrap_untrusted
from .i18n import DEFAULT_LANGUAGE, as_language, say, scope_clause

if TYPE_CHECKING:  # runtime import would cycle through the providers package
    from twotrack.providers.base import Provider

    from .ports import NumericLock


class Narration(BaseModel):
    text: str
    stripped: int = 0  # sentences the numeric lock removed
    source: Literal["model", "template"]


def format_number(value: int | float) -> str:
    """Exact, lock-friendly formatting: integral floats lose the '.0', others keep full repr."""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


# ---------------------------------------------------------------- labels

_ACRONYMS = {"sku": "SKU", "skus": "SKUs", "po": "PO", "pos": "POs", "eta": "ETA", "id": "ID",
             "ids": "IDs", "abc": "ABC", "wh": "WH", "rop": "ROP", "uom": "UoM", "kpi": "KPI"}
_UNITS = {"days": "days", "hours": "hours", "minutes": "minutes", "weeks": "weeks",
          "months": "months", "pct": "%", "percent": "%"}
_SPLIT = re.compile(r"[_\s]+")


def humanize_key(key: str) -> str:
    """A readable label for an aggregate key that has none: ``class_a_count`` ->
    ``Class A count``, ``po_count`` -> ``PO count``, ``lead_time_days`` -> ``Lead time
    (days)``. A trailing unit word becomes a parenthesised unit; single letters are classes."""
    words = [w for w in _SPLIT.split(key.strip()) if w]
    if not words:
        return key
    unit = _UNITS.get(words[-1].lower()) if len(words) > 1 else None
    if unit:
        words = words[:-1]
    out = []
    for word in words:
        lower = word.lower()
        if lower in _ACRONYMS:
            out.append(_ACRONYMS[lower])
        elif len(word) == 1 and word.isalpha():
            out.append(word.upper())
        else:
            out.append(lower)
    text = " ".join(out)
    text = text[:1].upper() + text[1:]
    return f"{text} ({unit})" if unit else text


def aggregate_label(key: str, language: str | None,
                    labels: Mapping[str, Mapping[str, str]] | None = None) -> str:
    """The declared label in ``language``, else the English one, else the humanized key."""
    labels = labels or {}
    for lang in (as_language(language) or DEFAULT_LANGUAGE, DEFAULT_LANGUAGE):
        found = (labels.get(lang) or {}).get(key)
        if isinstance(found, str) and found.strip():
            return found.strip()
    return humanize_key(key)


def declared_labels(target: Any) -> Mapping[str, Mapping[str, str]]:
    """``aggregate_labels`` of a Tool or Workflow (``{}`` for anything else, e.g. None)."""
    labels = getattr(target, "aggregate_labels", None)
    return labels if isinstance(labels, Mapping) else {}


def labels_for(registry: Any, name: str | None) -> Mapping[str, Mapping[str, str]]:
    """Declared labels of the registry target ``name``; ``{}`` when it is not a target."""
    if not name or registry is None:
        return {}
    try:
        return declared_labels(registry.target(name))
    except (KeyError, AttributeError):
        return {}


def result_view(result: ToolResult, *, language: str | None = None,
                labels: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, JsonValue]:
    """The narrator view plus the reply language and one readable label per aggregate."""
    lang = as_language(language) or DEFAULT_LANGUAGE
    view = result.narrator_view()
    view["language"] = lang
    view["labels"] = {key: aggregate_label(key, lang, labels) for key in result.aggregates}
    return view


DATA_TEXT_KEYS = ("message", "scope")


def fenced_view(view: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """``view`` as a language model may see it: each non-empty ``DATA_TEXT_KEYS`` string
    inside an untrusted-content fence with a fresh nonce (``guards.egress.wrap_untrusted``),
    so an instruction stored in a record (a supplier named "ignore previous instructions")
    reaches the model only as data. Adds nothing else."""
    out = dict(view)
    for key in DATA_TEXT_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = wrap_untrusted(value, key)
    return out


# ---------------------------------------------------------------- template


def _sentence(text: str, language: str) -> str:
    text = text.strip()
    if not text or text.endswith((".", "!", "?", "。", "！", "？")):
        return text
    return text + ("。" if language == "zh" else ".")


def template_narration(view: Mapping[str, JsonValue], *, language: str | None = None) -> str:
    """One short sentence per fact: the tool's message, the row count, each aggregate.

    The language is ``language``, else ``view["language"]``, else English; aggregate names come
    from ``view["labels"]`` and fall back to the humanized key.
    """
    lang = as_language(language) or as_language(view.get("language")) or DEFAULT_LANGUAGE
    labels = view.get("labels") if isinstance(view.get("labels"), Mapping) else {}
    sentences: list[str] = []
    message = _sentence(str(view.get("message") or ""), lang)
    if message:
        sentences.append(message)
    row_count = view.get("row_count")
    where = scope_clause(str(view.get("scope") or ""), lang)
    if isinstance(row_count, int) and not isinstance(row_count, bool):
        if row_count == 0:
            sentences.append(say("rows_none", lang, where=where))
        elif row_count == 1:
            sentences.append(say("rows_one", lang, where=where))
        else:
            sentences.append(say("rows_many", lang, count=row_count, where=where))
    aggregates = view.get("aggregates")
    if isinstance(aggregates, Mapping):
        for key, value in aggregates.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                label = labels.get(key) if isinstance(labels.get(key), str) else None
                sentences.append(say("figure", lang, label=label or humanize_key(str(key)),
                                     value=format_number(value)))
    joiner = "" if lang == "zh" else " "
    return joiner.join(sentences) or say("no_figures", lang)


def allowed_numbers(result: ToolResult) -> set[float]:
    return {float(n) for n in result.allowed_numbers()}


def narrate_result(
    provider: "Provider", question: str, result: ToolResult, lock: "NumericLock", *,
    language: str | None = None, labels: Mapping[str, Mapping[str, str]] | None = None,
) -> Narration:
    """Ask the model to narrate the narrator view, lock its numbers, degrade on any failure."""
    view = result_view(result, language=language, labels=labels)
    try:
        text = provider.narrate(question, view)
        if not isinstance(text, str):
            raise TypeError("narration is not text")
    except Exception:  # any model-side failure degrades to the template, never to an error
        return Narration(text=template_narration(view), source="template")
    locked = lock(text, allowed_numbers(result))
    if not locked.text.strip():
        return Narration(text=template_narration(view), stripped=locked.stripped_count,
                         source="template")
    return Narration(text=locked.text, stripped=locked.stripped_count, source="model")
