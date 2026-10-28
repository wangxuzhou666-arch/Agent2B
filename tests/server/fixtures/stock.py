"""Fixture plugin for the server tests.

Two root entities (``item`` with a ``bolt`` category, and ``bin``), restricted fields on both,
read tools, a write tool, a precondition that reads the store, and a deliberately slow tool
(for the heartbeat test).
"""

import time

from pydantic import BaseModel, ConfigDict, Field

from twotrack.core import (
    EntitySpec,
    FieldSpec,
    FieldType,
    Plugin,
    Precondition,
    Provenance,
    Sensitivity,
    Tool,
    ToolOutputs,
    ToolResult,
)

OPS = frozenset({"operator"})

ITEM = EntitySpec(
    id="item", label="Item", primary_key="sku", label_field="name",
    fields=(
        FieldSpec(id="sku", label="SKU", required=True, aliases=("item code",)),
        FieldSpec(id="name", label="Name", sensitivity=Sensitivity.PUBLIC),
        FieldSpec(id="qty", label="Quantity", type=FieldType.INT, aliases=("qty on hand",)),
        FieldSpec(id="unit_cost", label="Unit Cost", type=FieldType.FLOAT,
                  sensitivity=Sensitivity.RESTRICTED, visible_roles=OPS),
        FieldSpec(id="supplier", label="Supplier", sensitivity=Sensitivity.RESTRICTED,
                  visible_roles=OPS),
        FieldSpec(id="status", label="Status", type=FieldType.ENUM,
                  enum_values=("active", "retired")),
    ),
)
BOLT = EntitySpec(
    id="bolt", label="Bolt", extends="item", description="Threaded fasteners",
    attrs=(FieldSpec(id="thread_pitch", label="Thread Pitch", type=FieldType.FLOAT,
                     required=True),
           FieldSpec(id="vendor_ref", label="Vendor Ref", sensitivity=Sensitivity.RESTRICTED,
                     visible_roles=OPS)),
)
# A second root entity: its fields show up in rows of tools bound to `item`.
BIN = EntitySpec(
    id="bin", label="Bin", primary_key="bin_id", label_field="bin_id",
    fields=(
        FieldSpec(id="bin_id", label="Bin ID", required=True),
        FieldSpec(id="zone", label="Zone", sensitivity=Sensitivity.PUBLIC),
        FieldSpec(id="rent", label="Rent", type=FieldType.FLOAT,
                  sensitivity=Sensitivity.RESTRICTED, visible_roles=OPS),
    ),
)
BINS = {"A-1": ("BIN-1", "north", 12.5), "A-2": ("BIN-1", "north", 12.5),
        "A-3": ("BIN-2", "south", 7.0)}


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetQty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    qty: int = Field(ge=0)


def _result(ctx, tool, rows=(), columns=(), **aggregates) -> ToolResult:
    return ToolResult(
        rows=list(rows), columns=list(columns), aggregates=aggregates,
        provenance=Provenance(tool=tool, row_count=len(rows), as_of=ctx.now(), scope="all items"))


def item_count(ctx, params):
    return _result(ctx, "item_count", item_count=ctx.store.count("item"))


def item_list(ctx, params):
    page = ctx.store.list("item", sort="sku", limit=20)
    rows = [{**row, "reorder_flag": "yes" if (row["qty"] or 0) < 10 else "no"}
            for row in page.rows]
    columns = ["sku", "name", "qty", "unit_cost", "supplier", "reorder_flag"]
    return _result(ctx, "item_list", rows, columns, listed=len(rows))


def item_locations(ctx, params):
    """Item rows joined with their bin: `zone` and `rent` are declared by `bin`, not `item`."""
    rows = []
    for item in ctx.store.list("item", sort="sku", limit=20).rows:
        if item["sku"] in BINS:
            bin_id, zone, rent = BINS[item["sku"]]
            rows.append({"sku": item["sku"], "name": item["name"], "bin_id": bin_id,
                         "zone": zone, "rent": rent, "qty": item["qty"]})
    columns = ["sku", "name", "bin_id", "zone", "rent", "qty"]
    return _result(ctx, "item_locations", rows, columns, located=len(rows))


def item_audit(ctx, params):
    return _result(ctx, "item_audit", audited=ctx.store.count("item"))


def set_item_qty(ctx, params):
    row = ctx.store.get("item", params.sku)
    if row is None:
        raise ValueError("unknown sku")
    entity = row.pop("_entity")
    row["qty"] = params.qty
    ctx.store.upsert_many(entity, [row])
    return _result(ctx, "set_item_qty", new_qty=params.qty)


def slow_report(ctx, params):
    time.sleep(0.4)
    return _result(ctx, "slow_report", slow_count=1)


# every key an item row can carry (a bolt adds its attrs); item_list passes stored rows through
ITEM_COLUMNS = ("sku", "name", "qty", "unit_cost", "supplier", "status", "thread_pitch",
                "vendor_ref")

PLUGIN = Plugin(
    name="stock", version="0.1.0", entities=(ITEM, BOLT, BIN),
    # reads the store: the routing context must carry one, or this fails closed and the tool
    # behind it silently disappears
    preconditions=(Precondition(name="has_items", description="No items on record yet.",
                                check=lambda ctx: ctx.store.count("item") > 0),),
    tools=(
        Tool(name="item_count", description="Count the items on record.", params_model=NoParams,
             handler=item_count, roles=("viewer", "operator"), effect="read", approval="none",
             outputs=ToolOutputs(aggregates=("item_count",), columns=()), explorable=True,
             entity="item", keywords=("how many items", "item count"),
             examples=("how many items do we have",)),
        Tool(name="item_list", description="List items with their stock and cost.",
             params_model=NoParams, handler=item_list, roles=("viewer", "operator"),
             effect="read", approval="none", explorable=False,
             outputs=ToolOutputs(aggregates=("listed",), columns=(*ITEM_COLUMNS, "reorder_flag")),
             entity="item", keywords=("list items", "show items"),
             examples=("show all items",)),
        Tool(name="item_locations", description="Items with the bin they sit in.",
             params_model=NoParams, handler=item_locations, roles=("viewer", "operator"),
             effect="read", approval="none", explorable=False,
             outputs=ToolOutputs(aggregates=("located",),
                                 columns=("sku", "name", "bin_id", "zone", "rent", "qty")),
             entity="item", keywords=("item locations", "where are items")),
        Tool(name="item_audit", description="Audit the item records.", params_model=NoParams,
             handler=item_audit, roles=("viewer", "operator"), effect="read", approval="none",
             outputs=ToolOutputs(aggregates=("audited",), columns=()), explorable=False,
             requires=("has_items",), keywords=("audit items", "item audit"),
             examples=("run an item audit",)),
        Tool(name="set_item_qty", description="Set the on-hand quantity of an item.",
             params_model=SetQty, handler=set_item_qty, roles=("operator",), effect="write",
             approval="required", outputs=ToolOutputs(aggregates=("new_qty",), columns=()),
             explorable=True, entity="item",
             keywords=("set item quantity", "update item stock")),
        Tool(name="slow_report", description="A report that takes a moment.",
             params_model=NoParams, handler=slow_report, roles=("viewer", "operator"),
             effect="read", approval="none",
             outputs=ToolOutputs(aggregates=("slow_count",), columns=()), explorable=False,
             keywords=("slow report", "take a moment")),
    ),
)
