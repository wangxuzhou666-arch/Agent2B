"""Shared test world for the controlled-execution stack: a part catalog with two categories."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from twotrack.core import (
    Approval,
    EntitySpec,
    FieldSpec,
    FieldType,
    Plugin,
    Precondition,
    Principal,
    Provenance,
    Sensitivity,
    Step,
    StepKind,
    Tool,
    ToolContext,
    ToolOutputs,
    ToolResult,
    Workflow,
    build_registry,
)
from twotrack.core.audit import AuditError, AuditStatus, SqliteAuditSink
from twotrack.core.database import Database
from twotrack.core.grants import SqliteGrantStore
from twotrack.core.pipeline import Pipeline
from twotrack.core.store import SqliteStore

OPS = frozenset({"operator"})

PART = EntitySpec(
    id="part", label="Part", primary_key="sku", label_field="name",
    fields=(
        FieldSpec(id="sku", label="SKU", required=True, aliases=("part code", "item no")),
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
FASTENER = EntitySpec(
    id="fastener", label="Fastener", extends="part",
    attrs=(FieldSpec(id="thread_pitch", label="Thread Pitch", type=FieldType.FLOAT,
                     required=True),),
)
CABLE = EntitySpec(
    id="cable", label="Cable", extends="part",
    attrs=(FieldSpec(id="length_m", label="Length", type=FieldType.FLOAT),
           FieldSpec(id="vendor_ref", label="Vendor Ref", sensitivity=Sensitivity.RESTRICTED,
                     visible_roles=OPS)),
)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetQty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    qty: int = Field(ge=0)
    unit_cost: float | None = None


def _result(ctx, tool, rows=(), **aggregates):
    return ToolResult(rows=list(rows), aggregates=aggregates,
                      provenance=Provenance(tool=tool, row_count=len(rows), as_of=ctx.now()))


def part_count(ctx, params):
    return _result(ctx, "part_count", part_count=ctx.store.count("part"))


def set_qty(ctx, params):
    row = ctx.store.get("part", params.sku) or {"sku": params.sku, "name": params.sku}
    entity = row.pop("_entity", "part")
    row.update(qty=params.qty)
    if params.unit_cost is not None:
        row["unit_cost"] = params.unit_cost
    ctx.store.upsert_many(entity, [row])
    return _result(ctx, "set_qty", rows=[row])


def sneaky_write(ctx, params):
    ctx.store.upsert_many("part", [{"sku": "X-1", "name": "sneaky"}])
    return _result(ctx, "sneaky_write")


def write_then_fail(ctx, params):
    ctx.store.upsert_many("part", [{"sku": params.sku, "name": "half", "qty": params.qty}])
    raise RuntimeError("downstream failure")


# set_qty returns the stored part, whichever category it belongs to
PART_COLUMNS = ("sku", "name", "qty", "unit_cost", "supplier", "status", "thread_pitch",
                "length_m", "vendor_ref")
NO_OUTPUT = ToolOutputs(aggregates=(), columns=())

PLUGIN = Plugin(
    name="parts", version="0.1.0", entities=(PART, FASTENER, CABLE),
    preconditions=(Precondition(name="has_upload", description="Upload a file first.",
                                check=lambda ctx: bool(ctx.facts.get("upload"))),),
    tools=(
        Tool(name="part_count", description="Count parts.", params_model=NoParams,
             handler=part_count, roles=("viewer", "operator"), effect="read", approval="none",
             outputs=ToolOutputs(aggregates=("part_count",), columns=()), explorable=False,
             entity="part"),
        Tool(name="set_qty", description="Set the on-hand quantity of a part.",
             params_model=SetQty, handler=set_qty, roles=("operator",), effect="write",
             approval="required", outputs=ToolOutputs(aggregates=(), columns=PART_COLUMNS),
             explorable=False, entity="part"),
        Tool(name="gated_set_qty", description="Set quantity from an uploaded sheet.",
             params_model=SetQty, handler=set_qty, roles=("operator",), effect="write",
             approval="required", outputs=ToolOutputs(aggregates=(), columns=PART_COLUMNS),
             explorable=False, requires=("has_upload",), entity="part"),
        Tool(name="sneaky_write", description="Read tool that tries to write.",
             params_model=NoParams, handler=sneaky_write, roles=("operator",), effect="read",
             approval="none", outputs=NO_OUTPUT, explorable=False),
        Tool(name="write_then_fail", description="Writes, then fails.", params_model=SetQty,
             handler=write_then_fail, roles=("operator",), effect="write", approval="required",
             outputs=NO_OUTPUT, explorable=False),
    ),
    workflows=(
        Workflow(name="restock_all", description="Restock every part.", roles=("operator",),
                 effect="write", approval=Approval.REQUIRED, explorable=False,
                 outputs=ToolOutputs(aggregates=("part_count",), columns=()),
                 steps=(Step(id="compute", kind=StepKind.DETERMINISTIC, description="compute",
                             fn=lambda ctx, data: data),)),
    ),
)

# A root entity with admin-only fields, for the import tests: an operator can import it but may
# not see (so may not write) its contact and discount columns.
VENDOR = EntitySpec(
    id="vendor", label="Vendor", primary_key="vendor_id", label_field="name",
    fields=(
        FieldSpec(id="vendor_id", label="Vendor ID", required=True),
        FieldSpec(id="name", label="Name", required=True, sensitivity=Sensitivity.PUBLIC),
        FieldSpec(id="region", label="Region"),
        FieldSpec(id="contact_email", label="Contact Email", aliases=("email",),
                  sensitivity=Sensitivity.RESTRICTED, visible_roles=frozenset({"admin"})),
        FieldSpec(id="discount_pct", label="Discount %", type=FieldType.FLOAT,
                  aliases=("discount",), sensitivity=Sensitivity.RESTRICTED,
                  visible_roles=frozenset({"admin"})),
    ),
)
VENDOR_PLUGIN = Plugin(name="vendors", version="0.1.0", entities=(VENDOR,))
VENDOR_ROW = {"vendor_id": "V-1", "name": "Acme", "region": "EU",
              "contact_email": "orders@acme.example", "discount_pct": 1.2}

VIEWER = Principal(user_id="vera", roles=frozenset({"viewer"}))
OPERATOR = Principal(user_id="olga", roles=frozenset({"operator"}))
OPERATOR_2 = Principal(user_id="omar", roles=frozenset({"operator"}))
ADMIN = Principal(user_id="ada", roles=frozenset({"admin"}))


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FailingAudit:
    """Wraps a real sink and fails on chosen statuses (to prove audit is fail-closed)."""

    def __init__(self, inner, fail_on) -> None:
        self.inner, self.fail_on = inner, set(fail_on)

    def record(self, entry):
        if entry.status in self.fail_on:
            raise AuditError("disk full")
        return self.inner.record(entry)

    def list(self, *args, **kwargs):
        return self.inner.list(*args, **kwargs)

    def get(self, entry_id):
        return self.inner.get(entry_id)


@dataclass
class World:
    registry: object
    db: Database
    store: SqliteStore
    audit: SqliteAuditSink
    grants: SqliteGrantStore
    clock: FakeClock
    pipeline: Pipeline
    seeded: list = field(default_factory=list)

    def ctx(self, principal, **facts) -> ToolContext:
        return ToolContext(principal=principal, facts=facts, clock=self.clock)

    def with_audit(self, sink) -> Pipeline:
        return Pipeline(self.registry, self.store, sink, self.grants, clock=self.clock)

    def audit_statuses(self) -> list[AuditStatus]:
        return [e.status for e in reversed(self.audit.list(limit=500).items)]


def build_world(seed: bool = True, extra_plugins: tuple[Plugin, ...] = ()) -> World:
    registry = build_registry([PLUGIN, *extra_plugins])
    db = Database()
    store = SqliteStore(db)
    store.ensure_schema(registry)
    clock = FakeClock()
    audit = SqliteAuditSink(db)
    grants = SqliteGrantStore(db, clock=clock)
    pipeline = Pipeline(registry, store, audit, grants, clock=clock)
    world = World(registry, db, store, audit, grants, clock, pipeline)
    if seed:
        store.upsert_many("fastener", [
            {"sku": "F-1", "name": "Hex bolt", "qty": 40, "unit_cost": 0.25,
             "supplier": "Supplier Alpha", "thread_pitch": 1.25},
        ])
        store.upsert_many("cable", [
            {"sku": "C-1", "name": "Patch lead", "qty": 7, "unit_cost": 3.5,
             "supplier": "Supplier Beta", "length_m": 2.0, "vendor_ref": "CW-REF-889"},
        ])
    return world
