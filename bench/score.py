"""Scoring: which numbers a user is shown, and whether each one is expected, backed or wrong.

Definitions (bench/README.md has the long form):

* Shown numbers are read exactly as the numeric lock reads them: ``split_sentences`` then
  ``extract_numbers`` from ``core.guards.numeric_lock``, so the scorer and the lock agree on
  what a number is (dates, ISO times, bare years, ordinals, list markers and ids such as
  ``SKU-10042`` are not numbers to either).
* A shown number *matches* an expected value when the lock's own ``is_backed`` rule holds
  (the written number is the value at the precision it was written), or it is within the
  item's absolute tolerance. A match is a *coincidence*, not an answer, when the only tool
  outputs of the run that back the number come from tools that do not measure the asked
  quantity (the item's ``accepted_tools``), e.g. a "Shipped count: 3" from the purchase-order
  report when the question asked how many orders are late and the answer is also 3.
* Each shown number gets one role, checked in order: ``expected`` (matches an expected
  value, not by coincidence), ``echo`` (repeats a number of the question), ``supported``
  (backed by the run's tool outputs, see ``result_numbers``), else ``unsupported``.
* A *stand-in* is a supported or unsupported number that matches a scope distractor of an
  expected value the answer does not show. A stand-in is a *contradiction* unless the answer
  says it could not answer (abstention language), then it is *caveated*.
* wrong-number  = at least one unsupported number, or a contradiction.
* abstained     = no shown number other than echoes, or abstention language.
* outcome       = wrong_number, else (answerable) correct if every expected number and text
  is shown, abstained, or incomplete; else (unanswerable) correct if abstained, or answered.
"""

import bisect
import itertools
import json
import math
import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from twotrack.core.guards.numeric_lock import (
    WrittenNumber,
    extract_numbers,
    is_backed,
    split_sentences,
)
from twotrack.core.narration import format_number

from .questions import CATEGORIES, Expected, ExpectedText, Item, parse_locator, question_numbers

Role = Literal["expected", "echo", "supported", "unsupported"]
Outcome = Literal["correct", "wrong_number", "abstained", "incomplete", "answered"]

MAX_GROUPS = 200  # group-by columns with more distinct values than this are not grouped
MAX_ROWS_PAIRWISE = 5  # within-row arithmetic is credited for results this short
_DIGITS = re.compile(r"\d+(?:\.\d+)?")

# Abstention / clarification / no-match language. Deliberately narrow: phrases that say the
# system cannot or will not give the asked figure. Applied identically to every system.
ABSTAIN_PATTERNS = (
    r"\b(?:i|we)\s+(?:can ?not|can't|cannot|am unable|are unable|am not able|are not able)\b",
    r"\b(?:i|we)\s+(?:don't|do not)\s+have\b",
    r"\bunable to\b",
    r"\bnot able to\b",
    r"\b(?:can ?not|can't|cannot)\s+(?:be\s+)?(?:determine|answer|provide|find|calculate|"
    r"compute|tell|forecast|predict|break)",
    r"\b(?:no|not any)\s+(?:data|information|records?|history)\s+(?:on|about|for|available)",
    r"\b(?:doesn't|does not|don't|do not)\s+(?:include|contain|track|record|cover|break down|"
    r"show|provide|have)\b",
    r"\b(?:isn't|is not|aren't|are not)\s+(?:available|tracked|recorded|included|covered)\b",
    r"\bnot (?:available|tracked|recorded|included|supported)\s+(?:in|by)\b",
    r"\bout of scope\b|\boutside (?:the|my) scope\b",
    r"\bnot found\b|\bno such\b|\b(?:does not|doesn't) exist\b",
    r"\bno\s+(?:matching\s+)?(?:purchase orders?|inventory records?|rows?|records?|orders?|"
    r"skus?|items?)\s+(?:match|matched|found)\b",
    r"\bcould you (?:please )?(?:clarify|specify|tell me which)\b",
    r"\bplease (?:clarify|specify|confirm)\b",
    r"\bwhich (?:one|order|sku|supplier|item|report)s? (?:do|did) you mean\b",
    r"\bdid you mean\b",
    r"\bwithheld\b",
    r"\bnot (?:\w+\s+){0,3}(?:broken down|split|separated|filtered)\b",
    r"\bno (?:breakdown|split)\b",
    "无法", "不能提供", "不能回答", "没有相关", "没有.{0,6}数据", "未包含", "不包含", "不支持",
    "不存在", "未找到", "没有找到", "没有匹配", "查无", "请明确", "请说明", "请具体", "请先确认",
    "您是指", "您指的是", "你指的是", "哪一张", "哪一个", "超出.{0,4}范围", "暂无", "已被拦下",
    "没有按", "未按", "不区分",
)
ABSTAIN_RE = re.compile("|".join(f"(?:{p})" for p in ABSTAIN_PATTERNS), re.IGNORECASE)

