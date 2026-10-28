"""Test world for the built-in supply-chain plugins: real registry, store, audit, grants and
pipeline over an in-memory database, with a fixed clock."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from twotrack.core import Principal, ToolContext, load_registry
from twotrack.core.audit import SqliteAuditSink
from twotrack.core.database import Database
from twotrack.core.grants import SqliteGrantStore
from twotrack.core.pipeline import Pipeline
from twotrack.core.store import ReadOnlyStore, SqliteStore
from twotrack.core.workflow import run_workflow
from twotrack.providers.mock import MockProvider
from twotrack.seed import seed_database

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
TODAY = NOW.date()

VIEWER = Principal(user_id="vera", roles=frozenset({"viewer"}))
OPERATOR = Principal(user_id="olga", roles=frozenset({"operator"}))
ADMIN = Principal(user_id="ada", roles=frozenset({"admin"}))


def builtin_registry():
    return load_registry(extra_paths=[])  # built-ins only, whatever the environment says


@dataclass
class World:
    registry: Any
    db: Database
    store: SqliteStore
    audit: SqliteAuditSink
    grants: SqliteGrantStore
    pipeline: Pipeline

    def ctx(self, principal: Principal = OPERATOR, **facts: Any) -> ToolContext:
        # the registry, as the pipeline hands it to targets, so direct calls name filters alike
        return ToolContext(principal=principal, store=ReadOnlyStore(self.store), facts=facts,
                           clock=lambda: NOW, registry=self.registry)

    def run(self, principal: Principal, target: str, params: dict[str, Any] | None = None,
            **kwargs: Any):
        runner = kwargs.pop("workflow_runner", None) or (
            lambda ctx, wf, p: run_workflow(ctx, wf, p, MockProvider()))
        return self.pipeline.run(self.ctx(principal), target, params or {},
                                 workflow_runner=runner, **kwargs)

    def call(self, target: str, params: dict[str, Any] | None = None,
             principal: Principal = OPERATOR):
        """Run a read tool's handler directly (no audit); returns its ToolResult."""
        tool = self.registry.tool(target)
        return tool.handler(self.ctx(principal), tool.parse_params(params or {}))


def build_world(registry=None, *, seed: bool = False, today: date = TODAY) -> World:
    registry = registry or builtin_registry()
    db = Database()
    store = SqliteStore(db)
    store.ensure_schema(registry)
    audit = SqliteAuditSink(db)
    grants = SqliteGrantStore(db, clock=lambda: NOW)
    world = World(registry, db, store, audit, grants,
                  Pipeline(registry, store, audit, grants, clock=lambda: NOW))
    if seed:
        seed_database(store, registry, today=today)
    return world


def sku(sku_id: str, *, entity: str = "spare_part", **fields: Any) -> tuple[str, dict]:
    """A SKU row with attr defaults for its category."""
    row = {"sku_id": sku_id, "name": f"Item {sku_id}", **fields}
    if entity == "spare_part":
        row.setdefault("machine_model", "CNV-100")
    elif entity == "packaging":
        row.setdefault("material", "cardboard")
    return entity, row


def stock(record_id: str, sku_id: str, warehouse: str, on_hand: int, reserved: int = 0) -> dict:
    return {"id": record_id, "sku_id": sku_id, "warehouse": warehouse, "on_hand": on_hand,
            "reserved": reserved, "as_of": TODAY.isoformat()}


def po(po_id: str, sku_id: str, supplier_id: str, qty: int, status: str, *,
       eta: str = "2026-10-20", ordered_at: str = "2026-09-01",
       unit_price: float | None = None) -> dict:
    return {"po_id": po_id, "sku_id": sku_id, "supplier_id": supplier_id, "qty": qty,
            "status": status, "eta": eta, "ordered_at": ordered_at, "unit_price": unit_price}


def load(world: World, *, skus=(), inventory=(), suppliers=(), orders=()) -> None:
    by_entity: dict[str, list[dict]] = {}
    for entity, row in skus:
        by_entity.setdefault(entity, []).append(row)
    for entity, rows in by_entity.items():
        world.store.upsert_many(entity, rows)
    for entity, rows in (("inventory", inventory), ("supplier", suppliers),
                         ("purchase_order", orders)):
        if rows:
            world.store.upsert_many(entity, list(rows))
