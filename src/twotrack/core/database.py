"""One SQLite connection shared by the store, the audit log and the grant store.

Sharing the connection is what makes audit fail-closed cheap: a write tool's data change and
its outcome audit record commit in the same transaction, so a failed audit write rolls the
data change back with it. Every statement runs under one re-entrant lock; ``transaction()``
nests with savepoints, so code that already holds a transaction can call helpers that open
their own.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MEMORY = ":memory:"


class Database:
    def __init__(self, path: str | Path = MEMORY) -> None:
        self.path = str(path)
        # isolation_level=None: no implicit transactions; transaction() issues BEGIN itself.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != MEMORY:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on any exception. Nested calls use savepoints."""
        with self._lock:
            depth = self._depth
            savepoint = f"sp{depth}"
            self._conn.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO {savepoint}")
                    self._conn.execute(f"RELEASE {savepoint}")
                raise
            self._depth -= 1
            if depth > 0:
                self._conn.execute(f"RELEASE {savepoint}")
                return
            try:
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Run reads under the lock (no transaction of its own)."""
        with self._lock:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