# Diagnostic only (does not change any score): numbers written as words are invisible to the
# lock and to this scorer alike.
SPELLED_RE = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|million)\b|[两二三四五六七八九十]+(?:个|张|家|天|件|种|位)",
    re.IGNORECASE)


# ---------------------------------------------------------------- numbers


def is_number(value: Any) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and math.isfinite(value))


def numbers_in(text: str) -> list[WrittenNumber]:
    """Numbers as the numeric lock reads them: per sentence piece, same extractor."""
    out: list[WrittenNumber] = []
    for piece in split_sentences(text or ""):
        out.extend(extract_numbers(piece))
    return out


def written(value: float) -> WrittenNumber:
    """A computed value as a display would write it (exact, lock-friendly formatting)."""
    found = extract_numbers(format_number(value))
    if not found:  # e.g. 2026 reads as a year; keep it as an exact integer
        return WrittenNumber(abs(float(value)), 1.0, False, format_number(value))
    return found[0]


def matches(number: WrittenNumber, expected: Expected) -> bool:
    if is_backed(number, [abs(expected.value)]):
        return True
    return expected.tol > 0 and abs(number.value - abs(expected.value)) <= expected.tol + 1e-9


class Pool:
    """Sorted absolute values. ``backs`` equals ``numeric_lock.is_backed`` over the same values
    (a binary search finds the candidates, ``is_backed`` decides)."""

    def __init__(self, values: Iterable[Any] = ()) -> None:
        self.values = sorted({abs(float(v)) for v in values if is_number(v)})

    def __len__(self) -> int:
        return len(self.values)

    def backs(self, number: WrittenNumber) -> bool:
        reach = number.step / 2 + 1e-9 * max(1.0, abs(number.value))
        windows = [(number.value - reach, number.value + reach)]
        if number.percent:
            windows.append(((number.value - reach) / 100, (number.value + reach) / 100))
        for low, high in windows:
            pad = 1e-9 * max(1.0, abs(high))
            i = bisect.bisect_left(self.values, low - pad)
            j = bisect.bisect_right(self.values, high + pad)
            if i < j and is_backed(number, self.values[i:j]):
                return True
        return False


