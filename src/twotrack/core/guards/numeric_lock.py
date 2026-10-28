"""Numeric lock: a narration sentence may only contain numbers the tool result can back.

Rules (over-stripping is safe, under-stripping is not):

* Split into sentences on ``. ! ? ;`` followed by whitespace or end, on ``。！？；`` and on
  newlines. A sentence with any unbacked number is removed whole; the rest is kept as written.
* Numbers: ASCII or full-width digits, thousands separators (``1,234``), decimals, a leading
  sign or currency symbol (ignored), a trailing ``%``/``percent`` and scale words
  (``k``, ``M``, ``bn``, ``thousand``, ``million``, ``billion``, ``万``, ``亿``). CJK numerals
  are read when they contain a unit (``四十二``); a lone ``一`` is too common in words to count.
* Backed: a written number w with precision p (``12.3`` -> 0.1, ``1.2 million`` -> 100,000) is
  backed if some allowed value a has ``|abs(a) - w| <= p / 2``, i.e. w is a written at that
  precision. Signs are ignored ("fell by 12" may cite -12). A percentage is also backed by a
  ratio: ``93.5%`` matches 0.935.
* Not checked: a bare 4-digit integer 1900-2100 (a year; write quantities with a separator,
  ``2,000``, and they are checked), ordinals (``3rd``, ``#2``, ``第3``), ISO dates and clock
  times, line-start list markers (``1.``, ``2)``), and digits glued to letters (``Q3``,
  ``SKU-123``: identifiers, not quantities). Small integers are NOT exempt: a made-up
  "3 orders are late" is exactly the error this lock exists to catch.
* Known gap: spelled-out English numbers ("three") are not read; the narrator prompt must ask
  for digits.
"""

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

_SOFT_END = frozenset(".!?;")
_HARD_END = frozenset("。！？；")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_DATES = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}:\d{2}(?::\d{2})?"
                    r"|\d{4}年\d{1,2}月(?:\d{1,2}日)?")
_LIST_MARKER = re.compile(r"^\s*\d{1,2}[.)](?=\s)")
_ORDINAL = re.compile(r"(?:st|nd|rd|th)(?![A-Za-z])")
_PERCENT = re.compile(r"\s?(?:%|percent(?![A-Za-z])|pct(?![A-Za-z]))")
_SCALE = re.compile(r"\s?(thousand|million|billion|bn|k|K|M)(?![A-Za-z])|(万|亿)")
_SCALES = {"thousand": 1e3, "k": 1e3, "K": 1e3, "million": 1e6, "M": 1e6, "billion": 1e9,
           "bn": 1e9, "万": 1e4, "亿": 1e8}
_CURRENCY = frozenset("$€£¥")
_CJK_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
               "七": 7, "八": 8, "九": 9}
_CJK_UNITS = {"十": 10, "百": 100, "千": 1000}
_CJK_BIG = {"万": 10_000, "亿": 100_000_000}
_CJK_RUN = re.compile("[" + "".join([*_CJK_DIGITS, *_CJK_UNITS, *_CJK_BIG]) + "]+")
_CJK_PERCENT_PREFIX = "百分之"


class LockResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    stripped_count: int
    stripped_sentences: list[str]


@dataclass(frozen=True)
class WrittenNumber:
    value: float  # absolute value after scaling
    step: float  # precision as written, after scaling
    percent: bool
    text: str


def split_sentences(text: str) -> list[str]:
    """Pieces that concatenate back to ``text``; each keeps its terminator and trailing space."""
    pieces, start, i, n = [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n" or ch in _HARD_END or (ch in _SOFT_END and (i + 1 == n
                                                                    or text[i + 1].isspace())):
            end = i + 1
            while ch != "\n" and end < n and text[end] in " \t":
                end += 1
            pieces.append(text[start:end])
            start = i = end
            continue
        i += 1
    if start < n:
        pieces.append(text[start:])
    return pieces


