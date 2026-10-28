"""Shared test doubles for routing, graph and provider tests.

Generic supply-chain fixture plugin plus small fakes for the interfaces the orchestration layer
consumes (pipeline, numeric lock, scrubber) and a scripted provider that records every payload.
Row values are distinctive (ROW_MARKERS) so tests can prove rows never reach a provider.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from twotrack.core import (
    Approval,
    Plugin,
    Precondition,
    Principal,
    Provenance,
    ResourceEffect,
    Step,
    StepKind,
    Tool,
    ToolContext,
    ToolOutputs,
    ToolResult,
    TraceStatus,
    TraceStep,
    Workflow,
    build_registry,
)
from twotrack.core.narration import template_narration
from twotrack.providers.base import AgentStep, LLMRoute, ProviderError

ROW_MARKERS = ("alpha-bolt-zq", "beta-nut-zq", 7771, 7772)
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str | None = None


class AdjustParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str | None = None
    qty: int | None = None


class Brief(BaseModel):
    horizon_weeks: int


def stock_rows(ctx: ToolContext, params: Any) -> ToolResult:
    rows = [{"item": "alpha-bolt-zq", "qty": 7771}, {"item": "beta-nut-zq", "qty": 7772}]
    return ToolResult(
        rows=rows, columns=["item", "qty"], aggregates={"total_units": 15543, "item_count": 2},
        provenance=Provenance(tool="stock_level", row_count=2, as_of=ctx.now(), scope="all sites"),
    )


def simple_result(name: str, **aggregates: float) -> Any:
    def handler(ctx: ToolContext, params: Any) -> ToolResult:
        return ToolResult(aggregates=aggregates,
                          provenance=Provenance(tool=name, row_count=0, as_of=ctx.now()))
    return handler


def item_extractor(query: str) -> dict[str, Any]:
    match = re.search(r"\bitem\s+([a-z0-9-]+)", query.lower())
    return {"item": match.group(1)} if match else {}


def plan_step(ctx: ToolContext, data: Any) -> ToolResult:
    result = stock_rows(ctx, data)
    return result.model_copy(update={"aggregates": {"units_to_order": 420}})


def brief_step(ctx: ToolContext, data: Brief) -> ToolResult:
    return ToolResult(aggregates={"horizon_weeks": data.horizon_weeks},
                      provenance=Provenance(tool="demand_brief", row_count=0, as_of=ctx.now()))


def tool(name: str, effect: str = "read", roles=("viewer", "operator"), **kw: Any) -> Tool:
    """A fixture tool: a read needs no approval, a write needs one, nothing is explorable unless
    it says so and the default handler returns one aggregate, ``count``. Any field can be
    overridden."""
    base = dict(description=f"{name.replace('_', ' ')} report", params_model=NoParams,
                handler=simple_result(name, count=1), roles=roles, effect=effect,
                approval="required" if effect == "write" else "none",
                outputs=ToolOutputs(aggregates=("count",), columns=()), explorable=False)
    return Tool(name=name, **{**base, **kw})


TOOLS = (
    tool("stock_level", description="Current stock on hand per item across sites.",
         params_model=ItemParams, handler=stock_rows, param_extractor=item_extractor,
         outputs=ToolOutputs(aggregates=("total_units", "item_count"), columns=("item", "qty")),
         explorable=True, keywords=("stock level", "on hand", "inventory"),
         examples=("what is our stock level", "how much inventory is on hand")),
    tool("order_status", description="Status of open purchase orders.",
         keywords=("order status", "open orders", "purchase orders"),
         examples=("show open orders",)),
    tool("stock_adjust", effect="write", roles=("operator",), params_model=AdjustParams,
         description="Adjust the recorded stock quantity of an item.",
         keywords=("adjust stock", "inventory correction"),
         examples=("adjust stock for an item",)),
    tool("vendor_lookup", description="Look up vendors from an uploaded vendor file.",
         keywords=("vendor", "supplier"), requires=("has_vendor_file",)),
    tool("admin_purge", effect="write", roles=("admin",), description="Purge archived records.",
         keywords=("purge archived",)),
    tool("lead_time", description="Average supplier lead time in days for deliveries.",
         keywords=("lead time",)),
    tool("delivery_delay", description="Late deliveries and delay counts by carrier.",
         keywords=("lead time", "late deliveries")),
)

WORKFLOWS = (
    Workflow(name="reorder_plan", description="Compute what to reorder, then explain it.",
             roles=("operator",), effect=ResourceEffect.READ, approval=Approval.NONE,
             outputs=ToolOutputs(aggregates=("units_to_order",), columns=("item", "qty")),
             explorable=False, keywords=("reorder plan", "restock"),
             steps=(Step(id="compute", kind=StepKind.DETERMINISTIC, description="compute",
                         fn=plan_step),
                    Step(id="explain", kind=StepKind.LLM,
                         description="Explain which items to reorder first.",
                         fn=lambda ctx, data: data))),
    Workflow(name="demand_brief", description="Turn a request into a demand brief.",
             roles=("operator",), effect=ResourceEffect.READ, approval=Approval.NONE,
             outputs=ToolOutputs(aggregates=("horizon_weeks",), columns=()), explorable=False,
             keywords=("demand brief",),
             steps=(Step(id="understand", kind=StepKind.LLM, description="read the horizon",
                         fn=lambda ctx, data: ctx.facts.get("query", ""), output_model=Brief),
                    Step(id="compute", kind=StepKind.DETERMINISTIC, description="compute",
                         fn=brief_step))),
)

PLUGIN = Plugin(
    name="supply_fixture", version="0.1.0", tools=TOOLS, workflows=WORKFLOWS,
    preconditions=(Precondition(name="has_vendor_file", description="Upload a vendor file first.",
                                check=lambda ctx: bool(ctx.facts.get("vendor_file"))),),
)

REGISTRY = build_registry([PLUGIN])


def make_ctx(*roles: str, sink=None, **facts: Any) -> ToolContext:
    return ToolContext(principal=Principal(user_id="u1", roles=frozenset(roles or ("operator",))),
                       facts=facts, clock=lambda: NOW, trace=sink or (lambda step: None))


# ---------------------------------------------------------------- numeric lock / scrubber


@dataclass
class Locked:
    text: str
    stripped_count: int


def fake_lock(text: str, allowed: set[float]) -> Locked:
    """Drop every sentence that cites a number outside ``allowed``."""
    kept, stripped = [], 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", sentence)]
        if all(n in allowed for n in numbers):
            kept.append(sentence)
        else:
            stripped += 1
    return Locked(" ".join(s for s in kept if s), stripped)


@dataclass
class Scrubbed:
    text: str
    redacted_count: int


@dataclass
class RecordingScrubber:
    seen: list[str] = field(default_factory=list)

    def scrub(self, payload: str) -> Scrubbed:
        self.seen.append(payload)
        text, count = re.subn(r"[\w.]+@[\w.]+", "[email]", payload)
        return Scrubbed(text, count)


# ---------------------------------------------------------------- pipeline


@dataclass
class FakeGrant:
    token: str
    expires_at: datetime
    summary: str


@dataclass
class FakeOutcome:
    status: str
    result: ToolResult | None = None
    grant: FakeGrant | None = None
    trace: list[TraceStep] = field(default_factory=list)
    problem: str | None = None


class FakePipeline:
    """Enough of the real pipeline's contract: authz, write grant, params, execute."""

    GRANT = "grant-ok"

    def __init__(self, registry=REGISTRY) -> None:
        self.registry = registry
        self.runs: list[tuple[str, dict]] = []
        self.sources: list = []
        self.priors: list[list[str]] = []  # step names of each run's prior_trace

    def run(self, ctx, target, params, *, grant_token=None, workflow_runner=None,
            route_source=None, prior_trace=(), parent_id=None):
        self.runs.append((target, dict(params)))
        self.sources.append(route_source)
        self.priors.append([s.step for s in prior_trace])
        if target not in self.registry.candidates(ctx):
            return FakeOutcome("blocked", problem=f"'{target}' is not allowed")
        item = self.registry.target(target)
        if item.approval is Approval.REQUIRED and grant_token != self.GRANT:
            return FakeOutcome("needs_confirmation", grant=FakeGrant(
                self.GRANT, NOW + timedelta(minutes=5), f"{target} with {json.dumps(params)}"))
        if isinstance(item, Workflow):
            result = workflow_runner(ctx, item, params)
        else:
            try:
                parsed = item.parse_params(params)
            except ValidationError as exc:
                return FakeOutcome("invalid", problem=f"{exc.error_count()} invalid param(s)")
            result = item.handler(ctx, parsed)
        step = TraceStep(step="execute", status=TraceStatus.OK, facts={"target": target})
        ctx.emit(step)  # emitted AND returned: the graph must not record it twice
        return FakeOutcome("ok", result=result, trace=[step])


