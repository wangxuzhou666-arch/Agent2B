"""reorder_plan: low stock -> order quantities -> group by criticality and source -> narrate.

Three deterministic steps produce every number; the single llm step is last and only narrates
the finished ToolResult (it sees ``narrator_view()``, and its text passes the numeric lock).
Each hand-off between steps is a Pydantic model, never a bare dict.
"""

from typing import Any

from pydantic import BaseModel

from twotrack.core import (
    Approval,
    Provenance,
    ResourceEffect,
    Step,
    StepKind,
    ToolContext,
    ToolOutputs,
    ToolResult,
    Workflow,
    describe_filters,
)

from . import logic
from .entities import CRITICALITY
from .extract import extractor
from .tools import READ_ROLES, CategoryId, Criticality, Params, bound, category_param, labels, say


class ReorderPlanInput(Params):
    criticality: Criticality | None = bound("field:sku.criticality")
    category: CategoryId | None = category_param()


class LowStockSet(BaseModel):
    positions: list[logic.StockPosition]
    criticality: str | None = None
    category: str | None = None


class SuggestionLine(BaseModel):
    position: logic.StockPosition
    suggested_qty: int


class SuggestionSet(BaseModel):
    lines: list[SuggestionLine]
    criticality: str | None = None
    category: str | None = None


PLAN_COLUMNS = ["criticality", "sku_id", "name", "category", "suggested_qty", "supplier_id",
                "supplier_name", "unit_price", "estimated_cost", "supplier_source"]
QUESTION = ("Summarize this reorder plan for a buyer: how many SKUs to order, the split by "
            "criticality, how many suppliers are involved and the estimated spend.")


def find_low_stock(ctx: ToolContext, data: ReorderPlanInput) -> LowStockSet:
    stock = logic.stock_positions(ctx.store, logic.sku_filter(ctx.store, data.category))
    positions = logic.low_stock_positions(stock, data.criticality)
    return LowStockSet(positions=positions, criticality=data.criticality, category=data.category)


def compute_suggestions(ctx: ToolContext, data: LowStockSet) -> SuggestionSet:
    lines = [SuggestionLine(position=p, suggested_qty=logic.suggested_qty(p))
             for p in data.positions]
    return SuggestionSet(lines=[line for line in lines if line.suggested_qty > 0],
                         criticality=data.criticality, category=data.category)


def group_and_source(ctx: ToolContext, data: SuggestionSet) -> ToolResult:
    book = logic.SupplierBook.load(ctx.store)
    rows: list[dict[str, Any]] = []
    for line in data.lines:
        p = line.position
        supplier, from_history = book.best_for(p.sku_id)
        sid = supplier["supplier_id"] if supplier else None
        price = (book.price_for(p.sku_id, sid) if sid else None) or p.unit_cost
        cost = logic.money(line.suggested_qty * price) if price is not None else None
        rows.append({"criticality": p.criticality, "sku_id": p.sku_id, "name": p.name,
                     "category": p.category, "suggested_qty": line.suggested_qty,
                     "supplier_id": sid, "supplier_name": supplier.get("name") if supplier
                     else None, "unit_price": price, "estimated_cost": cost,
                     "supplier_source": "history" if from_history else "best overall"})
    rows.sort(key=lambda r: (logic.criticality_rank(r["criticality"]),
                             -(r["estimated_cost"] or 0.0), r["sku_id"]))
    aggregates: dict[str, int | float] = {
        "sku_count": len(rows),
        "total_suggested_qty": sum(r["suggested_qty"] for r in rows),
    }
    for level in CRITICALITY:
        aggregates[f"class_{level.lower()}_skus"] = sum(1 for r in rows
                                                        if r["criticality"] == level)
    aggregates["supplier_count"] = len({r["supplier_id"] for r in rows if r["supplier_id"]})
    aggregates["estimated_cost"] = logic.money(sum(r["estimated_cost"] or 0.0 for r in rows))
    message = (say(ctx, "Reorder plan grouped by criticality, class A first. No orders were "
                        "created; raise them with create_po.",
                   "补货计划按重要等级分组，A 类在前。尚未创建任何采购单，可用 create_po 下单。")
               if rows else
               say(ctx, "No SKU is below its reorder point, so there is nothing to reorder.",
                   "没有低于再订货点的 SKU，无需补货。"))
    scope = describe_filters(ctx.registry, "reorder_plan",
                             {"criticality": data.criticality, "category": data.category},
                             ctx.language) or say(ctx, "all low-stock SKUs", "全部低库存 SKU")
    return ToolResult(rows=rows, columns=PLAN_COLUMNS, aggregates=aggregates, message=message,
                      provenance=Provenance(tool="reorder_plan", row_count=len(rows),
                                            as_of=ctx.now(), scope=scope))


def narration_question(ctx: ToolContext, result: ToolResult) -> str:
    return QUESTION


REORDER_PLAN = Workflow(
    name="reorder_plan",
    title={"en": "Reorder plan", "zh": "补货计划"},
    description={
        "en": "Full reorder plan: find low-stock SKUs, compute order quantities, group them by "
              "criticality and pick a supplier for each, then summarize.",
        "zh": "完整的补货计划：找出低库存物料，计算采购数量，按重要等级分组并为每个物料选定供应商，"
              "最后汇总。"},
    route_hint="Use for a complete purchasing plan with suppliers. Read-only: it creates no "
               "orders.",
    keywords=("reorder plan", "replenishment plan", "restock plan", "purchasing plan",
              "procurement plan", "purchase plan", "补货计划", "采购计划", "补货方案"),
    examples=("build a reorder plan", "make a replenishment plan for class A items",
              "生成补货计划"),
    roles=READ_ROLES, effect=ResourceEffect.READ, approval=Approval.NONE, explorable=True,
    requires=("has_inventory_data",),
    outputs=ToolOutputs(
        aggregates=("sku_count", "total_suggested_qty",
                    *(f"class_{level.lower()}_skus" for level in CRITICALITY),
                    "supplier_count", "estimated_cost"),
        columns=PLAN_COLUMNS),
    param_extractor=extractor("criticality"),
    aggregate_labels=labels(
        sku_count=("SKUs to order", "需要补货的 SKU"),
        total_suggested_qty=("Suggested order quantity (units)", "建议采购量（件）"),
        class_a_skus=("Class A SKUs", "A 类 SKU"),
        class_b_skus=("Class B SKUs", "B 类 SKU"),
        class_c_skus=("Class C SKUs", "C 类 SKU"),
        supplier_count=("Suppliers involved", "涉及供应商"),
        estimated_cost=("Estimated cost", "预计成本")),
    steps=(
        Step(id="find_low_stock", kind=StepKind.DETERMINISTIC,
             description="Find SKUs below their reorder point.", fn=find_low_stock,
             input_model=ReorderPlanInput, output_model=LowStockSet),
        Step(id="compute_suggestions", kind=StepKind.DETERMINISTIC,
             description="Compute the suggested order quantity of each low SKU.",
             fn=compute_suggestions, input_model=LowStockSet, output_model=SuggestionSet),
        Step(id="group_and_source", kind=StepKind.DETERMINISTIC,
             description="Group by criticality and pick the best-ranked supplier per SKU.",
             fn=group_and_source, input_model=SuggestionSet, output_model=ToolResult),
        Step(id="narrate", kind=StepKind.LLM, description=QUESTION, fn=narration_question),
    ),
)
