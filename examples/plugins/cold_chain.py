"""Drop-in example: a third item category, added without touching core or built-in code.

Load it by pointing the plugin path at this directory and restarting::

    TWOTRACK_PLUGIN_PATHS=examples/plugins

``cold_chain_item`` extends the built-in ``sku`` entity, so its rows live in the existing SKU
table (the two new fields are attrs stored as JSON, never new columns). Every built-in tool
(inventory, low stock, reorder suggestions, purchase orders) works on it unchanged, and the
ones that filter by category accept ``category="cold_chain_item"``; a question that says
"cold chain" (or 冷链) is bound to that filter through ``synonyms`` alone.

``expiring_soon`` estimates expiry from the inventory snapshot: the base model has no lot or
receipt dates, so stock is assumed to have arrived on the inventory record's ``as_of`` date
and to expire ``shelf_life_days`` later. A real deployment would add lot tracking.
"""

import re
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field

from twotrack.core import (
    Approval,
    EntitySpec,
    FieldSpec,
    FieldType,
    Plugin,
    Provenance,
    ResourceEffect,
    Tool,
    ToolContext,
    ToolOutputs,
    ToolResult,
)

PAGE_SIZE = 1000
# what the handler may return: the pipeline refuses any other aggregate key or column
EXPIRING_AGGREGATES = ("item_count", "expiring_units", "soonest_days_left")
EXPIRING_COLUMNS = ["sku_id", "sku_name", "warehouse", "on_hand", "storage_temp_c", "expires_on",
                    "days_left"]
_WITHIN = re.compile(r"(\d+)\s*(?:days?|天)", re.IGNORECASE)

COLD_CHAIN_ITEM = EntitySpec(
    id="cold_chain_item",
    # labels and descriptions are plain strings (the same in every language) or per language
    label={"en": "Cold-Chain Item", "zh": "冷链物料"},
    description={"en": "A temperature-controlled item with a limited shelf life.",
                 "zh": "有保质期、需要控温储存的物料。"},
    extends="sku",
    # how a question names the category, besides its id and its label in each language; built-in
    # tools that filter by category pick it up from these declarations alone. The scope guard
    # shows it by its label, like the console.
    synonyms={"en": ("cold chain", "refrigerated items"), "zh": ("冷链物料", "冷链")},
    attrs=(
        FieldSpec(id="storage_temp_c", label={"en": "Storage Temp (C)", "zh": "储存温度"},
                  description={"en": "Required storage temperature in degrees Celsius.",
                               "zh": "要求的储存温度，单位摄氏度。"},
                  type=FieldType.FLOAT, unit="C",
                  aliases=("storage temperature", "temp c", "储存温度")),
        FieldSpec(id="shelf_life_days", label={"en": "Shelf Life (days)", "zh": "保质期"},
                  description={"en": "Days the item stays usable after it is received.",
                               "zh": "物料收货后可以使用的天数。"},
                  type=FieldType.INT, required=True, unit="days",
                  aliases=("shelf life", "保质期")),
    ),
)


class ExpiringSoonParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    within_days: int = Field(default=14, ge=0, le=365)


def within_days(query: str) -> dict:
    match = _WITHIN.search(query)
    return {"within_days": int(match.group(1))} if match else {}


def _all(store, entity_id: str) -> list[dict]:
    rows: list[dict] = []
    while True:
        page = store.list(entity_id, limit=PAGE_SIZE, offset=len(rows))
        rows.extend(page.rows)
        if not page.rows or len(rows) >= page.total:
            return rows


def expiring_soon(ctx: ToolContext, params: ExpiringSoonParams) -> ToolResult:
    today = ctx.now().date()
    horizon = today + timedelta(days=params.within_days)
    items = {r["sku_id"]: r for r in _all(ctx.store, "cold_chain_item")}
    rows = []
    for inv in _all(ctx.store, "inventory"):
        item = items.get(inv["sku_id"])
        if item is None or not inv.get("on_hand") or not inv.get("as_of"):
            continue
        expires = date.fromisoformat(inv["as_of"]) + timedelta(days=item["shelf_life_days"])
        if expires <= horizon:
            rows.append({"sku_id": item["sku_id"], "sku_name": item.get("name"),
                         "warehouse": inv["warehouse"], "on_hand": inv["on_hand"],
                         "storage_temp_c": item.get("storage_temp_c"),
                         "expires_on": expires.isoformat(),
                         "days_left": (expires - today).days})
    rows.sort(key=lambda r: (r["days_left"], r["sku_id"], r["warehouse"]))
    aggregates = {
        "item_count": len({r["sku_id"] for r in rows}),
        "expiring_units": sum(r["on_hand"] for r in rows),
        "soonest_days_left": min((r["days_left"] for r in rows), default=0),
    }
    return ToolResult(
        rows=rows, aggregates=aggregates, columns=EXPIRING_COLUMNS,
        message="" if rows else "No cold-chain stock expires within the window.",
        provenance=Provenance(tool="expiring_soon", row_count=len(rows), as_of=ctx.now(),
                              scope=f"cold-chain stock expiring by {horizon.isoformat()}"),
    )


EXPIRING_SOON = Tool(
    name="expiring_soon",
    title={"en": "Expiring soon", "zh": "临期库存"},  # what the console calls this report
    # a plain string, or per language like the title; routing reads every language's text
    description={
        "en": "Cold-chain stock that expires within N days (default 14), soonest first. "
              "Expiry = inventory as_of date + shelf_life_days.",
        "zh": "N 天内（默认 14 天）到期的冷链物料库存，最早到期的在前。到期日 = 库存的盘点日期 + "
              "保质期天数。"},
    route_hint="Use for shelf life, expiry or spoilage of temperature-controlled items.",
    keywords=("expiring", "expiry", "expire", "shelf life", "spoil", "cold chain", "临期",
              "过期", "保质期"),
    examples=("which cold chain items are expiring soon", "what expires this week",
              "哪些冷链物料快过期了"),
    roles=("admin", "operator", "viewer"),
    effect=ResourceEffect.READ,
    approval=Approval.NONE,  # a read: there is nothing to approve
    outputs=ToolOutputs(aggregates=EXPIRING_AGGREGATES, columns=EXPIRING_COLUMNS),
    explorable=True,  # a read-only report is fine to offer to open analysis
    params_model=ExpiringSoonParams,
    handler=expiring_soon,
    # no ``entity``: rows join inventory records with item attrs, so no single entity's
    # field list describes them (restricted fields are still hidden by name)
    param_extractor=within_days,
    # it only ever reports cold-chain items, so "cold chain" in a question is already applied
    implied_scope={"category:sku": "cold_chain_item"},
)

PLUGIN = Plugin(
    name="cold_chain",
    version="0.1.0",
    description="Drop-in example: a cold-chain item category and an expiry report.",
    entities=(COLD_CHAIN_ITEM,),
    tools=(EXPIRING_SOON,),
)
