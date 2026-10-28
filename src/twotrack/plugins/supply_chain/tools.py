"""Tools of the built-in supply-chain example: six reads and one write.

Handlers are plain code over the Store protocol; the numbers they report go in ``aggregates``
(the only figures a narrator may cite). Messages carry no free numbers, only identifiers and
ISO dates, so a model that echoes them cannot trip the numeric lock. Messages and scopes are
phrased in the request language (``ctx.language``); every aggregate has a label in both.

Params that apply a constraint declare it (``json_schema_extra={"binds": ...}``, see
core.params): ``category`` (any entity that extends ``sku``, drop-in categories included) on
every stock and purchase-order read, ``warehouse``, ``status`` and ``criticality`` where a
tool filters by them. Routing fills them from the question, and asks instead of answering when
a question names a constraint the routed tool has no param for.
"""

from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from twotrack.core import (
    Approval,
    Precondition,
    Provenance,
    ResourceEffect,
    Tool,
    ToolContext,
    ToolOutputs,
    ToolResult,
    describe_filters,
    resolve_entity,
)
from twotrack.core.errors import InputProblem

from . import logic
from .entities import LATE_STATUSES, PO_STATUSES, SUPPLIER
from .extract import extractor

READ_ROLES = ("admin", "operator", "viewer")
WRITE_ROLES = ("admin", "operator")
SUPPLIER_VIEW = resolve_entity(SUPPLIER, None)


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
CategoryId = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1,
                                              max_length=64)]
Warehouse = Annotated[Literal["WH-EAST", "WH-WEST", "WH-CENTRAL"], BeforeValidator(_upper)]
Criticality = Annotated[Literal["A", "B", "C"], BeforeValidator(_upper)]
PoStatus = Annotated[Literal["draft", "open", "shipped", "received", "cancelled"],
                     BeforeValidator(_lower)]


def bound(key: str, description: str | None = None) -> Any:
    """An optional param that applies the constraint ``key`` (core.params ``binds``)."""
    return Field(default=None, description=description, json_schema_extra={"binds": key})


def category_param() -> Any:
    return bound("category:sku", "Item category: the id of an entity that extends sku, e.g. "
                                 "spare_part or packaging (its sub-categories included).")


class Params(BaseModel):
    """Unknown keys are rejected: a misnamed filter must not silently widen a listing."""

    model_config = ConfigDict(extra="forbid")


class InventoryLookupParams(Params):
    sku_id: Code | None = None
    warehouse: Warehouse | None = bound("field:inventory.warehouse")
    category: CategoryId | None = category_param()


class LowStockParams(Params):
    criticality: Criticality | None = bound("field:sku.criticality")
    category: CategoryId | None = category_param()


class ReorderSuggestionParams(Params):
    sku_id: Code | None = None
    category: CategoryId | None = category_param()


class PoStatusParams(Params):
    po_id: Code | None = None
    sku_id: Code | None = None
    supplier_id: Code | None = None
    status: PoStatus | None = bound("field:purchase_order.status")
    category: CategoryId | None = category_param()


class LateShipmentsParams(Params):
    sku_id: Code | None = None
    supplier_id: Code | None = None
    category: CategoryId | None = category_param()


class SupplierCompareParams(Params):
    sku_id: Code | None = None


class CreatePoParams(Params):
    sku_id: Code
    qty: int = Field(gt=0, le=1_000_000)
    supplier_id: Code | None = Field(default=None, json_schema_extra={
        "label": {"en": "Supplier", "zh": "供应商"},
        "unset": {"en": "best-ranked for this SKU", "zh": "该物料排名最高的供应商"}})


def _result(ctx: ToolContext, tool: str, rows: list[dict[str, Any]], columns: list[str],
            aggregates: dict[str, int | float], *, scope: str = "",
            message: str = "") -> ToolResult:
    return ToolResult(rows=rows, columns=columns, aggregates=aggregates, message=message,
                      provenance=Provenance(tool=tool, row_count=len(rows), as_of=ctx.now(),
                                            scope=scope))


def _filters(params: BaseModel) -> dict[str, Any]:
    return {k: v for k, v in params.model_dump().items() if v is not None}


def _scope(ctx: ToolContext, tool: str, filters: dict[str, Any], everything: str) -> str:
    """The filters a listing ran with, in plain words of the request language ("Category:
    Packaging"), or what it covers without any."""
    return describe_filters(ctx.registry, tool, filters, ctx.language) or everything


