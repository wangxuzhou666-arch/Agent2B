"""``twotrack demo``: a scripted walkthrough, in process, on the zero-key mock provider.

It builds a throwaway runtime (temporary data directory, seeded synthetic data, mock provider,
no server) and shows, for a handful of questions, the decision ladder (every trace step with
its route source and timing), the answer and its provenance. Then it makes the mock invent a
figure to show the numeric lock removing it, and walks a write through confirmation, approval,
execution and the audit chain that links them. The write names create_po by one phrase and
gives every param it requires, so even with no model it goes straight to the one-time
confirmation; should routing offer it as a choice first (weaker evidence), the walkthrough picks
it the way the console would. Colour only when stdout is a terminal (and ``NO_COLOR`` is
unset). The exit status is 1 if any of the demonstrated guarantees did not hold, so ``make
demo`` doubles as a smoke test.
"""

import dataclasses
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from twotrack.core import Principal, TraceStatus, TraceStep
from twotrack.core.audit import AuditQuery, AuditStatus
from twotrack.core.graph import QueryResponse, run_query
from twotrack.core.guards.numeric_lock import LockResult, lock_narrative
from twotrack.providers import MockProvider, provider_info
from twotrack.runtime import Runtime, build_runtime
from twotrack.settings import Settings

QUERIES = (
    "which SKUs are low on stock",  # keyword rule
    "哪些采购单逾期了",  # keyword rule, Chinese question -> Chinese narration
    "build a reorder plan",  # a workflow: deterministic steps, then narration
    "rank our sources by rating",  # no keyword: the semantic fallback picks the target
    "write a haiku about the sea",  # nothing fits: chat, honest that no model is configured
)
LOCK_QUERY = "how many units are on hand in WH-EAST"
INVENTED_FIGURE = 31337
DEMO_USER = "demo-operator"

