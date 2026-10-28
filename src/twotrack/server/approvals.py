"""Server-side memory of requests that wait for a one-time confirmation.

The core issues a grant (a bearer token) when a write or an import needs confirming. The
console must not hold that token, so the server keeps it here, next to what it authorizes:
the original request (target, params, route source, requester) or, for an import, the staged
file. The browser only ever sees the public ``grant_id``; approving it replays the stored
request with the stored token, as the stored requester.

Rows leave the table when they are claimed (approve/reject), expire (same 15 minutes as the
grant) or are pushed out: a user keeps their newest ``MAX_STAGED_PER_USER`` imports, whether
previewed (staged) or already submitted for approval (awaiting). The confirmations of questions
are not imports and not counted. The table lives in the database next to the audit log and
password hashes, inside the 0700 data directory.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from twotrack.core import Principal
from twotrack.core.canonical import jsonable
from twotrack.core.database import Database
from twotrack.core.grants import Grant, GrantStore
from twotrack.core.pipeline import PipelineOutcome, PipelineStatus

# imports a user keeps, staged or awaiting: each row holds its whole file, so the count (not the
# state) is what bounds the table; a newer import pushes the oldest one out
MAX_STAGED_PER_USER = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_request (
    grant_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    target TEXT NOT NULL,
    summary TEXT NOT NULL,
    params TEXT NOT NULL,
    route_source TEXT,
    query TEXT NOT NULL,
    entity_id TEXT,
    filename TEXT,
    digest TEXT,
    payload BLOB,
    token TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
)"""

Kind = Literal["query", "import"]
State = Literal["staged", "awaiting"]  # staged: previewed upload, not yet submitted for approval