def say(ctx: ToolContext, en: str, zh: str) -> str:
    """The same sentence in the request language."""
    return zh if ctx.language == "zh" else en


def labels(**names: tuple[str, str]) -> dict[str, dict[str, str]]:
    """``aggregate_labels`` from ``key=(english, chinese)`` pairs."""
    return {"en": {k: en for k, (en, _) in names.items()},
            "zh": {k: zh for k, (_, zh) in names.items()}}


# ---------------------------------------------------------------- reads


# "sku_name", not "name": a key declared by another entity (sku.name) is not an inventory
# field, and result projection keyed on this tool's entity may drop it.
INVENTORY_COLUMNS = ["sku_id", "sku_name", "warehouse", "on_hand", "reserved", "available",
                     "as_of"]


def inventory_lookup(ctx: ToolContext, params: InventoryLookupParams) -> ToolResult:
    filters = _filters(params)
    query = logic.scoped_filters(ctx.store, filters, params.category)
    records = logic.fetch_all(ctx.store, "inventory", query or None,
                              sort=["sku_id", "warehouse"])
    skus = query.get("sku_id")
    names = {s["sku_id"]: s.get("name") for s in logic.fetch_all(
        ctx.store, "sku", {"sku_id": skus} if skus is not None else None)}
    rows = []
    for r in records:
        on_hand, reserved = r.get("on_hand") or 0, r.get("reserved") or 0
        rows.append({"sku_id": r["sku_id"], "sku_name": names.get(r["sku_id"]),
                     "warehouse": r["warehouse"], "on_hand": on_hand, "reserved": reserved,
                     "available": on_hand - reserved, "as_of": r.get("as_of")})
    aggregates = {
        "total_on_hand": sum(r["on_hand"] for r in rows),
        "total_reserved": sum(r["reserved"] for r in rows),
        "total_available": sum(r["available"] for r in rows),
        "sku_count": len({r["sku_id"] for r in rows}),
    }
    message = "" if rows else say(ctx, "No inventory records match this request.",
                                  "没有符合条件的库存记录。")
    return _result(ctx, "inventory_lookup", rows, INVENTORY_COLUMNS, aggregates,
                   scope=_scope(ctx, "inventory_lookup", filters,
                                say(ctx, "all SKUs, all warehouses", "全部 SKU、全部仓库")),
                   message=message)


LOW_STOCK_COLUMNS = ["sku_id", "name", "category", "criticality", "available", "open_po_qty",
                     "reorder_point", "shortfall"]


def low_stock(ctx: ToolContext, params: LowStockParams) -> ToolResult:
    positions = logic.stock_positions(ctx.store, logic.sku_filter(ctx.store, params.category))
    low = logic.low_stock_positions(positions, params.criticality)
    rows = [{"sku_id": p.sku_id, "name": p.name, "category": p.category,
             "criticality": p.criticality, "available": p.available,
             "open_po_qty": p.open_po_qty, "reorder_point": p.reorder_point,
             "shortfall": p.shortfall} for p in low]
    aggregates = {
        "low_stock_count": len(rows),
        "class_a_count": sum(1 for p in low if p.criticality == "A"),
        "total_shortfall": sum(p.shortfall for p in low),
    }
    message = "" if rows else say(ctx, "No SKU is below its reorder point.",
                                  "没有低于再订货点的 SKU。")
    return _result(ctx, "low_stock", rows, LOW_STOCK_COLUMNS, aggregates,
                   scope=_scope(ctx, "low_stock", _filters(params),
                                say(ctx, "all SKUs, summed across warehouses",
                                    "全部 SKU，各仓合计")),
                   message=message)


REORDER_COLUMNS = ["sku_id", "name", "criticality", "avg_daily_demand", "lead_time_days",
                   "reorder_point", "available", "open_po_qty", "suggested_qty",
                   "estimated_cost"]


def suggestion_rows(positions: list[logic.StockPosition]) -> list[dict[str, Any]]:
    rows = []
    for p in positions:
        qty = logic.suggested_qty(p)
        cost = logic.money(qty * p.unit_cost) if p.unit_cost is not None else None
        rows.append({"sku_id": p.sku_id, "name": p.name, "criticality": p.criticality,
                     "avg_daily_demand": p.avg_daily_demand, "lead_time_days": p.lead_time_days,
                     "reorder_point": p.reorder_point, "available": p.available,
                     "open_po_qty": p.open_po_qty, "suggested_qty": qty, "estimated_cost": cost})
    return rows