_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
_STATUS_COLOUR = {TraceStatus.OK: "green", TraceStatus.SKIPPED: "yellow",
                  TraceStatus.BLOCKED: "yellow", TraceStatus.ERROR: "red"}


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        codes = ";".join(_CODES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"


def use_colour(stream: TextIO) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


# ---------------------------------------------------------------- recording doubles


class RecordingProvider:
    """The mock provider, keeping every draft it narrated (before the numeric lock)."""

    def __init__(self, inner: MockProvider) -> None:
        self.inner, self.drafts = inner, []
        info = provider_info(inner)  # labels pass through: it is still the mock
        self.name, self.mock = info.name, info.mock

    def route(self, query, tool_cards):
        return self.inner.route(query, tool_cards)

    def narrate(self, question, view):
        text = self.inner.narrate(question, view)
        self.drafts.append(text)
        return text

    def chat(self, messages):
        return self.inner.chat(messages)

    def agent_step(self, messages, tool_cards):
        return self.inner.agent_step(messages, tool_cards)


def recording_lock(results: list[LockResult]) -> Callable[[str, Any], LockResult]:
    def lock(text: str, allowed: Any) -> LockResult:
        out = lock_narrative(text, allowed)
        results.append(out)
        return out
    return lock


# ---------------------------------------------------------------- rendering


def source_label(source: Any, mock: bool) -> str:
    """How the demo names an answer source: text the zero-key mock "narrated" is its
    deterministic template, so it is never credited to a model."""
    return "mock template" if mock and source == "model" else str(source)


def _mock_narrated(trace: list[TraceStep]) -> bool:
    """Whether the mock provider stood behind this answer (narrate or workflow llm step)."""
    return any(s.facts.get("mock") is True for s in trace
               if s.step == "narrate" or (s.step == "execute" and s.facts.get("kind") == "llm"))


def _step_summary(step: TraceStep, mock: bool = False) -> str:
    facts = step.facts
    if step.step == "candidates":
        return f"{facts.get('count', '?')} targets this user may reach"
    if step.step == "understand":
        target = facts.get("target") or facts.get("kind")
        options = facts.get("options") or []
        detail = f"options {', '.join(options)}" if options else facts.get("reason", "")
        confidence = facts.get("confidence")
        head = f"{step.source} -> {target}" + (f" ({confidence})" if confidence else "")
        return f"{head}: {detail}" if detail else head
    if step.step == "gate":
        if step.status is TraceStatus.OK:
            return f"{facts.get('effect')}, confirmation {facts.get('confirmation')}"
        if "clarify" in facts:
            return f"ask the user: {', '.join(facts['clarify'])}"
        return f"stopped at {facts.get('stage', 'gate')}"
    if step.step == "execute":
        if "workflow" in facts:
            return f"{facts['workflow']}.{facts['step']} ({facts['kind']})"
        if "row_count" in facts:
            return f"{facts['row_count']} rows computed"
        return ", ".join(f"{k}={v}" for k, v in facts.items())
    if step.step == "narrate":
        return (f"{source_label(facts.get('source'), facts.get('mock') is True)}, numeric lock "
                f"removed {facts.get('stripped', 0)}")
    if step.step == "respond":
        return (f"answer from {source_label(facts.get('answer_source'), mock)}, "
                f"{facts.get('rows', 0)} rows to the table")
    return ", ".join(f"{k}={v}" for k, v in facts.items())


class Printer:
    def __init__(self, out: TextIO, style: Style) -> None:
        self.out, self.s = out, style

    def line(self, text: str = "") -> None:
        print(text, file=self.out)

    def heading(self, label: str, text: str) -> None:
        self.line()
        self.line(f"{self.s(label, 'bold', 'cyan')} {self.s(text, 'bold')}")

    def field(self, label: str, text: str, *styles: str) -> None:
        self.line(f"  {self.s(f'{label:<12}', 'dim')} {self.s(text, *styles)}")

    def ladder(self, trace: list[TraceStep]) -> None:
        mock = _mock_narrated(trace)
        for step in trace:
            status = self.s(f"{step.status.value:<8}", _STATUS_COLOUR[step.status])
            ms = f"{step.duration_ms:7.1f} ms" if step.duration_ms else " " * 10
            self.line(f"    {step.step:<11}{status}{self.s(ms, 'dim')}  "
                      f"{_step_summary(step, mock)}")

    def provenance(self, response: QueryResponse) -> None:
        p = response.provenance
        if p is not None:
            self.field("provenance", f"{p.tool} | {p.row_count} rows | as of "
                       f"{p.as_of:%Y-%m-%d %H:%M} UTC | {p.scope}", "dim")


# ---------------------------------------------------------------- the walkthrough


class Demo:
    def __init__(self, runtime: Runtime, out: Printer) -> None:
        self.runtime, self.p = runtime, out
        self.principal: Principal = _demo_principal()
        self.failed: list[str] = []

    def check(self, ok: bool, what: str) -> None:
        if not ok:
            self.failed.append(what)
            self.p.field("check", f"FAILED: {what}", "red", "bold")

    def ask(self, query: str, engine: Any = None, *,
            explicit_target: str | None = None) -> QueryResponse:
        from twotrack.server.context import request_context

        ctx = request_context(self.runtime, self.principal)
        return run_query(engine or self.runtime.engine, ctx, query,
                         explicit_target=explicit_target)

    def show(self, label: str, query: str, response: QueryResponse) -> None:
        self.p.heading(label, query)
        self.p.ladder(response.trace)
        self.p.field("answer", response.answer or (response.error or ""), "bold")
        self.p.provenance(response)
        if response.audit_ids:
            entry = self.runtime.audit.get(response.audit_ids[-1])
            steps = " > ".join(s.step for s in entry.trace) if entry else "?"
            self.p.field("audit", f"#{response.audit_ids[-1]} {entry.status.value if entry else ''}"
                         f" replays: {steps}", "dim")

    def questions(self) -> None:
        for number, query in enumerate(QUERIES, 1):
            response = self.ask(query)
            self.show(f"[{number}/{len(QUERIES)}]", query, response)
            self.check(bool(response.audit_ids), f"'{query}' left an audit record")

    def numeric_lock(self) -> None:
        provider = RecordingProvider(MockProvider(hallucinated_number=INVENTED_FIGURE))
        locks: list[LockResult] = []
        engine = dataclasses.replace(self.runtime.engine, provider=provider,
                                     lock=recording_lock(locks))
        response = self.ask(LOCK_QUERY, engine)
        self.show("[lock]", f"{LOCK_QUERY}   (the mock is told to invent {INVENTED_FIGURE})",
                  response)
        draft = provider.drafts[-1] if provider.drafts else ""
        self.p.field("mock draft" if provider.mock else "model draft", draft, "yellow")
        self.p.field("after lock", response.answer, "green")
        removed = locks[-1].stripped_sentences if locks else []
        noun = "sentence" if len(removed) == 1 else "sentences"
        self.p.field("numeric lock", f"removed {len(removed)} {noun}: "
                     + " | ".join(f'"{s}"' for s in removed), "bold")
        self.check(response.narrative_stripped == 1, "the numeric lock removed 1 sentence")
        self.check(str(INVENTED_FIGURE) not in response.answer,
                   "the invented figure never reached the answer")

    def write(self) -> None:
        from twotrack.server.routes.approvals import run_query_approval

        sku = self.runtime.store.list("sku", sort="sku_id", limit=1).rows[0]["sku_id"]
        # one phrase of the write plus every param it requires (SKU and quantity): a complete
        # request, so routing goes straight to the write and the pipeline holds it at once
        query = f"create a purchase order for 40 units of {sku}"
        response = self.ask(query)
        self.show("[write]", query, response)
        if response.grant is None and [o.name for o in response.clarify_options] == ["create_po"]:
            # weaker evidence (a phrase without every required param) is offered first; picking
            # the option sends the question again with that target, as the console does
            self.p.field("picked", "create_po: the pipeline now asks for its own confirmation",
                         "yellow")
            response = self.ask(query, explicit_target="create_po")
            self.show("[write]", f"{query}   (create_po picked)", response)
        grant = response.grant
        self.check(grant is not None, "the write stopped for confirmation")
        if grant is None:
            return
        from twotrack.core.grants import grant_id_for

        grant_id = grant_id_for(grant.token)
        self.p.field("needs", f"confirmation: {grant.summary}", "yellow", "bold")
        waiting = self.runtime.approvals.awaiting([grant_id])
        self.p.field("approvals", f"{grant_id} waits in the console's Approvals for "
                     f"{self.principal.user_id} (GET /api/approvals); nothing written yet")
        self.check(grant_id in waiting, "the request is listed as a pending approval")
        claimed = self.runtime.approvals.claim(grant_id, user_id=self.principal.user_id,
                                               states=("awaiting",))
        outcome = run_query_approval(self.runtime, self.principal, claimed)
        self.p.field("approved", f"{self.principal.user_id} approves -> {outcome.status}",
                     "green", "bold")
        self.p.field("executed", outcome.answer, "bold")
        self.check(outcome.status == "ok", "the approved write ran")
        self.audit_chain(response.audit_ids[-1] if response.audit_ids else None)

    def audit_chain(self, request_record: int | None) -> None:
        origin = self.runtime.audit.get(request_record) if request_record else None
        if origin is None or not origin.request_id:
            self.check(False, "the write's request is in the audit log")
            return
        chain = list(reversed(self.runtime.audit.list(
            AuditQuery(request_id=origin.request_id), limit=20).items))
        links = " -> ".join(f"#{e.id} {e.status.value}" for e in chain)
        self.p.field("audit chain", f"{links}   (request {origin.request_id[:8]})", "cyan")
        last = chain[-1]
        detail = self.runtime.audit.get(last.id)
        self.p.field(f"replay #{last.id}", " > ".join(
            f"{s.step}:{s.status.value}" for s in detail.trace) + f"   (parent #{last.parent_id})",
            "dim")
        self.check(last.status is AuditStatus.OK and last.parent_id == origin.id,
                   "the execution record points back at the request")

    def summary(self) -> None:
        entries = [e for e in self.runtime.audit.list(limit=500).items
                   if e.status is not AuditStatus.STARTED]
        by_source = Counter(e.route_source or "unknown" for e in entries)
        by_status = Counter(e.status.value for e in entries)
        self.p.heading("[audit]", f"{len(entries)} decisions and runs recorded")
        self.p.field("by source", _counts(by_source))
        self.p.field("by status", _counts(by_status))


def _counts(counter: Counter) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items()))


