"""Users, password hashing, tokens and brute-force throttling. Pure: no FastAPI here.

* Passwords: stdlib scrypt with a random per-user salt, compared in constant time. An unknown
  user costs the same hashing work as a wrong password, so response time does not reveal
  which usernames exist.
* Tokens: HS256 JWTs signed with the secret from the data directory, short-lived. Only the
  algorithm we sign with is accepted on verify. A token names a user; roles are read from the
  users table on every request, so changing or disabling a user takes effect immediately.
  Every token carries a random id (``jti``); signing out puts that id on a deny-list in the
  database (``RevokedTokens``), which verify consults, until the token would have expired.
* Nothing ships with a default account. Demo mode adds built-in passwordless users that exist
  only in code and are accepted only while demo mode is on.
"""

import base64
import functools
import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel

from twotrack.core import ROLES, Principal, Role
from twotrack.core.database import Database

SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_BYTES, SALT_BYTES = 2**14, 8, 1, 32, 16
SCRYPT_MAXMEM = 64 * 1024 * 1024
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,39}$")
DEMO_PREFIX = "demo-"  # reserved: real users cannot take a demo user's name
MIN_PASSWORD_LENGTH = 10
JWT_ALGORITHM = "HS256"

TOKEN_ID_BYTES = 16

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
    user_id TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    roles TEXT NOT NULL,
    created_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
)"""

_REVOKED_SCHEMA = """
CREATE TABLE IF NOT EXISTS revoked_token (
    token_id TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
)"""


# ---------------------------------------------------------------- passwords


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES,
                          maxmem=SCRYPT_MAXMEM)


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """False for a wrong password and for a stored value we cannot parse."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _unb64(parts[4]), _unb64(parts[5])
    except ValueError:
        return False
    if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
        return False  # parameters come from our own hashes; refuse to run attacker-picked cost
    return hmac.compare_digest(_scrypt(password, salt, n, r, p), expected)


@functools.cache
def _decoy_hash() -> str:
    return hash_password("decoy-for-unknown-users")


# ---------------------------------------------------------------- users


class UserExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class UserRecord(BaseModel):
    user_id: str
    roles: tuple[Role, ...]
    created_at: datetime
    disabled: bool

    @property
    def principal(self) -> Principal:
        return Principal(user_id=self.user_id, roles=frozenset(self.roles))


def check_username(name: str) -> None:
    if name.startswith(DEMO_PREFIX):
        raise ValueError(f"usernames starting with '{DEMO_PREFIX}' are reserved for demo mode")
    if not USERNAME_RE.match(name):
        raise ValueError("username must be 2-40 characters: lowercase letters, digits, "
                         "'_', '.', '-', starting with a letter")


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise ValueError("password must be at most 256 characters")


def check_roles(roles: Iterable[str]) -> list[str]:
    """The roles without repeats, sorted; ValueError unless they are a non-empty subset of
    ``ROLES``."""
    chosen = sorted(set(roles))
    unknown = [r for r in chosen if r not in ROLES]
    if unknown or not chosen:
        raise ValueError(f"roles must be a non-empty subset of {sorted(ROLES)}")
    return chosen


class UserStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db.transaction() as conn:
            conn.execute(_SCHEMA)

    @staticmethod
    def _record(row: sqlite3.Row) -> UserRecord:
        return UserRecord(user_id=row["user_id"], roles=tuple(row["roles"].split(",")),
                          created_at=datetime.fromisoformat(row["created_at"]),
                          disabled=bool(row["disabled"]))

    def add(self, user_id: str, password: str, roles: Iterable[str]) -> UserRecord:
        check_username(user_id)
        check_password(password)
        chosen = check_roles(roles)
        stored = hash_password(password)  # slow on purpose; outside the database lock
        created = datetime.now(UTC)
        try:
            with self.db.transaction() as conn:
                conn.execute("INSERT INTO app_user (user_id, password_hash, roles, created_at)"
                             " VALUES (?, ?, ?, ?)",
                             (user_id, stored, ",".join(chosen), created.isoformat()))
        except sqlite3.IntegrityError:
            raise UserExistsError(f"user '{user_id}' already exists") from None
        return UserRecord(user_id=user_id, roles=tuple(Role(r) for r in chosen),
                          created_at=created, disabled=False)

    def get(self, user_id: str) -> UserRecord | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM app_user WHERE user_id = ?", (user_id,)).fetchone()
        return self._record(row) if row else None

    def list(self) -> list[UserRecord]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM app_user ORDER BY user_id").fetchall()
        return [self._record(r) for r in rows]

    def _change(self, user_id: str, statement: str, value: str | int) -> UserRecord:
        """Run ``statement`` (an UPDATE of one column, given the value and then the user id) and
        return the user as it is now. The server reads this table on every request, so a change
        shows on the next one: tokens name a user and carry no roles and no flag."""
        with self.db.transaction() as conn:
            conn.execute(statement, (value, user_id))
            row = conn.execute("SELECT * FROM app_user WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise UserNotFoundError(f"no user '{user_id}'")
        return self._record(row)

    def set_disabled(self, user_id: str, disabled: bool) -> UserRecord:
        """Turn an account off or back on. Sign-in and every token already issued to it are
        refused while it is off; no token is revoked, so the unexpired ones work again once it
        is on."""
        return self._change(user_id, "UPDATE app_user SET disabled = ? WHERE user_id = ?",
                            int(disabled))

    def set_roles(self, user_id: str, roles: Iterable[str]) -> UserRecord:
        """Replace the user's roles with exactly these."""
        return self._change(user_id, "UPDATE app_user SET roles = ? WHERE user_id = ?",
                            ",".join(check_roles(roles)))

    def set_password(self, user_id: str, password: str) -> None:
        """Replace the password. Tokens issued before the change stay valid until they expire."""
        check_password(password)
        stored = hash_password(password)  # slow on purpose; outside the database lock
        self._change(user_id, "UPDATE app_user SET password_hash = ? WHERE user_id = ?", stored)

    def authenticate(self, user_id: str, password: str) -> UserRecord | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM app_user WHERE user_id = ?", (user_id,)).fetchone()
        stored = row["password_hash"] if row else _decoy_hash()
        matches = verify_password(password, stored)
        if row is None or not matches or row["disabled"]:
            return None
        return self._record(row)


# ---------------------------------------------------------------- demo users


def demo_user_id(role: Role) -> str:
    return f"{DEMO_PREFIX}{role.value}"


DEMO_USERS: dict[str, Principal] = {
    demo_user_id(role): Principal(user_id=demo_user_id(role), roles=frozenset({role}))
    for role in Role
}


# ---------------------------------------------------------------- tokens


class TokenError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Claims:
    user_id: str
    demo: bool
    expires_at: datetime
    token_id: str  # the token's ``jti``: what signing out puts on the deny-list


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RevokedTokens:
    """Deny-list of tokens that were signed out: their ids, each with the moment the token
    would have expired anyway.

    Kept in the database next to the users, so a sign-out survives a restart. A token that has
    expired is refused by its own expiry, so its entry has nothing left to do: entries are
    pruned when a token is revoked and when the service starts, which bounds the table by the
    sign-outs of one token lifetime. ``revoke`` is idempotent."""

    def __init__(self, db: Database, clock: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self.clock = clock or _utc_now
        with db.transaction() as conn:
            conn.execute(_REVOKED_SCHEMA)
            self._prune(conn)

    def _prune(self, conn: sqlite3.Connection) -> int:
        return conn.execute("DELETE FROM revoked_token WHERE expires_at <= ?",
                            (self.clock().timestamp(),)).rowcount

    def prune(self) -> int:
        """Drop the entries of tokens that have expired by now; returns how many."""
        with self.db.transaction() as conn:
            return self._prune(conn)

    def revoke(self, token_id: str, expires_at: datetime) -> None:
        with self.db.transaction() as conn:
            self._prune(conn)
            conn.execute("INSERT OR IGNORE INTO revoked_token (token_id, expires_at)"
                         " VALUES (?, ?)", (token_id, expires_at.timestamp()))

    def is_revoked(self, token_id: str) -> bool:
        with self.db.read() as conn:
            found = conn.execute("SELECT 1 FROM revoked_token WHERE token_id = ?",
                                 (token_id,)).fetchone()
        return found is not None


class TokenService:
    def __init__(self, secret: str, ttl_seconds: int, revoked: RevokedTokens) -> None:
        self._secret = secret
        self.ttl_seconds = ttl_seconds
        self.revoked = revoked

    def issue(self, user_id: str, *, demo: bool = False,
              ttl_seconds: int | None = None) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self.ttl_seconds if ttl_seconds is None
                                  else ttl_seconds)
        claims: dict[str, str | int | bool] = {
            "sub": user_id, "iat": int(now.timestamp()), "exp": int(expires.timestamp()),
            "jti": secrets.token_hex(TOKEN_ID_BYTES)}
        if demo:
            claims["demo"] = True
        return jwt.encode(claims, self._secret, algorithm=JWT_ALGORITHM), expires

    def verify(self, token: str) -> Claims:
        """The claims of a token we signed that has neither expired nor been signed out. A token
        without an id is refused: it could never be signed out."""
        try:
            claims = jwt.decode(token, self._secret, algorithms=[JWT_ALGORITHM],
                                options={"require": ["exp", "iat", "sub", "jti"]})
        except jwt.ExpiredSignatureError:
            raise TokenError("token_expired", "The session has expired; sign in again.") from None
        except jwt.InvalidTokenError:
            raise TokenError("invalid_token", "The token is not valid.") from None
        if self.revoked.is_revoked(claims["jti"]):
            raise TokenError("token_revoked", "The session was signed out; sign in again.")
        return Claims(user_id=claims["sub"], demo=claims.get("demo") is True,
                      expires_at=datetime.fromtimestamp(claims["exp"], UTC),
                      token_id=claims["jti"])

    def revoke(self, token_id: str, expires_at: datetime) -> None:
        """Sign out: refuse the token with this id (``Claims.token_id``) until ``expires_at``,
        when it would have expired anyway."""
        self.revoked.revoke(token_id, expires_at)


# ---------------------------------------------------------------- throttling


class LoginThrottle:
    """Refuse a (client, username) pair after too many failures in a window.

    ``reserve`` is how a sign-in meets it: the limit is checked and the attempt booked in one
    step under the lock, before the password is hashed. A booked attempt counts as a failure
    from that moment, and ``clear`` erases it when the password turns out right. Checking first
    and recording afterwards would let every request of a parallel burst pass the check before
    the first of them failed, and the whole burst would be hashed instead of 5 of it. An attempt
    that dies in the server before it is decided stays booked too (fail closed).

    In-process memory only: it slows online guessing against one server, which is its whole
    job. The table is bounded, so inventing usernames cannot grow it, and what it forgets when
    full is chosen so that inventing usernames cannot unlock anyone: an expired key goes first,
    then the unlocked key with the fewest failures (the least recently used among equals), and a
    locked key never. If the table is full of live locks, a key it has no room for is refused as
    if it were locked, until a lock expires. That state takes ``max_keys * max_failures`` slow
    password checks inside one window; refusing new keys then is the cheaper failure than
    evicting a lock, which would hand that effort a way to undo it. Requests run in worker
    threads, hence the lock.
    """

    def __init__(self, *, max_failures: int = 5, window_seconds: float = 300.0,
                 max_keys: int = 10_000, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures, self.window, self.max_keys, self.clock = (
            max_failures, window_seconds, max_keys, clock)
        # least recently booked first; each key holds the times of its live attempts
        self._failures: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> list[float]:
        # written as t + window > now so that "live" and "has seconds left to wait" cannot
        # disagree by a rounding error
        return [t for t in self._failures.get(key, ()) if t + self.window > now]

    def reserve(self, key: str) -> float:
        """Book one attempt for ``key``: 0.0 when it is booked, else the seconds until an
        attempt is allowed (nothing is booked then)."""
        with self._lock:
            now = self.clock()
            recent = self._live(key, now)
            if len(recent) >= self.max_failures:
                return recent[0] + self.window - now
            if key not in self._failures and len(self._failures) >= self.max_keys:
                wait = self._make_room(now)
                if wait > 0:
                    return wait
            recent.append(now)
            self._failures[key] = recent
            self._failures.move_to_end(key)
            return 0.0

    def _make_room(self, now: float) -> float:
        """Forget one key to free a slot: 0.0 when that worked, else the seconds until a lock
        expires and frees one."""
        victim, fewest, unlocks_in = None, self.max_failures, math.inf
        for key in self._failures:
            live = self._live(key, now)
            if len(live) >= self.max_failures:
                unlocks_in = min(unlocks_in, live[0] + self.window - now)
            elif len(live) < fewest:
                victim, fewest = key, len(live)
                if fewest <= 1:  # expired keys come first in this order, so none is cheaper
                    break
        if victim is None:
            return unlocks_in
        del self._failures[victim]
        return 0.0

    def clear(self, key: str) -> None:
        """The password was right: forget this key's booked attempts."""
        with self._lock:
            self._failures.pop(key, None)
