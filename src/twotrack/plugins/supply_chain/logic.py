"""Deterministic supply-chain math. Every number a tool or workflow reports comes from here.

Definitions used everywhere (and repeated in tool descriptions so the router's cards and the
UI explain the same thing):

* ``available``   = sum over warehouses of (on_hand - reserved). A SKU with no inventory rows
  has 0 available. Missing on_hand/reserved count as 0. It is not clamped: a negative value
  means more is reserved than is on hand.
* ``open_po_qty`` = sum of qty on purchase orders whose status is draft, open or shipped
  (ordered, not yet received). Drafts count so a draft raised by ``create_po`` stops the SKU
  from being suggested twice.
* low stock       : ``available + open_po_qty < reorder_point`` (strictly below; a SKU without
  a reorder point is never flagged).
* suggestion      : ``suggested_qty = max(0, ceil(avg_daily_demand * lead_time_days
  + reorder_point - available - open_po_qty))``. Missing demand, lead time or reorder point
  count as 0. The raw value is rounded to 6 decimals before ``ceil`` so float noise
  (0.1 * 3 = 0.30000000000000004) cannot add a unit.
* supplier rank   : rating descending, then avg_lead_time_days ascending, then supplier_id
  (missing rating or lead time sorts last).
* category        : a SKU is in category C when its row belongs to C or to a category that
  extends C (``sku_filter``). Every tool that filters by category uses that one function.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from twotrack.core.errors import InputProblem
from twotrack.core.store import StoreError

from .entities import CRITICALITY, INCOMING_STATUSES

PAGE_SIZE = 1000  # SqliteStore.list caps a page at 1000 rows
FIRST_PO_NUMBER = 20001
_PO_ID = re.compile(r"^PO-(\d+)$")


def fetch_all(store: Any, entity_id: str, filters: Mapping[str, Any] | None = None,
              sort: str | list[str] | None = None) -> list[dict[str, Any]]:
    """Every matching row, paging through ``Store.list`` (works with any Store)."""
    rows: list[dict[str, Any]] = []
    while True:
        page = store.list(entity_id, filters=filters, sort=sort, limit=PAGE_SIZE,
                          offset=len(rows))
        rows.extend(page.rows)
        if not page.rows or len(rows) >= page.total:
            return rows


def _num(value: Any) -> float:
    return 0.0 if value is None else float(value)


def _int(value: Any) -> int:
    return 0 if value is None else int(value)


def criticality_rank(value: str | None) -> int:
    return CRITICALITY.index(value) if value in CRITICALITY else len(CRITICALITY)


def sku_filter(store: Any, category: str | None,
               sku_id: str | None = None) -> list[str] | None:
    """The SKU ids a request covers: ``None`` means every SKU; otherwise the SKUs of
    ``category`` (sub-categories included), narrowed to ``sku_id`` when one is given.

    The pipeline already rejects a category the registry does not know; an unknown one that
    still reaches here (a direct handler call) is a user-fixable InputProblem."""
    if category is None:
        return None if sku_id is None else [sku_id]
    try:
        ids = {i for i in store.column_values(category, "sku_id") if isinstance(i, str)}
    except StoreError:
        raise InputProblem(f"Unknown category '{category}'.") from None
    if sku_id is not None:
        return [sku_id] if sku_id in ids else []
    return sorted(ids)


def scoped_filters(store: Any, filters: Mapping[str, Any],
                   category: str | None) -> dict[str, Any]:
    """Store filters of a listing keyed by ``sku_id``: ``filters`` without ``category``, with
    ``sku_id`` narrowed to the category's SKUs (``sku_filter``)."""
    out = {k: v for k, v in filters.items() if k not in ("category", "sku_id")}
    skus = sku_filter(store, category, filters.get("sku_id"))
    if skus is not None:
        out["sku_id"] = skus
    return out


# ---------------------------------------------------------------- stock positions


class StockPosition(BaseModel):
    """One SKU's supply picture, summed across warehouses."""

    model_config = ConfigDict(frozen=True)

    sku_id: str
    name: str | None = None
    category: str = "sku"  # the entity id the row belongs to (spare_part, packaging, ...)
    criticality: str | None = None
    unit: str | None = None
    unit_cost: float | None = None
    avg_daily_demand: float = 0.0
    lead_time_days: int = 0
    reorder_point: int | None = None
    on_hand: int = 0
    reserved: int = 0
    open_po_qty: int = 0

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved

    @property
    def projected(self) -> int:
        return self.available + self.open_po_qty

    @property
    def is_low(self) -> bool:
        return self.reorder_point is not None and self.projected < self.reorder_point

    @property
    def shortfall(self) -> int:
        """Units below the reorder point (0 when not low)."""
        if self.reorder_point is None:
            return 0
        return max(0, self.reorder_point - self.projected)


