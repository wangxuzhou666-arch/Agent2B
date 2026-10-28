"""Drop-in example: a new read tool, added without touching core or built-in code.

Load it by pointing the plugin path at this directory and restarting::

    TWOTRACK_PLUGIN_PATHS=examples/plugins

``supplier_scorecard`` brings no entity, category or column. It computes from the purchase orders
and suppliers the built-in plugins already store: for one supplier, or for all of them, the
orders placed in the last ``window_days`` days, how many of those are due, how many of the due
ones have arrived, and how long the rest are past their ETA. The output is figures only
(``rows`` stays empty), so everything a model or a table could show is an aggregate that code
computed.

An order is due once it is received, or once its ETA is behind us while it is still open or
shipped (``days_late``, the rule ``late_shipments`` uses, so the two tools never disagree on what
late means). A draft is not placed yet and a cancelled order owes nothing: neither is due.
The base model keeps no receipt date, so "on time" cannot be measured: a received order counts as
received whenever it arrived. The share is an upper bound of the on-time rate, and the labels
say "received", not "on time".
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from twotrack.core import (
    Approval,
    Plugin,
    Precondition,
    Provenance,
    ResourceEffect,
    Tool,
    ToolContext,
    ToolOutputs,
    ToolResult,
)
from twotrack.core.errors import InputProblem
from twotrack.plugins.supply_chain.entities import LATE_STATUSES
from twotrack.plugins.supply_chain.extract import extractor
from twotrack.plugins.supply_chain.logic import days_late, fetch_all

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
DEFAULT_WINDOW_DAYS = 90
LEADER_COUNT = 3  # suppliers named when the question covers all of them
_WINDOW = re.compile(r"(\d{1,3})\s*[- ]?\s*(?:days?|天)", re.IGNORECASE)
read_supplier_id = extractor("supplier_id")  # the built-in tools' reader: routing and params agree


class ScorecardParams(BaseModel):
    """Unknown keys are rejected: a misnamed filter must not silently widen the scorecard."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: Code | None = None
    window_days: int = Field(default=DEFAULT_WINDOW_DAYS, ge=1, le=365, json_schema_extra={
        "label": {"en": "Window (days)", "zh": "统计窗口（天）"}})


@dataclass
class Tally:
    """The orders of one supplier, or of all, reduced to counts."""

    placed: int = 0
    received: int = 0
    days_overdue: list[int] = field(default_factory=list)

    @property
    def due(self) -> int:
        return self.received + len(self.days_overdue)

    @property
    def received_pct(self) -> float:
        return round(100 * self.received / self.due, 1)

    def add(self, other: "Tally") -> None:
        self.placed += other.placed
        self.received += other.received
        self.days_overdue += other.days_overdue


def say(ctx: ToolContext, en: str, zh: str) -> str:
    """The same sentence in the request language."""
    return zh if ctx.language == "zh" else en


def labels(**names: tuple[str, str]) -> dict[str, dict[str, str]]:
    """``aggregate_labels`` from ``key=(english, chinese)`` pairs."""
    return {"en": {key: en for key, (en, _) in names.items()},
            "zh": {key: zh for key, (_, zh) in names.items()}}


def placed_in_window(order: dict[str, Any], since: date, today: date) -> bool:
    ordered_at = order.get("ordered_at")  # optional in the base model
    return (order["status"] != "draft" and bool(ordered_at)
            and since <= date.fromisoformat(ordered_at) <= today)


def count_order(tally: Tally, order: dict[str, Any], today: date) -> None:
    tally.placed += 1
    if order["status"] == "received":
        tally.received += 1
    elif order["status"] in LATE_STATUSES:
        late = days_late(order.get("eta"), today)
        if late is not None:
            tally.days_overdue.append(late)


def figures(total: Tally, window_days: int, suppliers: int | None) -> dict[str, int | float]:
    """Every number the answer may cite. A share or an average that has nothing to divide is
    left out rather than reported as 0."""
    out: dict[str, int | float] = {} if suppliers is None else {"supplier_count": suppliers}
    out |= {"po_count": total.placed, "due_count": total.due, "received_count": total.received,
            "overdue_count": len(total.days_overdue)}
    if total.due:
        out["received_rate_pct"] = total.received_pct
    if total.days_overdue:
        out["avg_days_overdue"] = round(fmean(total.days_overdue), 1)
        out["max_days_overdue"] = max(total.days_overdue)
    out["window_days"] = window_days
    return out


def leaders(by_supplier: dict[str, Tally]) -> list[str]:
    """Suppliers with due orders, highest share received first; ties go to the one with more
    due orders, then to the lower id. A share over few orders proves little: the counts of one
    supplier are one call away."""
    ranked = sorted(((sid, t) for sid, t in by_supplier.items() if t.due),
                    key=lambda pair: (-pair[1].received_pct, -pair[1].due, pair[0]))
    return [sid for sid, _ in ranked[:LEADER_COUNT]]


def note(ctx: ToolContext, params: ScorecardParams, total: Tally,
         by_supplier: dict[str, Tally]) -> str:
    """What the figures cannot say. Identifiers only, never a free number: a model that echoes
    the message cannot trip the numeric lock."""
    if not total.placed:
        return say(ctx, "No purchase order was placed in this window.",
                   "该时间窗口内没有下达的采购单。")
    if not total.due:
        return say(ctx, "No order is due yet, so there is no share to report.",
                   "还没有到期的采购单，因此没有占比可报告。")
    if params.supplier_id is not None:
        return ""
    names = say(ctx, ", ", "、").join(leaders(by_supplier))
    return say(ctx, f"Highest share of due orders received: {names}.",
               f"到期采购单已收货占比最高的供应商：{names}。")