def reorder_suggestion(ctx: ToolContext, params: ReorderSuggestionParams) -> ToolResult:
    skus = logic.sku_filter(ctx.store, params.category, params.sku_id)
    filters = describe_filters(ctx.registry, "reorder_suggestion", _filters(params),
                               ctx.language)
    if params.sku_id:
        positions = logic.stock_positions(ctx.store, skus)
        scope = filters
        empty = (say(ctx, f"No SKU {params.sku_id} in category {params.category}.",
                     f"类别 {params.category} 中没有 SKU {params.sku_id}。")
                 if params.category else
                 say(ctx, f"No SKU {params.sku_id} on file.", f"没有 SKU {params.sku_id} 的记录。"))
    else:
        positions = logic.low_stock_positions(logic.stock_positions(ctx.store, skus))
        below = say(ctx, "SKUs below their reorder point", "低于再订货点的 SKU")
        scope = f"{below}{say(ctx, ', ', '，')}{filters}" if filters else below
        empty = say(ctx, "No SKU is below its reorder point, so nothing needs ordering.",
                    "没有低于再订货点的 SKU，无需补货。")
    rows = suggestion_rows(positions)
    aggregates = {
        "sku_count": len(rows),
        "skus_to_order": sum(1 for r in rows if r["suggested_qty"] > 0),
        "total_suggested_qty": sum(r["suggested_qty"] for r in rows),
        "estimated_cost": logic.money(sum(r["estimated_cost"] or 0.0 for r in rows)),
    }
    return _result(ctx, "reorder_suggestion", rows, REORDER_COLUMNS, aggregates, scope=scope,
                   message="" if rows else empty)


PO_COLUMNS = ["po_id", "sku_id", "supplier_id", "qty", "unit_price", "status", "ordered_at",
              "eta"]


def po_status(ctx: ToolContext, params: PoStatusParams) -> ToolResult:
    filters = _filters(params)
    query = logic.scoped_filters(ctx.store, filters, params.category)
    records = logic.fetch_all(ctx.store, "purchase_order", query or None,
                              sort=["-ordered_at", "po_id"])
    rows = [{c: r.get(c) for c in PO_COLUMNS} for r in records]
    aggregates: dict[str, int | float] = {"po_count": len(rows),
                                          "total_qty": sum(r["qty"] or 0 for r in rows)}
    for status in PO_STATUSES:
        aggregates[f"{status}_count"] = sum(1 for r in rows if r["status"] == status)
    message = "" if rows else say(ctx, "No purchase orders match this request.",
                                  "没有符合条件的采购单。")
    return _result(ctx, "po_status", rows, PO_COLUMNS, aggregates,
                   scope=_scope(ctx, "po_status", filters,
                                say(ctx, "all purchase orders", "全部采购单")),
                   message=message)


LATE_COLUMNS = ["po_id", "sku_id", "supplier_id", "supplier_name", "qty", "status", "eta",
                "days_late"]


def late_shipments(ctx: ToolContext, params: LateShipmentsParams) -> ToolResult:
    today = ctx.now().date()
    filters = _filters(params)
    records = logic.fetch_all(ctx.store, "purchase_order",
                              {**logic.scoped_filters(ctx.store, filters, params.category),
                               "status": list(LATE_STATUSES)})
    names = {s["supplier_id"]: s.get("name") for s in logic.fetch_all(ctx.store, "supplier")}
    rows = []
    for r in records:
        late = logic.days_late(r.get("eta"), today)
        if late is not None:
            rows.append({"po_id": r["po_id"], "sku_id": r["sku_id"],
                         "supplier_id": r["supplier_id"],
                         "supplier_name": names.get(r["supplier_id"]), "qty": r.get("qty"),
                         "status": r["status"], "eta": r["eta"], "days_late": late})
    rows.sort(key=lambda r: (-r["days_late"], r["po_id"]))
    aggregates = {
        "late_count": len(rows),
        "oldest_days_late": max((r["days_late"] for r in rows), default=0),
        "late_qty": sum(r["qty"] or 0 for r in rows),
        "supplier_count": len({r["supplier_id"] for r in rows}),
    }
    message = "" if rows else say(
        ctx, f"No open or shipped order is past its ETA as of {today}.",
        f"截至 {today}，没有超过预计到货日期的已下达或已发货采购单。")
    return _result(ctx, "late_shipments", rows, LATE_COLUMNS, aggregates,
                   scope=_scope(ctx, "late_shipments", filters,
                                say(ctx, f"open and shipped orders, ETA before {today}",
                                    f"预计到货早于 {today} 的已下达和已发货采购单")),
                   message=message)