def result_numbers(rows: Sequence[Mapping[str, Any]], aggregates: Mapping[str, Any],
                   row_count: int | None = None) -> list[float]:
    """Numbers derivable from one tool result (the *supported* pool):

    * every number in it: aggregates, row count, cells, digit runs inside text cells;
    * per numeric column: sum, min, max, mean and count;
    * per text column: distinct count and, grouped by its values (at most ``MAX_GROUPS``
      groups), each group's size and each numeric column's sum, mean, min, max and share of
      the column total;
    * pairwise sum, difference and ratio of the aggregates and the row count;
    * within each row of a result with at most ``MAX_ROWS_PAIRWISE`` rows: pairwise sum,
      difference, product and ratio of its numeric cells.
    """
    out: list[float] = []
    agg_values = [float(v) for v in aggregates.values() if is_number(v)]
    out.extend(agg_values)
    count = len(rows) if row_count is None else row_count
    out.extend([float(count), float(len(rows))])
    columns: dict[str, list[Any]] = {}
    for row in rows:
        for key, value in row.items():
            columns.setdefault(key, []).append(value)
            if is_number(value):
                out.append(float(value))
            elif isinstance(value, str):
                out.extend(float(d) for d in _DIGITS.findall(value))
    numeric = {k: [float(v) for v in vs if is_number(v)] for k, vs in columns.items()}
    numeric = {k: v for k, v in numeric.items() if v}
    for values in numeric.values():
        out.extend([sum(values), min(values), max(values), sum(values) / len(values),
                    float(len(values))])
    for key, values in columns.items():
        distinct = {json.dumps(v, sort_keys=True, default=str) for v in values}
        out.append(float(len(distinct)))
        texty = all(isinstance(v, str) for v in values if v is not None)
        if not texty or not 1 < len(distinct) <= MAX_GROUPS or len(distinct) >= len(rows):
            continue
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get(key)), []).append(row)
        for members in groups.values():
            out.append(float(len(members)))
            for column, all_values in numeric.items():
                vals = [float(r[column]) for r in members if is_number(r.get(column))]
                if vals:
                    out.extend([sum(vals), sum(vals) / len(vals), min(vals), max(vals)])
                    if sum(all_values):
                        out.append(sum(vals) / sum(all_values))
    small = [*agg_values, float(count)]
    for a, b in itertools.permutations(small, 2):
        out.extend([a + b, a - b])
        if b:
            out.append(a / b)
    if len(rows) <= MAX_ROWS_PAIRWISE:
        for row in rows:
            cells = [float(v) for v in row.values() if is_number(v)]
            for a, b in itertools.permutations(cells, 2):
                out.extend([a + b, a - b, a * b])
                if b:
                    out.append(a / b)
    return [v for v in out if math.isfinite(v)]


def text_numbers(texts: Iterable[str]) -> list[float]:
    """Digit runs in free text (tool problem messages): echoing them is not inventing."""
    return [float(d) for text in texts for d in _DIGITS.findall(text or "")]


class Evidence:
    """The tool outputs one run saw, kept per tool so a match can be traced to its source."""

    def __init__(self, outputs: Iterable[tuple[str, Pool]] = (),
                 extra: Pool | None = None) -> None:
        self.outputs = list(outputs)
        self.extra = extra or Pool()

    def backs(self, number: WrittenNumber) -> bool:
        return self.extra.backs(number) or any(p.backs(number) for _, p in self.outputs)

    def coincidence(self, number: WrittenNumber, accepted: frozenset[str]) -> bool:
        """Backed by some output, but only by outputs of tools that measure something else."""
        backing = {tool for tool, pool in self.outputs if pool.backs(number)}
        return bool(backing) and not backing & accepted

    def size(self) -> int:
        return len(self.extra) + sum(len(p) for _, p in self.outputs)


# ---------------------------------------------------------------- views and scores


class View(BaseModel):
    """What one system showed for one question."""

    text: str
    aggregates: dict[str, float] = {}  # labeled figures shown next to the text
    rows: list[dict[str, Any]] = []  # the visible table, read only through locators
    use_locators: bool = False
    tool: str | None = None  # the target whose result the aggregates and rows come from


class Shown(BaseModel):
    text: str
    value: float
    source: Literal["text", "aggregate"]
    role: Role
    distractor_of: str | None = None


class Score(BaseModel):
    outcome: Outcome
    correct: bool
    wrong_number: bool
    unsupported: int
    contradiction: bool
    caveated: bool
    abstained: bool
    expected_found: list[bool]
    text_found: list[bool]
    spelled_out: bool
    numbers: list[Shown]


