"""Ground truth: every expected number is computed here from the store.

All business math comes from ``twotrack.plugins.supply_chain.logic`` (positions, low stock,
suggested quantity, money rounding, supplier ranking, days late). This module only filters
rows and adds up what those formulas return; it never restates a formula, and no expected
number anywhere in the benchmark is typed by hand.

Note the deliberate coupling: the tools use the same formulas, so a bug in a formula would not
show up as a twotrack error here. The benchmark measures whether a system reports what the
formulas give for the scope the user asked about, not whether the formulas are right.
"""

from collections.abc import Iterable
from datetime import date
from typing import Any

from twotrack.plugins.supply_chain import logic
from twotrack.plugins.supply_chain.entities import LATE_STATUSES

STOCK_FIELDS = ("available", "on_hand", "reserved")


class Truth:
    def __init__(self, store: Any, today: date) -> None:
        self.today = today
        self.skus = logic.fetch_all(store, "sku", sort="sku_id")
        self.inventory = logic.fetch_all(store, "inventory", sort="id")
        self.orders = logic.fetch_all(store, "purchase_order", sort="po_id")
        self.suppliers = logic.fetch_all(store, "supplier", sort="supplier_id")
        self.positions = {p.sku_id: p for p in
                          logic.build_positions(self.skus, self.inventory, self.orders)}
        self.book = logic.SupplierBook(self.suppliers, self.orders)
        self.supplier_names = {s["supplier_id"]: s.get("name") for s in self.suppliers}

    # ------------------------------------------------------------ stock

    def stock(self, field: str = "available", *, sku: str | None = None,
              warehouse: str | None = None) -> int:
        """Sum of a stock field over the matching inventory rows (positions without POs)."""
        if field not in STOCK_FIELDS:
            raise ValueError(f"unknown stock field {field!r}")
        skus = [s for s in self.skus if sku is None or s["sku_id"] == sku]
        rows = [r for r in self.inventory
                if (sku is None or r["sku_id"] == sku)
                and (warehouse is None or r["warehouse"] == warehouse)]
        return sum(getattr(p, field) for p in logic.build_positions(skus, rows, []))

    def stocked_warehouses(self, sku: str) -> list[str]:
        """Warehouses where ``sku`` has positive available stock."""
        rows = {r["warehouse"] for r in self.inventory if r["sku_id"] == sku}
        return sorted(w for w in rows if self.stock(sku=sku, warehouse=w) > 0)

    def sku_count(self, *, warehouse: str | None = None) -> int:
        return len({r["sku_id"] for r in self.inventory
                    if warehouse is None or r["warehouse"] == warehouse})

    # ------------------------------------------------------------ purchase orders

    def orders_where(self, *, po: str | None = None, sku: str | None = None,
                     supplier: str | None = None, status: str | None = None) -> list[dict]:
        return [o for o in self.orders
                if (po is None or o["po_id"] == po) and (sku is None or o["sku_id"] == sku)
                and (supplier is None or o["supplier_id"] == supplier)
                and (status is None or o["status"] == status)]

    @staticmethod
    def qty(orders: Iterable[dict]) -> int:
        return sum(o.get("qty") or 0 for o in orders)

    def late(self, *, sku: str | None = None, supplier: str | None = None,
             category: str | None = None) -> list[dict]:
        """Open/shipped orders past their ETA (``logic.days_late``), with ``days_late``."""
        out = []
        for o in self.orders:
            if o["status"] not in LATE_STATUSES:
                continue
            late = logic.days_late(o.get("eta"), self.today)
            if late is None:
                continue
            if sku is not None and o["sku_id"] != sku:
                continue
            if supplier is not None and o["supplier_id"] != supplier:
                continue
            if category is not None and self.category(o["sku_id"]) != category:
                continue
            out.append({**o, "days_late": late})
        return sorted(out, key=lambda o: (-o["days_late"], o["po_id"]))

    # ------------------------------------------------------------ low stock and suggestions

    def category(self, sku: str) -> str | None:
        position = self.positions.get(sku)
        return position.category if position else None

    def low(self, *, criticality: str | None = None,
            category: str | None = None) -> list[logic.StockPosition]:
        low = logic.low_stock_positions(self.positions.values(), criticality)
        return [p for p in low if category is None or p.category == category]

    def suggested(self, sku: str) -> int:
        return logic.suggested_qty(self.positions[sku])

    def standard_cost(self, skus: Iterable[str]) -> float:
        """Suggested qty x unit cost per SKU (``logic.money`` per line, then on the total)."""
        lines = []
        for sku in skus:
            p = self.positions[sku]
            if p.unit_cost is not None:
                lines.append(logic.money(logic.suggested_qty(p) * p.unit_cost))
        return logic.money(sum(lines))

    def plan_lines(self) -> list[dict]:
        """The reorder plan: every low SKU with a positive suggestion, the best-ranked
        supplier of its history (or overall), priced at that supplier's last price for the
        SKU, else the unit cost."""
        lines = []
        for p in self.low():
            qty = logic.suggested_qty(p)
            if qty <= 0:
                continue
            supplier, _ = self.book.best_for(p.sku_id)
            supplier_id = supplier["supplier_id"] if supplier else None
            price = (self.book.price_for(p.sku_id, supplier_id) if supplier_id else None) \
                or p.unit_cost
            cost = logic.money(qty * price) if price is not None else None
            lines.append({"sku_id": p.sku_id, "qty": qty, "supplier_id": supplier_id,
                          "cost": cost})
        return lines

    def plan_cost(self) -> float:
        return logic.money(sum(line["cost"] or 0.0 for line in self.plan_lines()))

    # ------------------------------------------------------------ suppliers

    def ranked_for(self, sku: str) -> list[dict]:
        return self.book.ranked_for(sku)

    def ranked(self) -> list[dict]:
        return self.book.ranked()
