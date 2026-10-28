"""Audit log: every gated operation leaves a record, and a failed audit write fails the operation.

``AuditSink.record`` raises ``AuditError`` instead of swallowing errors. Callers treat that as
fatal: the pipeline does not execute a write whose intent record failed, rolls back a write
whose outcome record failed, and withholds a read result whose record failed.

Replay: the last record a query leaves holds the query's whole decision ladder (candidates,
understand, gate, execute, narrate, respond). The pipeline writes the steps it has; the engine
appends what happened after it (``append_trace``) and writes one record of its own for a query
that never reached the pipeline (``clarify`` / ``chat``). Records of one request share
``request_id``; an approved write's execution points at the record that asked for the
confirmation (``parent_id``).

Imports (target ``import:<entity>``, core.entities.importer) leave the same chain: the preview
record (``needs_confirmation``, or ``ok`` when no row is valid) and the commit's ``started`` and
``ok`` (or ``error``) records share the preview's ``request_id``, and the commit's records point
at the preview record (``parent_id``). Their traces use these steps:

* ``parse``: file checks and parsing (facts ``file_type``, ``encoding``, ``rows``; a refused
  file: blocked, ``code``);
* ``validate``: header mapping and row checks (``columns``, ``unmapped``, ``valid``,
  ``errors``; a refused mapping: blocked, ``code``);
* ``gate``: ``stage`` ``authz`` (the role may not import, blocked), ``digest`` (the file changed
  since the preview, blocked) or ``confirmation`` (a grant issued at preview, blocked with
  ``grant_id``; consumed at commit, ok);
* ``execute``: the write (``written``, ``skipped``; or error with ``error`` and
  ``rolled_back``).

An approval-side refusal (someone other than the requester approves, an approval of a
confirmation that already ran, expired or was never submitted, or a rejection) is a record of
its own in the request's chain, with one ``gate`` step (``stage`` ``approval``), the refusal's
code as ``problem`` and no route source. A refusal further on (the pipeline's or an import
commit's, before the grant is consumed) names the grant it was given as well, so a grant's
records (``grant_id``) always show why nothing ran. Filters and statistics name a missing route
source ``none`` (``NO_ROUTE_SOURCE``).
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, JsonValue

from .contract import TraceStep
from .database import Database

NO_ROUTE_SOURCE = "none"  # how a filter or a statistic names "no route source recorded"


class AuditError(Exception):
    """The audit record could not be written. The operation must not proceed."""


class AuditStatus(StrEnum):
    STARTED = "started"  # intent record, written before an approved run (write or external)
    OK = "ok"
    NEEDS_CONFIRMATION = "needs_confirmation"
    BLOCKED = "blocked"
    INVALID = "invalid"
    ERROR = "error"
    CLARIFY = "clarify"  # routing asked the user to choose; nothing ran
    CHAT = "chat"  # answered without running a target (chat fallback, or no plan)


class AuditEntry(BaseModel):
    id: int | None = None
    ts: datetime
    user_id: str
    roles: list[str] = []
    target: str | None = None  # None: the query never named one target (clarify, chat)
    effect: str | None = None
    status: AuditStatus
    route_source: str | None = None
    params_digest: str | None = None
    params: dict[str, JsonValue] = {}  # restricted-field values already masked by the caller
    row_count: int | None = None
    grant_id: str | None = None
    problem: str | None = None
    trace: list[TraceStep] = []
    request_id: str | None = None  # shared by every record of one user request
    parent_id: int | None = None  # the record this one continues (an approval's request)


class AuditQuery(BaseModel):
    user_id: str | None = None
    target: str | None = None
    status: AuditStatus | None = None
    route_source: str | None = None  # NO_ROUTE_SOURCE: records written without one
    since: datetime | None = None
    until: datetime | None = None
    request_id: str | None = None
    grant_id: str | None = None


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int


@runtime_checkable
class AuditSink(Protocol):
    def record(self, entry: AuditEntry) -> int: ...

    def list(self, filters: AuditQuery | None = None, limit: int = 50,
             offset: int = 0) -> AuditPage: ...

    def get(self, entry_id: int) -> AuditEntry | None: ...

    def append_trace(self, entry_id: int, steps: Sequence[TraceStep]) -> None:
        """Add steps to the end of an existing record's trace. Raises AuditError."""
        ...


_COLUMNS = ("ts", "user_id", "roles", "target", "effect", "status", "route_source",
            "params_digest", "params", "row_count", "grant_id", "problem", "trace",
            "request_id", "parent_id")
_INTEGER_COLUMNS = frozenset({"row_count", "parent_id"})