def _norm_id(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKC", text).casefold())


def locate(locator: str, view: View) -> Any:
    kind, column, key_col, key_val = parse_locator(locator)
    if kind == "agg":
        return view.aggregates.get(column)
    if kind == "top":
        return view.rows[0].get(column) if view.rows else None
    if kind == "cell":
        for row in view.rows:
            if str(row.get(key_col, "")).casefold() == (key_val or "").casefold():
                return row.get(column)
    return None


def _locators_apply(item: Item, view: View) -> bool:
    """Table and top-row credit only for a result of a tool that measures the asked
    quantity (a locator names a column of that tool's output)."""
    return view.use_locators and view.tool in item.accepted_tools


def _located_number(item: Item, expected: Expected, view: View) -> bool:
    if not (expected.locator and _locators_apply(item, view)):
        return False
    value = locate(expected.locator, view)
    return is_number(value) and abs(float(value) - expected.value) <= max(
        expected.tol, 1e-9 * max(1.0, abs(expected.value)))


def _text_found(item: Item, expected: ExpectedText, view: View) -> bool:
    haystack = _norm_id(view.text)
    if any(_norm_id(alt) and _norm_id(alt) in haystack for alt in expected.alternatives):
        return True
    if expected.locator and _locators_apply(item, view):
        value = locate(expected.locator, view)
        return value is not None and any(_norm_id(str(value)) == _norm_id(alt)
                                         for alt in expected.alternatives)
    return False


def score(item: Item, view: View, evidence: Evidence) -> Score:
    shown: list[tuple[WrittenNumber, Literal["text", "aggregate"]]] = [
        (n, "text") for n in numbers_in(view.text)]
    for value in view.aggregates.values():
        if is_number(value):
            shown.append((written(value), "aggregate"))
    echoes = [*question_numbers(item.text), *(n.value for n in numbers_in(item.text))]
    accepted = item.accepted_tools
    found = [False] * len(item.expected)
    hit_distractor: set[int] = set()
    numbers: list[Shown] = []
    for number, source in shown:
        expected_hits = [i for i, e in enumerate(item.expected) if matches(number, e)]
        if expected_hits and evidence.coincidence(number, accepted):
            expected_hits = []
        for i in expected_hits:
            found[i] = True
        role: Role
        if expected_hits:
            role = "expected"
        elif is_backed(number, echoes):
            role = "echo"
        elif evidence.backs(number):
            role = "supported"
        else:
            role = "unsupported"
        distractor_of = None
        if role in ("supported", "unsupported"):
            for i, e in enumerate(item.expected):
                if e.distractors and is_backed(number, e.distractors):
                    distractor_of = e.name
                    hit_distractor.add(i)
                    break
        numbers.append(Shown(text=number.text, value=number.value, source=source, role=role,
                             distractor_of=distractor_of))
    for i, e in enumerate(item.expected):
        if not found[i] and _located_number(item, e, view):
            found[i] = True
    text_found = [_text_found(item, t, view) for t in item.expected_text]

    unsupported = sum(1 for n in numbers if n.role == "unsupported")
    claims = [n for n in numbers if n.role != "echo"]
    caveat = bool(ABSTAIN_RE.search(view.text or ""))
    abstained = not claims or caveat
    stand_in = any(not found[i] for i in hit_distractor)
    contradiction = stand_in and not caveat
    wrong = unsupported > 0 or contradiction
    outcome: Outcome
    if wrong:
        outcome = "wrong_number"
    elif item.answerable:
        if all(found) and all(text_found):
            outcome = "correct"
        else:
            outcome = "abstained" if abstained else "incomplete"
    else:
        outcome = "correct" if abstained else "answered"
    return Score(outcome=outcome, correct=outcome == "correct", wrong_number=wrong,
                 unsupported=unsupported, contradiction=contradiction,
                 caveated=stand_in and caveat, abstained=abstained, expected_found=found,
                 text_found=text_found, spelled_out=bool(SPELLED_RE.search(view.text or "")),
                 numbers=numbers)


# ---------------------------------------------------------------- summaries

VIEWS = ("A", "C", "B_seen", "B_text")
VIEW_LABELS = {
    "A": "A  baseline agent",
    "C": "C  baseline + numeric lock",
    "B_seen": "B  twotrack, what the user sees",
    "B_text": "B  twotrack, narration only",
}


def _rate_block(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {"n": len(records),
            "correct": sum(1 for r in records if r["score"]["correct"]),
            "wrong_number": sum(1 for r in records if r["score"]["wrong_number"]),
            "abstained": sum(1 for r in records if r["score"]["abstained"])}


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[-1]


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per view: counts behind every rate the report prints."""
    out: dict[str, dict[str, Any]] = {}
    for view in VIEWS:
        mine = [r for r in records if r["view"] == view]
        if not mine:
            continue
        answerable = [r for r in mine if r["answerable"]]
        unanswerable = [r for r in mine if not r["answerable"]]
        latencies = [r["latency_ms"] for r in mine if r.get("latency_ms") is not None]
        routed = [r for r in answerable if r.get("routed_ok") is not None]
        hits = [r for r in answerable if r.get("tool_hit") is not None]
        tokens = [r.get("tokens") or {} for r in mine]
        failed = sum(len(r.get("model_errors") or ()) for r in mine)
        block = {
            **_rate_block(mine),
            "unsupported_answers": sum(1 for r in mine if r["score"]["unsupported"] > 0),
            "contradiction_answers": sum(1 for r in mine if r["score"]["contradiction"]),
            "caveated": sum(1 for r in mine if r["score"]["caveated"]),
            "answerable": len(answerable),
            "abstained_answerable": sum(1 for r in answerable if r["score"]["abstained"]),
            "unanswerable": len(unanswerable),
            "correct_unanswerable": sum(1 for r in unanswerable if r["score"]["correct"]),
            "routing": ({"n": len(routed), "ok": sum(1 for r in routed if r["routed_ok"]),
                         "strict_ok": sum(1 for r in routed
                                          if (r.get("detail") or {}).get("routed_strict"))}
                        if routed else None),
            "tool_hit": ({"n": len(hits), "ok": sum(1 for r in hits if r["tool_hit"])}
                         if hits else None),
            "latency_ms_avg": round(statistics.fmean(latencies), 1) if latencies else None,
            "latency_ms_p95": round(_p95(sorted(latencies)), 1) if latencies else None,
            "prompt_tokens": sum(t.get("prompt") or 0 for t in tokens),
            "completion_tokens": sum(t.get("completion") or 0 for t in tokens),
            "tokens_estimated": any(t.get("estimated") for t in tokens),
            "model_calls": sum(len(r.get("calls") or []) for r in mine),
            "call_errors": dict(Counter(c["purpose"] for r in mine for c in r.get("calls") or []
                                        if not c.get("ok", True))),
            # model calls the system itself reports as failed (twotrack: its answer's
            # model_errors; the baseline: its own); any one degrades the view
            "failed_calls": failed,
            "degraded": failed > 0,
            "errors": sum(1 for r in mine if r.get("error")),
            "spelled_out": sum(1 for r in mine if r["score"]["spelled_out"]),
            "outcomes": dict(Counter(r["score"]["outcome"] for r in mine)),
            "by_category": {c: _rate_block([r for r in mine if r["category"] == c])
                            for c in CATEGORIES if any(r["category"] == c for r in mine)},
            "by_lang": {lang: _rate_block([r for r in mine if r["lang"] == lang])
                        for lang in ("en", "zh") if any(r["lang"] == lang for r in mine)},
            "by_tag": {tag: _rate_block([r for r in mine if tag in r.get("tags", ())])
                       for tag in sorted({t for r in mine for t in r.get("tags", ())})},
            "by_repeat": {str(k): _rate_block([r for r in mine if r["repeat"] == k])
                          for k in sorted({r["repeat"] for r in mine})},
            "route_sources": _route_sources(answerable),
        }
        out[view] = block
    return out


def _route_sources(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]] | None:
    """B only: which routing arm decided each answerable question, and how often it was right."""
    out: dict[str, dict[str, int]] = {}
    for r in records:
        decision = (r.get("detail") or {}).get("decision")
        if not decision:
            continue
        key = f"{decision['source']}:{decision['kind']}"
        row = out.setdefault(key, {"n": 0, "routed_ok": 0})
        row["n"] += 1
        row["routed_ok"] += int(bool(r.get("routed_ok")))
    return dict(sorted(out.items())) or None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))