SUPPLIER_COMPARE_COLUMNS = ["rank", *(f.id for f in SUPPLIER_VIEW.all_fields), "po_count",
                            "last_unit_price"]


def supplier_compare(ctx: ToolContext, params: SupplierCompareParams) -> ToolResult:
    """Restricted supplier fields are projected per caller with the entity's own FieldSpec
    visibility (ToolContext carries no projection), so non-admins never get them in rows."""
    book = logic.SupplierBook.load(ctx.store, params.sku_id)
    ranked = book.ranked_for(params.sku_id) if params.sku_id else book.ranked()
    message = ""
    if params.sku_id and not ranked:
        ranked = book.ranked()
        message = say(ctx, f"No purchase history for {params.sku_id}; ranking all suppliers "
                           "instead.", f"{params.sku_id} 没有采购记录，改为对全部供应商排名。")
    scope = (say(ctx, f"suppliers of {params.sku_id} by purchase history",
                 f"按采购记录排序的 {params.sku_id} 供应商")
             if params.sku_id and not message else say(ctx, "all suppliers", "全部供应商"))
    visible = [f.id for f in SUPPLIER_VIEW.visible_fields(ctx.principal)]
    rows = []
    for rank, s in enumerate(ranked, 1):
        row: dict[str, Any] = {"rank": rank, **{fid: s.get(fid) for fid in visible}}
        if params.sku_id:
            row["po_count"] = book.po_count[params.sku_id][s["supplier_id"]]
            row["last_unit_price"] = book.price_for(params.sku_id, s["supplier_id"])
        rows.append(row)
    columns = ["rank", *visible, *(["po_count", "last_unit_price"] if params.sku_id else [])]
    top = rows[0] if rows else {}
    aggregates: dict[str, int | float] = {"supplier_count": len(rows)}
    if top.get("rating") is not None:
        aggregates["top_rating"] = top["rating"]
    if top.get("avg_lead_time_days") is not None:
        aggregates["top_lead_time_days"] = top["avg_lead_time_days"]
    return _result(ctx, "supplier_compare", rows, columns, aggregates, scope=scope,
                   message=message or ("" if rows else say(ctx, "No suppliers on file.",
                                                            "暂无供应商记录。")))


# ---------------------------------------------------------------- write


CREATE_PO_COLUMNS = [*PO_COLUMNS, "supplier_name"]


def create_po(ctx: ToolContext, params: CreatePoParams) -> ToolResult:
    sku = ctx.store.get("sku", params.sku_id)
    if sku is None:
        raise InputProblem(say(ctx,
                               f"There is no SKU '{params.sku_id}'. Check the SKU id and retry.",
                               f"没有 SKU“{params.sku_id}”。请检查 SKU 编码后重试。"))
    book = logic.SupplierBook.load(ctx.store, params.sku_id)
    if params.supplier_id is not None:
        supplier = book.suppliers.get(params.supplier_id)
        if supplier is None:
            raise InputProblem(say(ctx, f"There is no supplier '{params.supplier_id}'.",
                                   f"没有供应商“{params.supplier_id}”。"))
    else:
        supplier, _ = book.best_for(params.sku_id)
        if supplier is None:
            raise InputProblem(say(ctx, "No supplier is on file to order from.",
                                   "没有可以下单的供应商记录。"))
    po_id = logic.next_po_id(ctx.store.column_values("purchase_order", "po_id"))
    price = book.price_for(params.sku_id, supplier["supplier_id"])
    if price is None:
        price = sku.get("unit_cost")
    lead = supplier.get("avg_lead_time_days") or sku.get("lead_time_days") or 0
    today = ctx.now().date()
    record = {"po_id": po_id, "sku_id": params.sku_id, "supplier_id": supplier["supplier_id"],
              "qty": params.qty, "unit_price": price, "status": "draft",
              "ordered_at": today.isoformat(), "eta": (today + timedelta(days=lead)).isoformat()}
    ctx.store.upsert_many("purchase_order", [record])
    aggregates: dict[str, int | float] = {"qty": params.qty, "lead_time_days": lead}
    if price is not None:
        aggregates["order_value"] = logic.money(params.qty * price)
    message = say(ctx,
                  f"Draft {po_id} created for {params.sku_id} with {supplier.get('name')}, "
                  f"ETA {record['eta']}. It stays a draft until someone approves it.",
                  f"已为 {params.sku_id} 创建草稿采购单 {po_id}（供应商：{supplier.get('name')}），"
                  f"预计到货 {record['eta']}。在审批之前它保持草稿状态。")
    return _result(ctx, "create_po", [{**record, "supplier_name": supplier.get("name")}],
                   CREATE_PO_COLUMNS, aggregates, scope=say(ctx, f"new draft {po_id}",
                                                            f"新建草稿 {po_id}"),
                   message=message)


