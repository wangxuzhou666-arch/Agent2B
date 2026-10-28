"""System B (``run_query`` on an ``Engine`` that this harness wires from core parts) and system C
(the baseline's final answer put through the numeric lock after the fact).

B is not the running server. ``bench/world.py`` builds the registry (``load_registry`` with no
extra paths: built-in plugins only) and a plain ``Pipeline`` over an in-memory database with a
``SqliteAuditSink``; ``make_engine`` adds ``EgressGuard`` as the scrubber over restricted values,
the provider and the product's numeric lock. There is no HTTP server and no
``ApprovalRecordingPipeline``, and ``Engine.audit`` and ``Engine.explorer`` are unset.
``allow_open_task`` is off (the product default); with it on, the built-in loop plans. What the
user sees is the locked narration plus the result's aggregates and table; B is scored twice, on
that whole view and on the narration alone.

C isolates the lock from the architecture: A's answer, locked against every number present in
A's own tool outputs (rows included), with the product's fallback text when nothing survives.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

from twotrack.core.contract import TraceStep
from twotrack.core.graph import Engine, GraphSettings, QueryResponse, run_query
from twotrack.core.graph.builder import DRAFT_STRIPPED
from twotrack.core.guards import EgressGuard, lock_narrative, restricted_values_source
from twotrack.providers.base import Provider

from .baseline import BaselineRun
from .questions import Item
from .score import Evidence, Pool, View, is_number, result_numbers, text_numbers
from .world import World

# ---------------------------------------------------------------- B: twotrack


def make_engine(world: World, provider: Provider, *, allow_open_task: bool = False) -> Engine:
    return Engine(registry=world.registry, provider=provider, pipeline=world.pipeline,
                  scrubber=EgressGuard(restricted_values_source(world.store, world.registry)),
                  settings=GraphSettings(allow_open_task=allow_open_task))


@dataclass
class TwotrackRun:
    response: QueryResponse | None
    latency_ms: float
    error: str | None = None


def run_twotrack(engine: Engine, world: World, question: str) -> TwotrackRun:
    started = time.perf_counter()
    try:
        response = run_query(engine, world.ctx(), question)
    except Exception as exc:  # the graph degrades on its own; this is a last guard
        return TwotrackRun(None, _ms(started), type(exc).__name__)
    return TwotrackRun(response, _ms(started), response.error)


def _result_tool(response: QueryResponse) -> str | None:
    if response.provenance is not None:
        return response.provenance.tool
    return response.decision.target if response.decision is not None else None


def twotrack_evidence(response: QueryResponse | None) -> Evidence:
    if response is None or (response.provenance is None and not response.rows
                            and not response.aggregates):
        return Evidence()
    row_count = response.provenance.row_count if response.provenance else len(response.rows)
    pool = Pool(result_numbers(response.rows, response.aggregates, row_count))
    return Evidence([(_result_tool(response) or "unknown", pool)])


def twotrack_views(response: QueryResponse | None) -> dict[str, View]:
    """``B_seen``: narration + aggregates + table (via locators); ``B_text``: narration only."""
    if response is None:
        return {"B_seen": View(text=""), "B_text": View(text="")}
    aggregates = {k: float(v) for k, v in response.aggregates.items() if is_number(v)}
    return {"B_seen": View(text=response.answer, aggregates=aggregates,
                           rows=list(response.rows), use_locators=True,
                           tool=_result_tool(response)),
            "B_text": View(text=response.answer)}


def _understand(trace: list[TraceStep]) -> dict[str, Any]:
    for step in trace:
        if step.step == "understand":
            return dict(step.facts)
    return {}


def twotrack_detail(response: QueryResponse | None) -> dict[str, Any]:
    if response is None:
        return {}
    decision = response.decision
    facts = _understand(response.trace)
    rule = facts.get("rule") or {}
    return {
        "decision": None if decision is None else {
            "kind": decision.kind.value, "target": decision.target,
            "params": dict(decision.params), "source": decision.source.value,
            "confidence": round(decision.confidence, 3),
            "clarify_options": list(decision.clarify_options)},
        "rule_strength": rule.get("strength") if isinstance(rule, dict) else None,
        "answer_source": response.answer_source,
        "narrative_stripped": response.narrative_stripped,
        "rows_shown": len(response.rows),
        "aggregates": dict(response.aggregates),
        "scope": response.provenance.scope if response.provenance else None,
    }


def model_errors_of(response: QueryResponse | None) -> list[dict[str, Any]]:
    """twotrack's own account of the model calls that failed while answering (the call, the
    error class, the HTTP status): its answer fell back without them."""
    if response is None:
        return []
    return [error.model_dump(mode="json") for error in response.model_errors]


def routed_ok(item: Item, response: QueryResponse | None, *,
              strict: bool = False) -> bool | None:
    """B routing accuracy (answerable only): the decision runs one of the item's accepted
    targets (``strict``: exactly the expected one)."""
    if not item.answerable:
        return None
    decision = response.decision if response is not None else None
    if decision is None or decision.kind.value not in ("tool", "workflow"):
        return False
    if strict:
        return decision.target == item.expected_tool
    return decision.target in item.accepted_tools


# ---------------------------------------------------------------- A evidence and C: the lock


def baseline_evidence(run: BaselineRun) -> Evidence:
    outputs = [(name, Pool(result_numbers(r.rows, r.aggregates, r.provenance.row_count)))
               for name, r in run.results]
    return Evidence(outputs, Pool(text_numbers(run.payloads)))


def literal_numbers(payloads: list[str]) -> list[float]:
    """Every number present in the tool outputs the baseline model read (rows included)."""
    out: list[float] = []

    def walk(value: Any) -> None:
        if is_number(value):
            out.append(float(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for payload in payloads:
        try:
            walk(json.loads(payload))
        except ValueError:
            continue
    return out


@dataclass
class AblationRun:
    answer: str
    stripped: int
    allowed_count: int


def ablate(run: BaselineRun) -> AblationRun:
    allowed = literal_numbers(run.payloads)
    locked = lock_narrative(run.answer, allowed)
    text = locked.text if locked.text.strip() else (DRAFT_STRIPPED if run.answer.strip()
                                                     else "")
    return AblationRun(answer=text, stripped=locked.stripped_count,
                       allowed_count=len(set(allowed)))


def baseline_detail(run: BaselineRun) -> dict[str, Any]:
    return {"turns": run.turns, "stop": run.stop,
            "tool_calls": [{"name": c.name, "arguments": c.arguments, "status": c.status,
                            "payload_chars": c.payload_chars} for c in run.calls]}


def tool_hit(item: Item, run: BaselineRun) -> bool | None:
    """Diagnostic for A/C: an accepted tool was called at least once with an ok result."""
    if not item.answerable:
        return None
    return any(c.name in item.accepted_tools and c.status == "ok" for c in run.calls)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
