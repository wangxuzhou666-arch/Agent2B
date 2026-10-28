"""Typed field values: coerce raw input (import cells, API JSON) into a field's declared type.

Stored forms: str/enum/date as text (dates ISO ``YYYY-MM-DD``), int, float, bool. Blank input
(None or whitespace) is None. Error messages never echo the input value, so they are safe to
show even when the field is restricted. They exist in both reply languages (``ValueProblem``,
``FieldProblem``; English when no language is given) and read as sentences ("must be one of A,
B or C"), never as a Python list.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..contract import FieldSpec, FieldType, ResolvedEntity
from ..i18n import join_or, say
from ..params import field_name

RECORD_KEY = "_record"  # error key for problems that are not about a single field


class ValueProblem(ValueError):
    """Why a raw value does not fit a field (an ``core.i18n`` message key). ``str()`` is the
    English text, so callers that only catch ValueError keep working."""

    def __init__(self, key: str, options: Sequence[str] = ()) -> None:
        self.key, self.options = key, tuple(options)
        super().__init__(self.text())

    def text(self, language: str | None = None) -> str:
        if self.options:
            return say(self.key, language, values=join_or(self.options, language))
        return say(self.key, language)


@dataclass(frozen=True)
class FieldProblem:
    """One problem of one record: the field it is about (None: the record as a whole), that
    field's display name and what is wrong, both in the requested language."""

    field_id: str | None
    label: str
    message: str

    def text(self, language: str | None = None) -> str:
        """'Quantity: must be a number', the form ``validate_record`` reports."""
        return say("value_field", language, label=self.label, problem=self.message) \
            if self.label else self.message

_GROUPED = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")  # 1,234 or 1,234.5
_PLAIN_INT = re.compile(r"^[+-]?\d+$")
_TRUE = frozenset({"true", "yes", "y", "t", "1"})
_FALSE = frozenset({"false", "no", "n", "f", "0"})


def is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _number_text(raw: Any) -> str:
    text = str(raw).strip()
    return text.replace(",", "") if _GROUPED.match(text) else text


def _to_float(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueProblem("value_number")
    try:
        value = float(raw) if isinstance(raw, int | float) else float(_number_text(raw))
    except (TypeError, ValueError):
        raise ValueProblem("value_number") from None
    if not math.isfinite(value):
        raise ValueProblem("value_finite")
    return value


def _to_int(raw: Any) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and _PLAIN_INT.match(_number_text(raw)):
        return int(_number_text(raw))
    value = _to_float(raw)
    if not value.is_integer():
        raise ValueProblem("value_whole")
    return int(value)


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueProblem("value_bool")


def _to_date(raw: Any) -> str:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    text = str(raw).strip().replace("/", "-")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        raise ValueProblem("value_date") from None


def _to_enum(spec: FieldSpec, raw: Any) -> str:
    text = str(raw).strip()
    for option in spec.enum_values:
        if option.casefold() == text.casefold():
            return option
    raise ValueProblem("value_one_of", spec.enum_values)


def _to_str(raw: Any) -> str:
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))  # a spreadsheet cell holding 123 for a text field
    if isinstance(raw, datetime | date):
        return raw.isoformat()
    return str(raw).strip()


def coerce_value(spec: FieldSpec, raw: Any) -> Any:
    """Raw value -> stored value for ``spec``. Blank -> None. Raises ValueProblem (a
    ValueError)."""
    if is_blank(raw):
        return None
    match spec.type:
        case FieldType.INT:
            return _to_int(raw)
        case FieldType.FLOAT:
            return _to_float(raw)
        case FieldType.BOOL:
            return _to_bool(raw)
        case FieldType.DATE:
            return _to_date(raw)
        case FieldType.ENUM:
            return _to_enum(spec, raw)
        case _:
            return _to_str(raw)


def check_record(entity: ResolvedEntity, record: Mapping[str, Any],
                 language: str | None = None) -> tuple[dict[str, Any], list[FieldProblem]]:
    """Coerce every field of ``entity`` from ``record``.

    Returns (clean values for all fields, the problems in field order). Unknown keys are one
    problem of the record as a whole; the ``_entity`` discriminator key is ignored.
    """
    known = {f.id for f in entity.all_fields}
    unknown = sorted(k for k in record if k not in known and k != "_entity")
    problems: list[FieldProblem] = []
    if unknown:
        problems.append(FieldProblem(None, "", f"unknown field(s) {unknown}"))
    clean: dict[str, Any] = {}
    for spec in entity.all_fields:
        try:
            value = coerce_value(spec, record.get(spec.id))
        except ValueError as exc:
            message = exc.text(language) if isinstance(exc, ValueProblem) else str(exc)
            problems.append(FieldProblem(spec.id, field_name(spec, language), message))
            clean[spec.id] = None
            continue
        if value is None and (spec.required or spec.id == entity.primary_key):
            problems.append(FieldProblem(spec.id, field_name(spec, language),
                                         say("value_required", language)))
        clean[spec.id] = value
    return clean, problems


def validate_record(
    entity: ResolvedEntity, record: Mapping[str, Any], language: str | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """``check_record`` with the errors keyed by field id (``RECORD_KEY`` for the record as a
    whole), each one a sentence: 'Quantity: must be a number'."""
    clean, problems = check_record(entity, record, language)
    return clean, {p.field_id or RECORD_KEY: p.text(language) for p in problems}