def _demo_principal() -> Principal:
    from twotrack.server.auth import DEMO_USERS

    return DEMO_USERS[DEMO_USER]


def run_demo(out: TextIO | None = None, *, colour: bool | None = None) -> int:
    stream = out or sys.stdout
    printer = Printer(stream, Style(use_colour(stream) if colour is None else colour))
    with tempfile.TemporaryDirectory(prefix="twotrack-demo-") as tmp:
        runtime = build_runtime(Settings(data_dir=Path(tmp) / "data", demo_mode=True),
                                provider=MockProvider())
        try:
            demo = Demo(runtime, printer)
            printer.line(printer.s("twotrack demo", "bold")
                         + " - LLMs route and narrate; code computes.")
            printer.line(printer.s(f"provider: mock (no API key) | data: seeded synthetic supply "
                                   f"chain in a temporary directory | user: {DEMO_USER}", "dim"))
            demo.questions()
            demo.numeric_lock()
            demo.write()
            demo.summary()
        finally:
            runtime.close()
    printer.line()
    if demo.failed:
        printer.line(printer.s(f"{len(demo.failed)} check(s) failed: "
                               + "; ".join(demo.failed), "red", "bold"))
        return 1
    printer.line(printer.s("all checks held.", "green") + " Next: TWOTRACK_DEMO_MODE=1 "
                 "uv run twotrack serve, then open /docs or the console.")
    return 0