# ---------------------------------------------------------------- provider


class ScriptedProvider:
    """Replays scripted replies and records every payload it receives."""

    def __init__(self, *, route: LLMRoute | None = None, narration: str | None = None,
                 chat_text: str = "Happy to help.", steps=None, fail=()) -> None:
        self.route_reply = route
        self.narration = narration
        self.chat_text = chat_text
        self.steps = steps if callable(steps) else list(steps or [])
        self.fail = set(fail)
        self.received: list[tuple[str, str]] = []  # (method, serialized payload)

    def _record(self, method: str, *payload: Any) -> None:
        self.received.append((method, json.dumps(payload, default=lambda o: o.model_dump())))
        if method in self.fail:
            raise ProviderError(f"{method} failed")

    def route(self, query, tool_cards):
        self._record("route", query, [c.name for c in tool_cards])
        return self.route_reply

    def narrate(self, question, view):
        self._record("narrate", question, view)
        return self.narration if self.narration is not None else template_narration(view)

    def chat(self, messages):
        self._record("chat", list(messages))
        return self.chat_text

    def agent_step(self, messages, tool_cards):
        self._record("agent_step", list(messages), [c.name for c in tool_cards])
        if callable(self.steps):
            return self.steps(messages, tool_cards)
        return self.steps.pop(0) if self.steps else AgentStep(text="Done.")

    def payloads(self, method: str | None = None) -> str:
        return "\n".join(p for m, p in self.received if method in (None, m))
