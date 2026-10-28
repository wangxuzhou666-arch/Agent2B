"""One-time confirmation grants for write operations.

A grant is bound to a digest of (user, target, validated params), lives 15 minutes and can be
consumed once. ``consume`` is a single conditional UPDATE, so two concurrent attempts cannot
both succeed. A consumed grant stays consumed even if the execution that follows fails: the
user confirms again rather than leaving a path that "un-uses" a grant.

Only a SHA-256 of the token is stored; the token itself exists only in the issue response.
``find`` tells, by public id, what became of a grant (used, rejected, expired or still open)
until ``issue`` purges it some time after it expired.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from .canonical import request_digest, sha256_hex
from .contract import Principal, Role
from .database import Database

GRANT_TTL = timedelta(minutes=15)

GrantState = Literal["open", "used", "rejected", "expired"]


def grant_id_for(token: str) -> str:
    """Public id of a grant (safe for audit logs): a prefix of the token's hash."""
    return sha256_hex(token)[:16]


class GrantInfo(BaseModel):
    grant_id: str
    user_id: str
    target: str
    summary: str
    created_at: datetime
    expires_at: datetime


class Grant(GrantInfo):
    token: str  # bearer secret; returned once, at issue time


class GrantRecord(GrantInfo):
    """A grant as the store remembers it, with what became of it."""

    state: GrantState


@runtime_checkable
class GrantStore(Protocol):
    def issue(self, principal: Principal, target: str, params: Any, summary: str) -> Grant: ...

    def consume(self, token: str, principal: Principal, target: str, params: Any) -> bool: ...

    def pending(self, principal: Principal | None = None) -> list[GrantInfo]: ...

    def reject(self, token: str, principal: Principal) -> bool: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmation_grant (
    token_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    target TEXT NOT NULL,
    digest TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL,
    rejected_at REAL
)"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


class SqliteGrantStore:
    def __init__(self, db: Database, *, ttl: timedelta = GRANT_TTL,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.db, self.ttl, self.clock = db, ttl, clock or _utc_now
        with db.transaction() as conn:
            conn.execute(_SCHEMA)

    def _now(self) -> float:
        return self.clock().timestamp()

    def issue(self, principal: Principal, target: str, params: Any, summary: str) -> Grant:
        token = secrets.token_urlsafe(32)
        now = self._now()
        expires = now + self.ttl.total_seconds()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM confirmation_grant WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO confirmation_grant (token_hash, grant_id, user_id, target, digest,"
                " summary, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sha256_hex(token), grant_id_for(token), principal.user_id, target,
                 request_digest(principal.user_id, target, params), summary, now, expires))
        return Grant(token=token, grant_id=grant_id_for(token), user_id=principal.user_id,
                     target=target, summary=summary, created_at=_dt(now),
                     expires_at=_dt(expires))

    def consume(self, token: str, principal: Principal, target: str, params: Any) -> bool:
        """True exactly once for the owner, the same target and identical params, before expiry."""
        if not token:
            return False
        now = self._now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE confirmation_grant SET used_at = ? WHERE token_hash = ? AND user_id = ?"
                " AND target = ? AND digest = ? AND used_at IS NULL AND rejected_at IS NULL"
                " AND expires_at > ?",
                (now, sha256_hex(token), principal.user_id, target,
                 request_digest(principal.user_id, target, params), now))
            return cursor.rowcount == 1

    def pending(self, principal: Principal | None = None) -> list[GrantInfo]:
        """Open grants: a user sees their own, an admin (or ``None``) sees all."""
        sql = ("SELECT * FROM confirmation_grant WHERE used_at IS NULL AND rejected_at IS NULL"
               " AND expires_at > ?")
        args: list[Any] = [self._now()]
        if principal is not None and Role.ADMIN not in principal.roles:
            sql += " AND user_id = ?"
            args.append(principal.user_id)
        with self.db.read() as conn:
            rows = conn.execute(sql + " ORDER BY created_at", args).fetchall()
        return [GrantInfo(grant_id=r["grant_id"], user_id=r["user_id"], target=r["target"],
                          summary=r["summary"], created_at=_dt(r["created_at"]),
                          expires_at=_dt(r["expires_at"])) for r in rows]

    def find(self, grant_id: str) -> GrantRecord | None:
        """The grant with this public id and what became of it: ``used`` once consumed (even
        if what followed failed); ``rejected`` when it was cancelled while still valid;
        ``expired`` when its time ran out unused, also when it was only closed after that (an
        approval that came too late closes it); else ``open``. None when the store does not know
        the id: never issued here, or purged since it expired (``issue`` purges)."""
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM confirmation_grant WHERE grant_id = ?"
                               " ORDER BY created_at DESC LIMIT 1", (grant_id,)).fetchone()
        if row is None:
            return None
        state: GrantState
        if row["used_at"] is not None:
            state = "used"
        elif row["rejected_at"] is not None and row["rejected_at"] < row["expires_at"]:
            state = "rejected"
        elif row["expires_at"] <= self._now():
            state = "expired"
        else:
            state = "open"
        return GrantRecord(grant_id=row["grant_id"], user_id=row["user_id"],
                           target=row["target"], summary=row["summary"],
                           created_at=_dt(row["created_at"]), expires_at=_dt(row["expires_at"]),
                           state=state)

    def reject(self, token: str, principal: Principal) -> bool:
        """Cancel an open grant. Owners reject their own; an admin may reject any."""
        sql = ("UPDATE confirmation_grant SET rejected_at = ? WHERE token_hash = ?"
               " AND used_at IS NULL AND rejected_at IS NULL")
        args: list[Any] = [self._now(), sha256_hex(token)]
        if Role.ADMIN not in principal.roles:
            sql += " AND user_id = ?"
            args.append(principal.user_id)
        with self.db.transaction() as conn:
            return conn.execute(sql, args).rowcount == 1
