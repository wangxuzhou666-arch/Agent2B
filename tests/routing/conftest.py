"""The built-in plugins over a seeded in-memory store, for routing tests that need real tools
(their preconditions read the store), and the engine over them for tests that follow a question
past routing (a write's one-time confirmation). ``with_scorecard`` adds one drop-in example
plugin on top, loaded the way an operator loads it."""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from twotrack.core import Principal, ToolContext, load_registry
from twotrack.core.audit import SqliteAuditSink
from twotrack.core.database import Database
from twotrack.core.grants import SqliteGrantStore
from twotrack.core.graph import Engine, QueryResponse, run_query
from twotrack.core.pipeline import Pipeline
from twotrack.core.routing import decide_route
from twotrack.core.store import ReadOnlyStore, SqliteStore
from twotrack.providers.mock import MockProvider
from twotrack.seed import seed_database

TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
OPERATOR = Principal(user_id="router-test", roles=frozenset({"operator"}))
SUPPLIER_SCORECARD = (Path(__file__).resolve().parents[2] / "examples" / "plugins"
                      / "supplier_scorecard.py")


@dataclass
class Builtin:
    registry: Any
    store: SqliteStore
    pipeline: Pipeline

    def ctx(self, query: str = "", principal: Principal = OPERATOR, sink=None) -> ToolContext:
        return ToolContext(principal=principal, store=ReadOnlyStore(self.store),
                           facts={"query": query}, clock=lambda: NOW,
                           trace=sink or (lambda step: None))

    def candidates(self, principal: Principal = OPERATOR):
        return self.registry.candidates(self.ctx(principal=principal))

    def decide(self, query: str, provider: Any = None, sink=None):
        return decide_route(query, self.ctx(query, sink=sink), self.registry,
                            provider or MockProvider())

    def ask(self, query: str, principal: Principal = OPERATOR) -> QueryResponse:
        """The whole question through the graph on the mock provider (routing, the pipeline's
        gates, narration), as the console asks it."""
        engine = Engine(registry=self.registry, provider=MockProvider(), pipeline=self.pipeline)
        return run_query(engine, self.ctx(principal=principal), query)


def _seeded(registry: Any) -> Iterator[Builtin]:
    database = Database()
    store = SqliteStore(database)
    store.ensure_schema(registry)
    seed_database(store, registry, today=TODAY)
    audit = SqliteAuditSink(database)
    grants = SqliteGrantStore(database, clock=lambda: NOW)
    yield Builtin(registry, store, Pipeline(registry, store, audit, grants, clock=lambda: NOW))
    database.close()


@pytest.fixture(scope="module")
def builtin():
    yield from _seeded(load_registry(extra_paths=[]))  # built-in plugins only


@pytest.fixture(scope="module")
def with_scorecard(tmp_path_factory):
    """The built-in plugins plus the supplier scorecard and nothing else: the one example file,
    alone in a plugin directory, found by the registry's own discovery."""
    directory = tmp_path_factory.mktemp("plugins")
    shutil.copy(SUPPLIER_SCORECARD, directory)
    yield from _seeded(load_registry(extra_paths=[directory]))