class PendingRequest(BaseModel):
    grant_id: str
    user_id: str
    kind: Kind
    state: State
    target: str
    summary: str
    params: dict[str, Any]
    route_source: str | None
    query: str
    entity_id: str | None
    filename: str | None
    digest: str | None
    payload: bytes | None
    token: str  # bearer secret: never put this in a response model
    created_at: datetime
    expires_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalStore:
    def __init__(self, db: Database, clock: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self.clock = clock or _utc_now
        with db.transaction() as conn:
            conn.execute(_SCHEMA)

    def _now(self) -> float:
        return self.clock().timestamp()

    @staticmethod
    def _model(row: sqlite3.Row) -> PendingRequest:
        data = dict(row)
        data["params"] = json.loads(data["params"])
        for key in ("created_at", "expires_at"):
            data[key] = datetime.fromtimestamp(data[key], UTC)
        return PendingRequest.model_validate(data)

    def _insert(self, conn: sqlite3.Connection, principal: Principal, grant: Grant, *,
                kind: Kind, state: State, target: str, params: Mapping[str, Any],
                route_source: str | None, query: str, entity_id: str | None = None,
                filename: str | None = None, digest: str | None = None,
                payload: bytes | None = None) -> None:
        conn.execute("DELETE FROM pending_request WHERE expires_at < ?", (self._now(),))
        conn.execute(
            "INSERT INTO pending_request (grant_id, user_id, kind, state, target, summary,"
            " params, route_source, query, entity_id, filename, digest, payload, token,"
            " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (grant.grant_id, principal.user_id, kind, state, target, grant.summary,
             json.dumps(jsonable(dict(params))), route_source, query, entity_id, filename,
             digest, payload, grant.token, grant.created_at.timestamp(),
             grant.expires_at.timestamp()))

    # ------------------------------------------------------------ writes

    def add_query(self, principal: Principal, grant: Grant, *, target: str,
                  params: Mapping[str, Any], route_source: str | None, query: str) -> None:
        with self.db.transaction() as conn:
            self._insert(conn, principal, grant, kind="query", state="awaiting", target=target,
                         params=params, route_source=route_source, query=query)

    def add_staged_import(self, principal: Principal, grant: Grant, *, target: str,
                          entity_id: str, filename: str, digest: str, payload: bytes,
                          valid_count: int) -> list[str]:
        """Stage a previewed upload. Returns the grant tokens of older imports of the same user,
        staged or already submitted, that were pushed out to stay within the per-user cap (their
        grants are still open in the grant store; the caller rejects them).

        ``valid_count`` (the rows the import would write) is kept as the import's one param, so
        its summary can be worded again in another language later (``approval_views``)."""
        with self.db.transaction() as conn:
            self._insert(conn, principal, grant, kind="import", state="staged", target=target,
                         params={"valid_count": valid_count}, route_source="explicit", query="",
                         entity_id=entity_id, filename=filename, digest=digest, payload=payload)
            stale = conn.execute(
                "SELECT grant_id, token FROM pending_request WHERE user_id = ? AND"
                " kind = 'import' ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                (principal.user_id, MAX_STAGED_PER_USER)).fetchall()
            conn.executemany("DELETE FROM pending_request WHERE grant_id = ?",
                             [(r["grant_id"],) for r in stale])
        return [r["token"] for r in stale]

    def submit(self, grant_id: str, user_id: str) -> bool:
        """Staged -> awaiting: the user asked for the approval to appear in their list."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE pending_request SET state = 'awaiting' WHERE grant_id = ? AND"
                " user_id = ? AND state = 'staged' AND expires_at > ?",
                (grant_id, user_id, self._now()))
            return cursor.rowcount == 1

    def claim(self, grant_id: str, *, user_id: str | None,
              states: tuple[State, ...]) -> PendingRequest | None:
        """Read and delete in one transaction, so two concurrent approvals cannot both win.
        ``user_id=None`` skips the owner check (admin rejecting someone else's request)."""
        marks = ", ".join("?" * len(states))
        sql = f"SELECT * FROM pending_request WHERE grant_id = ? AND state IN ({marks})"
        args: list[Any] = [grant_id, *states]
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        with self.db.transaction() as conn:
            row = conn.execute(sql, args).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM pending_request WHERE grant_id = ?", (grant_id,))
        return self._model(row)

    # ------------------------------------------------------------ reads

    def get(self, grant_id: str) -> PendingRequest | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM pending_request WHERE grant_id = ?",
                               (grant_id,)).fetchone()
        return self._model(row) if row else None

    def find_staged(self, user_id: str, entity_id: str, digest: str,
                    grant_id: str | None = None) -> PendingRequest | None:
        sql = ("SELECT * FROM pending_request WHERE user_id = ? AND kind = 'import' AND"
               " entity_id = ? AND digest = ? AND expires_at > ?")
        args: list[Any] = [user_id, entity_id, digest, self._now()]
        if grant_id is not None:
            sql += " AND grant_id = ?"
            args.append(grant_id)
        with self.db.read() as conn:
            row = conn.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                               args).fetchone()
        return self._model(row) if row else None

    def awaiting(self, grant_ids: list[str]) -> dict[str, PendingRequest]:
        """The submitted requests among ``grant_ids`` (token and payload are not loaded)."""
        if not grant_ids:
            return {}
        marks = ", ".join("?" * len(grant_ids))
        with self.db.read() as conn:
            rows = conn.execute(
                f"SELECT grant_id, user_id, kind, state, target, summary, params, route_source,"
                f" query, entity_id, filename, digest, NULL AS payload, '' AS token, created_at,"
                f" expires_at FROM pending_request WHERE state = 'awaiting' AND"
                f" grant_id IN ({marks})", grant_ids).fetchall()
        return {r["grant_id"]: self._model(r) for r in rows}


class ApprovalRecordingPipeline:
    """Wraps the pipeline the engine runs: when a write stops at ``needs_confirmation``, the
    request is remembered under its grant id before anyone hears about the grant.

    If it cannot be remembered the grant is cancelled and the run reports an error; a
    confirmation nobody can act on is worse than a clear failure.
    """

    def __init__(self, inner: Any, approvals: ApprovalStore, grants: GrantStore) -> None:
        self.inner, self.approvals, self.grants = inner, approvals, grants

    def run(self, ctx: Any, target: str, params: Mapping[str, Any], **kwargs: Any
            ) -> PipelineOutcome:
        outcome = self.inner.run(ctx, target, params, **kwargs)
        if outcome.status is not PipelineStatus.NEEDS_CONFIRMATION or outcome.grant is None:
            return outcome
        source = kwargs.get("route_source")
        try:
            self.approvals.add_query(
                ctx.principal, outcome.grant, target=target, params=params,
                route_source=None if source is None else str(source),
                query=str(ctx.facts.get("query", "")))
        except sqlite3.Error:
            self.grants.reject(outcome.grant.token, ctx.principal)
            return outcome.model_copy(update={
                "status": PipelineStatus.ERROR, "grant": None,
                "problem": "the confirmation could not be recorded; nothing ran"})
        return outcome
