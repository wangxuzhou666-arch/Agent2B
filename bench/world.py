"""A seeded in-memory twotrack world for the benchmark: registry, store, audit, grants, pipeline.

Wired directly from core parts (no server, no runtime module), the same way the test worlds are.
Built-in plugins only, whatever ``TWOTRACK_PLUGIN_PATHS`` says, so every run has the same tools.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from twotrack.core import Registry, ToolContext, ToolResult, Workflow, load_registry
from twotrack.core.audit import SqliteAuditSink
from twotrack.core.database import Database
from twotrack.core.grants import SqliteGrantStore
from twotrack.core.pipeline import Pipeline
from twotrack.core.store import ReadOnlyStore, SqliteStore
from twotrack.core.workflow import run_workflow
from twotrack.plugins.supply_chain import logic
from twotrack.providers.mock import MockProvider
from twotrack.seed import seed_database

from .config import PRINCIPAL, RNG_SEED, SCALE, TODAY, clock_for

DIGEST_ENTITIES = ("sku", "inventory", "supplier", "purchase_order")


@dataclass
class World:
    registry: Registry
    db: Database
    store: SqliteStore
    pipeline: Pipeline
    today: date
    seeded: dict[str, int] = field(default_factory=dict)

    @property
    def now(self) -> datetime:
        return clock_for(self.today)

    def ctx(self) -> ToolContext:
        return ToolContext(principal=PRINCIPAL, store=ReadOnlyStore(self.store),
                           clock=fixed_clock(self.now))

    def close(self) -> None:
        self.db.close()


def fixed_clock(now: datetime) -> Callable[[], datetime]:
    def clock() -> datetime:
        return now

    return clock


def build_world(*, rng_seed: int = RNG_SEED, scale: float = SCALE,
                today: date = TODAY) -> World:
    registry = load_registry(extra_paths=[])
    db = Database()
    store = SqliteStore(db)
    store.ensure_schema(registry)
    clock = fixed_clock(clock_for(today))
    pipeline = Pipeline(registry, store, SqliteAuditSink(db), SqliteGrantStore(db, clock=clock),
                        clock=clock)
    seeded = seed_database(store, registry, rng_seed=rng_seed, scale=scale, today=today)
    return World(registry=registry, db=db, store=store, pipeline=pipeline, today=today,
                 seeded=seeded)


def template_workflow_runner(ctx: ToolContext, workflow: Workflow, params: Any) -> ToolResult:
    """Workflow runner for the baseline's tool calls: deterministic steps, template narration.
    The baseline model writes its own answer, so the workflow's narrate step needs no model."""
    return run_workflow(ctx, workflow, params, MockProvider())


def data_digest(world: World) -> str:
    """Fingerprint of every business row; recorded before and after a run to prove that no
    system changed the data it was measured on."""
    snapshot = {entity: sorted(logic.fetch_all(world.store, entity),
                               key=lambda r: json.dumps(r, sort_keys=True, default=str))
                for entity in DIGEST_ENTITIES}
    blob = json.dumps(snapshot, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