def build_positions(skus: Iterable[Mapping[str, Any]], inventory: Iterable[Mapping[str, Any]],
                    purchase_orders: Iterable[Mapping[str, Any]]) -> list[StockPosition]:
    """Pure: rows in, positions out (sorted by sku_id). Inventory/POs of unknown SKUs are
    ignored; only POs with an incoming status count toward open_po_qty."""
    on_hand: Counter[str] = Counter()
    reserved: Counter[str] = Counter()
    incoming: Counter[str] = Counter()
    for row in inventory:
        on_hand[row["sku_id"]] += _int(row.get("on_hand"))
        reserved[row["sku_id"]] += _int(row.get("reserved"))
    for row in purchase_orders:
        if row.get("status") in INCOMING_STATUSES:
            incoming[row["sku_id"]] += _int(row.get("qty"))
    out = []
    for sku in skus:
        sid = sku["sku_id"]
        out.append(StockPosition(
            sku_id=sid, name=sku.get("name"), category=sku.get("_entity") or "sku",
            criticality=sku.get("criticality"), unit=sku.get("unit"),
            unit_cost=sku.get("unit_cost"), avg_daily_demand=_num(sku.get("avg_daily_demand")),
            lead_time_days=_int(sku.get("lead_time_days")),
            reorder_point=sku.get("reorder_point"), on_hand=on_hand[sid],
            reserved=reserved[sid], open_po_qty=incoming[sid]))
    return sorted(out, key=lambda p: p.sku_id)


def stock_positions(store: Any, sku_ids: Iterable[str] | None = None) -> list[StockPosition]:
    """Load the rows a position needs (optionally for some SKUs only) and build positions."""
    scope = {"sku_id": list(sku_ids)} if sku_ids is not None else {}
    return build_positions(
        fetch_all(store, "sku", scope or None),
        fetch_all(store, "inventory", scope or None),
        fetch_all(store, "purchase_order", {**scope, "status": list(INCOMING_STATUSES)}),
    )


def low_stock_positions(positions: Iterable[StockPosition],
                        criticality: str | None = None) -> list[StockPosition]:
    """Low SKUs, most urgent first: criticality A->C, then largest shortfall, then sku_id."""
    low = [p for p in positions if p.is_low and criticality in (None, p.criticality)]
    return sorted(low, key=lambda p: (criticality_rank(p.criticality), -p.shortfall, p.sku_id))


def suggested_qty(p: StockPosition) -> int:
    raw = (p.avg_daily_demand * p.lead_time_days + (p.reorder_point or 0)
           - p.available - p.open_po_qty)
    return max(0, math.ceil(round(raw, 6)))


def money(value: float) -> float:
    return round(value, 2)


# ---------------------------------------------------------------- suppliers


class SupplierBook:
    """Suppliers plus per-SKU purchase history, loaded once and ranked in memory."""

    def __init__(self, suppliers: Iterable[Mapping[str, Any]],
                 purchase_orders: Iterable[Mapping[str, Any]]) -> None:
        self.suppliers: dict[str, dict[str, Any]] = {s["supplier_id"]: dict(s)
                                                     for s in suppliers}
        self.po_count: dict[str, Counter[str]] = defaultdict(Counter)
        # (sku, supplier) -> (ordered_at, po_id, unit_price) of the latest priced PO
        self._last: dict[tuple[str, str], tuple[str, str, float]] = {}
        for po in purchase_orders:
            sku, sup = po["sku_id"], po["supplier_id"]
            if sup not in self.suppliers:
                continue
            self.po_count[sku][sup] += 1
            if po.get("unit_price") is None:
                continue
            stamp = (po.get("ordered_at") or "", po["po_id"], float(po["unit_price"]))
            current = self._last.get((sku, sup))
            if current is None or stamp[:2] >= current[:2]:
                self._last[sku, sup] = stamp

    @classmethod
    def load(cls, store: Any, sku_id: str | None = None) -> "SupplierBook":
        filters = {"sku_id": sku_id} if sku_id is not None else None
        return cls(fetch_all(store, "supplier"), fetch_all(store, "purchase_order", filters))

    @staticmethod
    def rank_key(s: Mapping[str, Any]) -> tuple[float, float, str]:
        rating = s.get("rating")
        lead = s.get("avg_lead_time_days")
        return (-rating if rating is not None else math.inf,
                lead if lead is not None else math.inf, s["supplier_id"])

    def ranked(self, supplier_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        ids = self.suppliers if supplier_ids is None else supplier_ids
        return sorted((self.suppliers[i] for i in ids if i in self.suppliers), key=self.rank_key)

    def ranked_for(self, sku_id: str) -> list[dict[str, Any]]:
        """Suppliers that have supplied ``sku_id`` (any PO status), best first."""
        return self.ranked(self.po_count.get(sku_id, {}))

    def best_for(self, sku_id: str) -> tuple[dict[str, Any] | None, bool]:
        """(best supplier, from_history). Falls back to the best supplier overall."""
        history = self.ranked_for(sku_id)
        if history:
            return history[0], True
        overall = self.ranked()
        return (overall[0] if overall else None), False

    def price_for(self, sku_id: str, supplier_id: str) -> float | None:
        found = self._last.get((sku_id, supplier_id))
        return found[2] if found else None


# ---------------------------------------------------------------- purchase orders


def next_po_id(existing_ids: Iterable[Any]) -> str:
    numbers = [int(m.group(1)) for i in existing_ids
               if isinstance(i, str) and (m := _PO_ID.match(i))]
    return f"PO-{max(numbers) + 1 if numbers else FIRST_PO_NUMBER}"


def days_late(eta: str | None, today: date) -> int | None:
    """Whole days past the ETA, or None when there is no ETA or it has not passed."""
    if not eta:
        return None
    late = (today - date.fromisoformat(eta)).days
    return late if late > 0 else None