# ---------------------------------------------------------------- declarations

HAS_INVENTORY = Precondition(
    name="has_inventory_data",
    description="No inventory data yet. Import inventory records (or load the demo data) "
                "first.",
    check=lambda ctx: ctx.store.count("inventory") > 0,
)
HAS_SUPPLIERS = Precondition(
    name="has_suppliers",
    description="No suppliers on file yet. Import a supplier list first.",
    check=lambda ctx: ctx.store.count("supplier") > 0,
)

# The definitions behind every stock figure, in each language. The Chinese texts use the labels
# the entities declare (物料, 在库数量, 预留数量, 再订货点, 采购单, 草稿 / 已下达 / 已发货).
STOCK_TERMS = ("available = on_hand - reserved summed across warehouses; open PO qty = qty on "
               "draft, open and shipped purchase orders")
STOCK_TERMS_ZH = ("可用数量 = 在库数量 - 预留数量，按各仓库合计；"
                  "未到货采购量 = 草稿、已下达和已发货采购单上的数量")

TOOLS = (
    Tool(
        name="inventory_lookup",
        title={"en": "Inventory lookup", "zh": "库存查询"},
        description={
            "en": "On-hand, reserved and available stock per SKU and warehouse, with totals.",
            "zh": "每个物料在各仓库的在库数量、预留数量和可用数量，并附合计。"},
        route_hint="Use for current stock of a SKU or a warehouse. Not for shortages "
                   "(low_stock) or how much to order (reorder_suggestion).",
        keywords=("inventory", "on hand", "stock level", "stock levels", "in stock",
                  "how much stock", "库存查询", "现有库存", "库存多少", "还有多少"),
        examples=("what is the inventory of SKU-10042", "how many units are on hand in WH-EAST",
                  "show stock levels by warehouse", "SKU-10042 库存多少", "查询东仓库存"),
        roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE,
        explorable=True, requires=("has_inventory_data",),
        outputs=ToolOutputs(
            aggregates=("total_on_hand", "total_reserved", "total_available", "sku_count"),
            columns=INVENTORY_COLUMNS),
        params_model=InventoryLookupParams, handler=inventory_lookup, entity="inventory",
        param_extractor=extractor("sku_id", "warehouse"),
        aggregate_labels=labels(
            total_on_hand=("Units on hand", "在库数量"),
            total_reserved=("Units reserved", "已预留数量"),
            total_available=("Units available", "可用数量"),
            sku_count=("SKUs", "SKU 数")),
    ),
    Tool(
        name="low_stock",
        title={"en": "Low stock", "zh": "低库存"},
        description={
            "en": "SKUs whose available stock plus open PO qty is below the reorder point "
                  f"({STOCK_TERMS}).",
            "zh": f"可用数量加未到货采购量低于再订货点的物料（{STOCK_TERMS_ZH}）。"},
        route_hint="Use to list shortages. For order quantities use reorder_suggestion; for a "
                   "full plan with suppliers use reorder_plan.",
        keywords=("low stock", "low on stock", "low in stock", "running low", "below reorder point",
                  "stockout", "stock out", "shortage", "shortages", "低库存", "缺货",
                  "库存不足", "低于再订货点"),
        examples=("which SKUs are low on stock", "what is running low",
                  "show items below reorder point", "哪些物料库存不足"),
        roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE,
        explorable=True, requires=("has_inventory_data",),
        outputs=ToolOutputs(
            aggregates=("low_stock_count", "class_a_count", "total_shortfall"),
            columns=LOW_STOCK_COLUMNS),
        params_model=LowStockParams, handler=low_stock, entity="sku",
        param_extractor=extractor("criticality"),
        aggregate_labels=labels(
            low_stock_count=("SKUs below reorder point", "低于再订货点的 SKU"),
            class_a_count=("Class A SKUs among them", "其中 A 类 SKU"),
            total_shortfall=("Total shortfall (units)", "缺口合计（件）")),
    ),
    Tool(
        name="reorder_suggestion",
        title={"en": "Reorder suggestion", "zh": "补货建议"},
        description={
            "en": "Suggested order quantity per SKU: suggested_qty = max(0, ceil("
                  "avg_daily_demand x lead_time_days + reorder_point - available - "
                  f"open_po_qty)) ({STOCK_TERMS}). One SKU, or every low-stock SKU.",
            "zh": "每个物料的建议采购量：建议采购量 = max(0, 向上取整("
                  "日均需求 × 交货周期 + 再订货点 - 可用数量 - 未到货采购量))"
                  f"（{STOCK_TERMS_ZH}）。针对单个物料，或全部低库存物料。"},
        route_hint="Use for how much to order. Does not pick suppliers or create orders.",
        keywords=("how much to order", "how much should we order", "how many to order",
                  "order quantity", "reorder quantity", "suggested order", "reorder",
                  "replenish", "补货建议", "订多少", "建议采购量", "补多少"),
        examples=("how much should we reorder for SKU-10042", "suggested order quantities",
                  "SKU-10042 应该补多少"),
        roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE,
        explorable=True, requires=("has_inventory_data",),
        outputs=ToolOutputs(
            aggregates=("sku_count", "skus_to_order", "total_suggested_qty", "estimated_cost"),
            columns=REORDER_COLUMNS),
        params_model=ReorderSuggestionParams, handler=reorder_suggestion, entity="sku",
        param_extractor=extractor("sku_id"),
        aggregate_labels=labels(
            sku_count=("SKUs reviewed", "评估的 SKU"),
            skus_to_order=("SKUs to order", "需要补货的 SKU"),
            total_suggested_qty=("Suggested order quantity (units)", "建议采购量（件）"),
            estimated_cost=("Estimated cost", "预计成本")),
    ),
    Tool(
        name="po_status",
        title={"en": "Purchase order status", "zh": "采购单状态"},
        description={
            "en": "Purchase orders filtered by PO, SKU, supplier or status, with counts per "
                  "status.",
            "zh": "按采购单号、物料、供应商或状态筛选的采购单，并按状态计数。"},
        route_hint="Use to look up purchase orders. For overdue ones use late_shipments.",
        keywords=("purchase order", "purchase orders", "po status", "order status",
                  "open orders", "open pos", "采购单", "采购订单", "订单状态"),
        examples=("what is the status of PO-20015", "show open purchase orders",
                  "purchase orders for SKU-10042", "PO-20015 状态"),
        roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE,
        explorable=True,
        outputs=ToolOutputs(
            aggregates=("po_count", "total_qty", *(f"{status}_count" for status in PO_STATUSES)),
            columns=PO_COLUMNS),
        params_model=PoStatusParams, handler=po_status, entity="purchase_order",
        param_extractor=extractor("po_id", "sku_id", "supplier_id", "status"),
        aggregate_labels=labels(
            po_count=("Purchase orders", "采购单"),
            total_qty=("Total quantity (units)", "总数量（件）"),
            draft_count=("Drafts", "草稿"),
            open_count=("Open", "已下达"),
            shipped_count=("Shipped", "已发货"),
            received_count=("Received", "已收货"),
            cancelled_count=("Cancelled", "已取消")),
    ),
    Tool(
        name="late_shipments",
        title={"en": "Late shipments", "zh": "逾期采购单"},
        description={
            "en": "Open or shipped purchase orders past their ETA, oldest first, with days "
                  "late.",
            "zh": "已超过预计到货日期的已下达或已发货采购单，逾期最久的在前，并给出逾期天数。"},
        route_hint="Use for overdue or delayed deliveries.",
        keywords=("late", "overdue", "past due", "delayed", "late shipments", "late deliveries",
                  "are late", "are overdue", "behind schedule", "逾期", "延迟", "延期", "迟到",
                  "没到货"),
        examples=("which shipments are late", "show overdue purchase orders",
                  "哪些采购单逾期了"),
        roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE,
        explorable=True,
        outputs=ToolOutputs(
            aggregates=("late_count", "oldest_days_late", "late_qty", "supplier_count"),
            columns=LATE_COLUMNS),
        params_model=LateShipmentsParams, handler=late_shipments, entity="purchase_order",
        param_extractor=extractor("sku_id", "supplier_id"),
        aggregate_labels=labels(
            late_count=("Late orders", "逾期采购单"),
            oldest_days_late=("Longest delay (days)", "最长逾期（天）"),
            late_qty=("Late quantity (units)", "逾期数量（件）"),
            supplier_count=("Suppliers affected", "涉及供应商")),
    ),
    Tool(
        name="supplier_compare",
        title={"en": "Supplier comparison", "zh": "供应商对比"},
        description={
            "en": "Suppliers of a SKU (from its purchase history) ranked by rating, then "
                  "average lead time. Without a SKU, ranks all suppliers.",
            "zh": "按评分、再按平均交期，对某个物料的供应商排名（依据该物料的采购记录）。"
                  "不指定物料时，对全部供应商排名。"},
        route_hint="Use to choose or compare suppliers or vendors.",
        keywords=("supplier", "suppliers", "vendor", "vendors", "compare suppliers",
                  "best supplier", "供应商", "比较供应商", "供应商对比"),
        examples=("compare suppliers for SKU-10042", "who is the best supplier",
                  "SKU-10042 哪个供应商最好"),
        roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE,
        explorable=True, requires=("has_suppliers",),
        outputs=ToolOutputs(
            aggregates=("supplier_count", "top_rating", "top_lead_time_days"),
            columns=SUPPLIER_COMPARE_COLUMNS),
        params_model=SupplierCompareParams, handler=supplier_compare, entity="supplier",
        param_extractor=extractor("sku_id"),
        aggregate_labels=labels(
            supplier_count=("Suppliers ranked", "参与排名的供应商"),
            top_rating=("Top supplier's rating", "排名最高供应商的评分"),
            top_lead_time_days=("Top supplier's lead time (days)", "排名最高供应商的交期（天）")),
    ),
    Tool(
        name="create_po",
        title={"en": "Create purchase order", "zh": "新建采购单"},
        description={
            "en": "Create a draft purchase order for a SKU. Without a supplier it uses the "
                  "best-ranked supplier of that SKU.",
            "zh": "为某个物料新建一张草稿采购单。未指定供应商时，选用该物料排名最高的供应商。"},
        route_hint="Creates data. Needs sku_id and qty; supplier_id is optional.",
        # phrases that name the write, never a bare verb: "create" alone is just as likely to
        # ask for a plan or a report ("create a reorder plan"), so it is no evidence of this tool
        keywords=("create a purchase order", "create purchase order", "new purchase order",
                  "create a po", "create po", "raise a po", "place an order", "下单",
                  "新建采购单", "创建采购单"),
        examples=("create a purchase order for 200 units of SKU-10042",
                  "raise a PO for SKU-10042, 50 pcs from SUP-003", "给SKU-10042下单200件"),
        roles=WRITE_ROLES, effect=ResourceEffect.WRITE, approval=Approval.REQUIRED,
        explorable=True,  # a proposal only: every run stops at a one-time approval first
        requires=("has_suppliers",),
        outputs=ToolOutputs(aggregates=("qty", "lead_time_days", "order_value"),
                            columns=CREATE_PO_COLUMNS),
        params_model=CreatePoParams, handler=create_po, entity="purchase_order",
        param_extractor=extractor("sku_id", "qty", "supplier_id"),
        implied_scope={"field:purchase_order.status": "draft"},  # it only ever creates drafts
        aggregate_labels=labels(
            qty=("Quantity ordered (units)", "下单数量（件）"),
            lead_time_days=("Lead time (days)", "交期（天）"),
            order_value=("Order value", "订单金额")),
    ),
)

PRECONDITIONS = (HAS_INVENTORY, HAS_SUPPLIERS)
