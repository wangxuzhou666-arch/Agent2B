"""Assembly: settings in, a fully wired runtime out. The only place the parts meet.

Startup order matters and is fixed:

1. the plugin registry (a bad plugin stops the start, listing every problem) and the model
   provider, checked with the exploration settings before anything is created on disk, so a
   refused start leaves no data directory or database behind;
2. one ``Database`` shared by the store, the audit log, the grant store, users, pending
   approvals and the deny-list of signed-out tokens, so a write and its audit record commit in
   the same transaction;
3. schema check (an existing database that disagrees with the registry stops the start);
4. pipeline, import service and egress guard (its restricted values come from the store);
5. the engine, whose provider is scrubbed by the egress guard on every call, whose
   pipeline remembers writes that wait for confirmation, and which completes each query's
   audit trail in the shared audit log;
6. the signing secret and, in demo mode, seed data.
"""

import importlib.util
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from twotrack.core import Registry, load_registry
from twotrack.core.audit import SqliteAuditSink
from twotrack.core.database import Database
from twotrack.core.entities import ImportService
from twotrack.core.explore.explorer import Explorer
from twotrack.core.explore.exposure import check_allowlist
from twotrack.core.explore.pi import pi_explorer
from twotrack.core.grants import SqliteGrantStore
from twotrack.core.graph import Engine, GraphSettings
from twotrack.core.guards import EgressGuard, UploadLimits, restricted_values_source
from twotrack.core.pipeline import Pipeline
from twotrack.core.secrets import load_or_create_secret
from twotrack.core.store import SqliteStore
from twotrack.providers import provider_from_env
from twotrack.providers.base import Provider
from twotrack.server.approvals import ApprovalRecordingPipeline, ApprovalStore
from twotrack.server.auth import RevokedTokens, TokenService, UserStore
from twotrack.settings import Settings

log = logging.getLogger("twotrack.runtime")

SEED_MODULE = "twotrack.seed"


@dataclass
class Runtime:
    settings: Settings
    database: Database
    registry: Registry
    store: SqliteStore
    audit: SqliteAuditSink
    grants: SqliteGrantStore
    pipeline: Pipeline  # the plain pipeline; approvals replay through it
    importer: ImportService
    egress: EgressGuard
    provider: Provider
    engine: Engine
    users: UserStore
    approvals: ApprovalStore
    tokens: TokenService
    clock: Callable[[], datetime]

    def close(self) -> None:
        self.database.close()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def prepare_data_dir(settings: Settings) -> None:
    """The data directory holds the database and the signing secret: owner access only."""
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)


def open_store(settings: Settings, registry: Registry) -> tuple[Database, SqliteStore]:
    prepare_data_dir(settings)
    database = Database(settings.db_path)
    if os.name != "nt":  # the directory is 0700 when we made it; do not rely on that alone
        settings.db_path.chmod(0o600)
    store = SqliteStore(database)
    try:
        store.ensure_schema(registry)
    except BaseException:
        database.close()
        raise
    return database, store


def store_is_empty(store: SqliteStore, registry: Registry) -> bool:
    roots = [e.id for e in registry.entities.values() if e.extends is None]
    return all(store.count(root) == 0 for root in roots)


def seed_if_empty(store: SqliteStore, registry: Registry) -> dict[str, int] | None:
    """Demo data for a fresh database. A missing seed module, or a registry the seed does not
    fit, is skipped with a warning; any other failure stops the start."""
    if importlib.util.find_spec(SEED_MODULE) is None:
        log.warning("demo mode: %s is not installed, starting with an empty database",
                    SEED_MODULE)
        return None
    if not store_is_empty(store, registry):
        return None
    from twotrack.seed import seed_database

    try:
        counts = seed_database(store, registry)
    except ValueError as exc:  # its signal for "this registry lacks the built-in entities"
        log.warning("demo mode: not seeding, %s", exc)
        return None
    log.info("demo mode: seeded %s", counts)
    return counts


def build_runtime(
    settings: Settings,
    *,
    registry: Registry | None = None,
    provider: Provider | None = None,
    clock: Callable[[], datetime] | None = None,
    explorer: Explorer | None = None,
) -> Runtime:
    """``registry``, ``provider``, ``clock`` and ``explorer`` are for tests and embedding; the
    server uses the plugin discovery, ``TWOTRACK_PROVIDER``, the real clock and
    ``TWOTRACK_EXPLORER``. ``TWOTRACK_EXPLORER=pi`` is checked here, before anything opens: a
    missing Node, worker or pinned install stops the start (``ExplorerUnavailable``). So is
    ``TWOTRACK_EXPLORE_TOOLS``, against the registry: a name that is not an explorable tool or
    workflow stops it too (``SettingsError``). So are the provider settings: a real provider
    without a model, an unknown provider, a malformed timeout or temperature (``ValueError``)."""
    if explorer is None and settings.explorer == "pi":
        explorer = pi_explorer(settings.pi_harness_dir, os.environ)
    registry = registry or load_registry()
    check_allowlist(registry, settings.explore_tools)
    model = provider or provider_from_env()
    clock = clock or _utc_now
    database, store = open_store(settings, registry)
    try:
        audit = SqliteAuditSink(database)
        grants = SqliteGrantStore(database, clock=clock)
        pipeline = Pipeline(registry, store, audit, grants, clock=clock)
        egress = EgressGuard(restricted_values_source(store, registry))
        importer = ImportService(registry, store, audit, grants, clock=clock,
                                 limits=UploadLimits(max_bytes=settings.max_upload_bytes))
        approvals = ApprovalStore(database, clock)
        engine = Engine(
            registry=registry, provider=model, scrubber=egress,
            pipeline=ApprovalRecordingPipeline(pipeline, approvals, grants),
            settings=GraphSettings(
                allow_open_task=settings.allow_open_task, max_turns=settings.explore_max_turns,
                max_tool_calls=settings.explore_max_calls,
                explore_timeout_seconds=settings.explore_timeout_seconds,
                explore_tool_timeout_seconds=settings.explore_tool_timeout_seconds,
                explore_startup_timeout_seconds=settings.explore_startup_timeout_seconds,
                explore_tools=settings.explore_tools),
            audit=audit, explorer=explorer)
        secret = load_or_create_secret(settings.secret_path)
        runtime = Runtime(
            settings=settings, database=database, registry=registry, store=store, audit=audit,
            grants=grants, pipeline=pipeline, importer=importer, egress=egress, provider=model,
            engine=engine, users=UserStore(database), approvals=approvals,
            tokens=TokenService(secret, settings.token_ttl_seconds, RevokedTokens(database)),
            clock=clock)
        if settings.demo_mode:
            seed_if_empty(store, registry)
    except BaseException:
        database.close()
        raise
    return runtime