def _column_type(column: str) -> str:
    return "INTEGER" if column in _INTEGER_COLUMNS else "TEXT"


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {", ".join(f"{c} {_column_type(c)}" for c in _COLUMNS)}
)"""


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class SqliteAuditSink:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db.transaction() as conn:
            conn.execute(_SCHEMA)
            # a log created by an older version lacks the newer columns: add them in place
            present = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)")}
            for column in _COLUMNS:
                if column not in present:
                    conn.execute(f"ALTER TABLE audit_log ADD COLUMN {column} "
                                 f"{_column_type(column)}")
            conn.execute("CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS audit_log_request "
                         "ON audit_log(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS audit_log_grant ON audit_log(grant_id)")

    def record(self, entry: AuditEntry) -> int:
        values = entry.model_dump(mode="json")
        row = [_ts(entry.ts), entry.user_id, json.dumps(values["roles"]), entry.target,
               entry.effect, entry.status.value, entry.route_source, entry.params_digest,
               json.dumps(values["params"], ensure_ascii=False), entry.row_count,
               entry.grant_id, entry.problem, json.dumps(values["trace"], ensure_ascii=False),
               entry.request_id, entry.parent_id]
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    f"INSERT INTO audit_log ({', '.join(_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_COLUMNS))})", row)
                return int(cursor.lastrowid)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise AuditError(f"audit write failed: {type(exc).__name__}: {exc}") from exc

    def append_trace(self, entry_id: int, steps: Sequence[TraceStep]) -> None:
        """Extend a record's trace (read and write in one transaction). Earlier steps and every
        other column stay as they were written."""
        if not steps:
            return
        try:
            added = [TraceStep.model_validate(s).model_dump(mode="json") for s in steps]
            with self.db.transaction() as conn:
                row = conn.execute("SELECT trace FROM audit_log WHERE id = ?",
                                   (entry_id,)).fetchone()
                if row is None:
                    raise AuditError(f"audit entry {entry_id} does not exist")
                trace = json.loads(row["trace"] or "[]") or []
                conn.execute("UPDATE audit_log SET trace = ? WHERE id = ?",
                             (json.dumps([*trace, *added], ensure_ascii=False), entry_id))
        except AuditError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise AuditError(f"audit append failed: {type(exc).__name__}: {exc}") from exc

    def list(self, filters: AuditQuery | None = None, limit: int = 50,
             offset: int = 0) -> AuditPage:
        clauses, args = [], []
        f = filters or AuditQuery()
        for column in ("user_id", "target", "route_source", "request_id", "grant_id"):
            if column == "route_source" and f.route_source == NO_ROUTE_SOURCE:
                clauses.append("COALESCE(route_source, '') = ''")  # none recorded
            elif getattr(f, column) is not None:
                clauses.append(f"{column} = ?")
                args.append(getattr(f, column))
        if f.status is not None:
            clauses.append("status = ?")
            args.append(f.status.value)
        if f.since is not None:
            clauses.append("ts >= ?")
            args.append(_ts(f.since))
        if f.until is not None:
            clauses.append("ts < ?")
            args.append(_ts(f.until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit, offset = max(1, min(int(limit), 500)), max(0, int(offset))
        with self.db.read() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", args).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM audit_log {where} ORDER BY id DESC "
                                "LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return AuditPage(items=[self._entry(r) for r in rows], total=total)

    def get(self, entry_id: int) -> AuditEntry | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,)).fetchone()
        return self._entry(row) if row else None

    @staticmethod
    def _entry(row: sqlite3.Row) -> AuditEntry:
        data: dict[str, Any] = dict(row)
        for key in ("roles", "params", "trace"):
            data[key] = json.loads(data[key] or "null") or ([] if key != "params" else {})
        return AuditEntry.model_validate(data)


class AuditRecorder:
    """Builds entries for one principal/target with shared fields; used by pipeline and import."""

    def __init__(self, sink: AuditSink, clock: Callable[[], datetime], *, user_id: str,
                 roles: list[str], target: str, effect: str | None,
                 route_source: str | None) -> None:
        self.sink, self.clock = sink, clock
        self.base = dict(user_id=user_id, roles=roles, target=target, effect=effect,
                         route_source=route_source)

    def __call__(self, status: AuditStatus, **fields: Any) -> int:
        """Write one entry. Raises AuditError; never returns without a stored record."""
        try:
            entry = AuditEntry(ts=self.clock(), status=status, **self.base, **fields)
            return self.sink.record(entry)
        except AuditError:
            raise
        except Exception as exc:  # a broken sink counts as a failed audit: fail closed
            raise AuditError(f"audit write failed: {type(exc).__name__}: {exc}") from exc
