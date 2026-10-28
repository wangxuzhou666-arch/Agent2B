"""The storage protocol tools code against, plus the visibility projection for API output.

Both classes below define a method named ``list``. From that line on, the name means the method
inside the class body, and Python before 3.14 evaluates annotations when the ``def`` runs, so
the annotations that follow spell the builtin ``builtins.list``.
"""

import builtins
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ..contract import ENTITY_KEY, Principal, ResolvedEntity

Filters = Mapping[str, Any]  # field id -> value (equality), list/tuple (IN) or None (IS NULL)
Sort = str | Sequence[str]  # field id, '-' prefix for descending


class StoreError(Exception):
    def __init__(self, message: str, problems: Iterable[str] = ()) -> None:
        self.problems = list(problems)
        detail = "".join(f"\n  - {p}" for p in self.problems)
        super().__init__(f"{message}{detail}")


class SchemaMismatchError(StoreError):
    """Existing tables disagree with the registry. Raised at startup; the app must not start."""


class Page(BaseModel):
    rows: list[dict[str, Any]]
    total: int


@runtime_checkable
class Store(Protocol):
    def ensure_schema(self, registry: Any) -> None: ...

    def list(
        self,
        entity_id: str,
        *,
        filters: Filters | None = None,
        sort: Sort | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page: ...

    def get(self, entity_id: str, pk: Any) -> dict[str, Any] | None: ...

    def count(self, entity_id: str, *, filters: Filters | None = None) -> int: ...

    def upsert_many(self, entity_id: str, records: Iterable[Mapping[str, Any]], *,
                    merge: bool = False) -> int: ...

    def column_values(self, entity_id: str, field_id: str) -> builtins.list[Any]: ...

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
              ) -> builtins.list[dict]: ...

    def transaction(self) -> AbstractContextManager[Any]: ...


class ReadOnlyStore:
    """What a ``read``/``external`` tool receives: every read passes, every write raises.

    It bounds the handle the tool is given, not the tool. The write methods raise,
    ``transaction()`` is unavailable, and ``query()`` runs one SELECT under SQLite's authorizer
    (``SqliteStore.query``). A plugin is trusted in-process code, so a handler can still reach
    the database file, the environment and the network by other routes.
    """

    def __init__(self, inner: Store) -> None:
        self._inner = inner

    def ensure_schema(self, registry: Any) -> None:
        raise StoreError("read-only store: schema changes are not allowed here")

    def list(self, entity_id: str, **kwargs: Any) -> Page:
        return self._inner.list(entity_id, **kwargs)

    def get(self, entity_id: str, pk: Any) -> dict[str, Any] | None:
        return self._inner.get(entity_id, pk)

    def count(self, entity_id: str, *, filters: Filters | None = None) -> int:
        return self._inner.count(entity_id, filters=filters)

    def upsert_many(self, entity_id: str, records: Iterable[Mapping[str, Any]], *,
                    merge: bool = False) -> int:
        raise StoreError("read-only store: this tool is declared read/external and cannot write")

    def column_values(self, entity_id: str, field_id: str) -> builtins.list[Any]:
        return self._inner.column_values(entity_id, field_id)

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
              ) -> builtins.list[dict]:
        return self._inner.query(sql, params)

    def transaction(self) -> AbstractContextManager[Any]:
        raise StoreError("read-only store: transactions are not available to read tools")


def project_for(principal: Principal, entity: ResolvedEntity, row: Mapping[str, Any]) -> dict:
    """The part of ``row`` that ``principal`` may see. Undeclared keys are dropped too."""
    out = {f.id: row[f.id] for f in entity.visible_fields(principal) if f.id in row}
    if ENTITY_KEY in row:
        out[ENTITY_KEY] = row[ENTITY_KEY]
    return out


def project_rows(principal: Principal, registry: Any, rows: Iterable[Mapping[str, Any]],
                 entity_id: str) -> list[dict]:
    """Project a listing; rows of a child category are projected with that category's spec."""
    fallback = registry.entity(entity_id)
    out = []
    for row in rows:
        spec = registry.entities.get(row.get(ENTITY_KEY, ""), fallback)
        out.append(project_for(principal, spec, row))
    return out