def _skip_arabic(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start else ""
    two_back = text[start - 2] if start >= 2 else ""
    if before.isascii() and (before.isalpha() or before == "_"):
        return True  # Q3, SKU123, v2
    if before in "-/" and two_back.isascii() and two_back.isalpha():
        return True  # SKU-123, A/12
    if before == "." and two_back.isdigit():
        return True  # third part of a version string 1.2.3
    if before in ("#", "第"):
        return True  # ordinal
    return bool(_ORDINAL.match(text, end))


def _arabic_numbers(text: str) -> list[WrittenNumber]:
    out = []
    for match in _NUMBER.finditer(text):
        start, end = match.span()
        if _skip_arabic(text, start, end):
            continue
        digits = match.group().replace(",", "")
        decimals = len(digits.partition(".")[2])
        value, step, scale = float(digits), 10.0 ** -decimals, 1.0
        percent = bool(_PERCENT.match(text, end))
        scaled = None if percent else _SCALE.match(text, end)
        if scaled:
            scale = _SCALES[scaled.group(1) or scaled.group(2)]
        before = text[start - 1] if start else ""
        is_year = (decimals == 0 and "," not in match.group() and len(digits) == 4
                   and 1900 <= value <= 2100 and not percent and scale == 1.0
                   and before not in _CURRENCY)
        if not is_year:
            out.append(WrittenNumber(value * scale, step * scale, percent, match.group()))
    return out


def _cjk_value(run: str) -> int | None:
    has_digit = any(c in _CJK_DIGITS for c in run)
    has_unit = any(c in _CJK_UNITS or c in _CJK_BIG for c in run)
    if not (has_digit and has_unit) or not (run[0] in _CJK_DIGITS or run[0] == "十"):
        return None
    total, section, digit = 0, 0, None
    for ch in run:
        if ch in _CJK_DIGITS:
            digit = _CJK_DIGITS[ch]
        elif ch in _CJK_UNITS:
            section += (1 if digit is None else digit) * _CJK_UNITS[ch]
            digit = None
        else:
            total += (section + (digit or 0)) * _CJK_BIG[ch]
            section, digit = 0, None
    return total + section + (digit or 0)


def _cjk_numbers(text: str) -> list[WrittenNumber]:
    out = []
    for match in _CJK_RUN.finditer(text):
        start = match.start()
        if start and text[start - 1] == "第":
            continue
        value = _cjk_value(match.group())
        if value is not None:
            percent = text[max(0, start - 3):start] == _CJK_PERCENT_PREFIX
            out.append(WrittenNumber(float(value), 1.0, percent, match.group()))
    return out


def extract_numbers(sentence: str) -> list[WrittenNumber]:
    text = unicodedata.normalize("NFKC", sentence)
    text = _LIST_MARKER.sub(" ", _DATES.sub(" ", text))
    return _arabic_numbers(text) + _cjk_numbers(text)


def _close(written: float, actual: float, step: float) -> bool:
    return abs(actual - written) <= step / 2 + 1e-9 * max(1.0, abs(written))


def is_backed(number: WrittenNumber, allowed: Iterable[float]) -> bool:
    for value in allowed:
        if _close(number.value, value, number.step):
            return True
        if number.percent and _close(number.value, value * 100, number.step):
            return True
    return False


def lock_narrative(text: str, allowed: Iterable[float]) -> LockResult:
    """Strip every sentence of ``text`` that cites a number not backed by ``allowed``."""
    pool = [abs(float(a)) for a in allowed
            if isinstance(a, int | float) and not isinstance(a, bool) and math.isfinite(a)]
    kept: list[str] = []
    stripped: list[str] = []
    for piece in split_sentences(text):
        if all(is_backed(n, pool) for n in extract_numbers(piece)):
            kept.append(piece)
            continue
        stripped.append(piece.strip())
        if piece.endswith("\n"):
            kept.append("\n")  # keep the paragraph break, drop the sentence
    return LockResult(text="".join(kept).strip(), stripped_count=len(stripped),
                      stripped_sentences=stripped)