def supplier_scorecard(ctx: ToolContext, params: ScorecardParams) -> ToolResult:
    today = ctx.now().date()
    since = today - timedelta(days=params.window_days)
    if params.supplier_id is not None and ctx.store.get("supplier", params.supplier_id) is None:
        raise InputProblem(say(ctx, f"There is no supplier '{params.supplier_id}'.",
                               f"没有供应商“{params.supplier_id}”。"))
    only = None if params.supplier_id is None else {"supplier_id": params.supplier_id}
    by_supplier: dict[str, Tally] = defaultdict(Tally)
    for order in fetch_all(ctx.store, "purchase_order", only):
        if placed_in_window(order, since, today):
            count_order(by_supplier[order["supplier_id"]], order, today)
    total = Tally()
    for tally in by_supplier.values():
        total.add(tally)
    who = params.supplier_id or say(ctx, "all suppliers", "全部供应商")
    return ToolResult(
        aggregates=figures(total, params.window_days,
                           len(by_supplier) if params.supplier_id is None else None),
        message=note(ctx, params, total, by_supplier),
        provenance=Provenance(
            tool="supplier_scorecard", row_count=total.placed, as_of=ctx.now(),
            scope=say(ctx, f"orders placed {since} to {today}, {who}",
                      f"{since} 至 {today} 下达的采购单，{who}")),
    )


def read_params(query: str) -> dict[str, Any]:
    """Raw params from the question; the pipeline still validates them (a window of 500 days is
    read here and refused there)."""
    window = _WINDOW.search(query)
    return {**read_supplier_id(query), **({"window_days": int(window.group(1))} if window else {})}


HAS_PURCHASE_ORDERS = Precondition(
    name="has_purchase_orders",
    description="No purchase orders on file yet. Import purchase orders (or load the demo "
                "data) first.",
    check=lambda ctx: ctx.store.count("purchase_order") > 0,
)

SUPPLIER_SCORECARD = Tool(
    name="supplier_scorecard",
    title={"en": "Supplier scorecard", "zh": "供应商评分卡"},
    description={
        "en": "Delivery scorecard of one supplier, or of all suppliers, for the purchase orders "
              "placed in the last N days (default 90): how many were placed, how many are due "
              "(received, or still open or shipped past their ETA), how many of those arrived, "
              "and how many days the rest are past their ETA. Figures only, no order list.",
        "zh": "某个供应商（或全部供应商）在最近 N 天（默认 90 天）内所下采购单的交货记分卡："
              "下达的采购单数、其中到期的数量（已收货，或已下达、已发货但超过预计到货日期）、"
              "其中已收货的数量，以及其余采购单超过预计到货日期的天数。只给汇总数字，不列出采购单。"},
    route_hint="Use for how reliably suppliers deliver over a recent window: the share of due "
               "orders received and the days past ETA of the rest, for one supplier or all. "
               "For the list of overdue orders use late_shipments; to rank suppliers by rating "
               "and lead time use supplier_compare.",
    # phrases and words that name this report, never a bare "supplier" or "late" (those belong
    # to supplier_compare and late_shipments)
    keywords=("scorecard", "scorecards", "supplier scorecard", "supplier scorecards",
              "on-time delivery", "on-time deliveries", "on-time rate", "otd",
              "delivery performance", "supplier performance", "delivers on time",
              "deliver on time", "reliability", "reliable", "reliably", "average delay",
              "评分卡", "供应商评分卡", "供应商绩效", "供应商表现", "交货表现", "交付表现", "履约",
              "准时交货", "准时交付", "准时到货", "按时交货", "按时交付", "按时到货", "准时率",
              "可靠", "平均延迟", "平均逾期"),
    examples=("supplier scorecard for the last 90 days", "how often does SUP-001 deliver on time",
              "on-time delivery rate by supplier", "供应商交货评分卡",
              "SUP-001 近 90 天的准时交付率", "各供应商的履约表现"),
    param_extractor=read_params,
    roles=("admin", "operator"),
    effect=ResourceEffect.READ,
    approval=Approval.NONE,  # a read: there is nothing to approve
    # figures only: every key ``figures`` can return, and no rows, so no columns
    outputs=ToolOutputs(
        aggregates=("supplier_count", "po_count", "due_count", "received_count", "overdue_count",
                    "received_rate_pct", "avg_days_overdue", "max_days_overdue", "window_days"),
        columns=()),
    explorable=True,  # a read-only report is fine to offer to open analysis
    requires=("has_purchase_orders",),
    params_model=ScorecardParams,
    handler=supplier_scorecard,
    entity="supplier",
    aggregate_labels=labels(
        supplier_count=("Suppliers with orders", "有采购单的供应商"),
        po_count=("Purchase orders placed (drafts excluded)", "已下达的采购单（不含草稿）"),
        due_count=("Due orders", "到期采购单"),
        received_count=("Due orders received", "其中已收货"),
        overdue_count=("Due orders still outstanding", "其中仍未到货"),
        received_rate_pct=("Share of due orders received (%)", "到期采购单已收货占比（%）"),
        avg_days_overdue=("Average days past ETA", "平均逾期天数"),
        max_days_overdue=("Longest days past ETA", "最长逾期天数"),
        window_days=("Window (days)", "统计窗口（天）")),
)

PLUGIN = Plugin(
    name="supplier_scorecard",
    version="0.1.0",
    description="Drop-in example: a supplier delivery scorecard computed from purchase orders.",
    tools=(SUPPLIER_SCORECARD,),
    preconditions=(HAS_PURCHASE_ORDERS,),
)
