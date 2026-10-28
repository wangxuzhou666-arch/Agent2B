"""SQLite implementation of the Store protocol.

Storage model: each root entity (no ``extends``) owns one table ``e_<root id>`` with a typed
column per core field, ``_entity`` (which entity in the family a row belongs to) and ``_attrs``
(JSON of the row's category attrs). A child category's rows live in its root's table, so
adding a category never adds a column. ``ensure_schema`` fails closed: any difference between
an existing table and the registry raises SchemaMismatchError at startup.

``SqliteStore`` defines a method named ``list``; from that line on the name means the method
inside the class body, and Python before 3.14 evaluates annotations when the ``def`` runs, so
the annotations that follow spell the builtin ``builtins.list``.
"""

import builtins
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any

from ..contract import FieldSpec, FieldType, ResolvedEntity
from ..database import Database
from ..entities.values import coerce_value, validate_record
from .base import ENTITY_KEY, Filters, Page, SchemaMismatchError, Sort, StoreError

TABLE_PREFIX = "e_"
ATTRS_COLUMN = "_attrs"
MAX_PAGE = 1000
_SQL_TYPES = {
    FieldType.STR: "TEXT",
    FieldType.INT: "INTEGER",
    FieldType.FLOAT: "REAL",
    FieldType.BOOL: "INTEGER",
    FieldType.DATE: "TEXT",
    FieldType.ENUM: "TEXT",
}
_SELECT_START = re.compile(r"^(select|with)\b", re.IGNORECASE)
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def table_name(root_id: str) -> str:
    return f"{TABLE_PREFIX}{root_id}"


def _q(identifier: str) -> str:
    return f'"{identifier}"'  # ids are validated against ^[a-z][a-z0-9_]*$ by the contract


def _to_sql(value: Any) -> Any:
    return int(value) if isinstance(value, bool) else value


def _read_only_authorizer(action: int, arg1: str | None, *_: Any) -> int:
    """Plugin queries may SELECT from entity tables only: no writes, pragmas or system tables."""
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION,
                  getattr(sqlite3, "SQLITE_RECURSIVE", 33)):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and arg1 and arg1.startswith(TABLE_PREFIX):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class SqliteStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._registry: Any = None
        self._family: dict[str, tuple[str, ...]] = {}  # entity id -> itself + descendants

    # ------------------------------------------------------------ schema

    def ensure_schema(self, registry: Any) -> None:
        entities: Mapping[str, ResolvedEntity] = registry.entities
        family = {eid: tuple(e.id for e in entities.values() if eid in e.lineage)
                  for eid in entities}
        problems: list[str] = []
        with self.db.transaction() as conn:
            for root in (e for e in entities.values() if e.extends is None):
                table = table_name(root.id)
                existing = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
                if not existing:
                    conn.execute(self._create_sql(root))
                    conn.execute(f"CREATE INDEX {_q(table + '__entity')} "
                                 f"ON {_q(table)}({_q(ENTITY_KEY)})")
                    continue
                found = self._column_problems(root, existing)
                problems.extend(found)
                if not found:
                    stored = {r[0] for r in conn.execute(
                        f"SELECT DISTINCT {_q(ENTITY_KEY)} FROM {_q(table)}")}
                    unknown = sorted(stored - set(family[root.id]))
                    if unknown:
                        problems.append(f"table '{table}' holds rows of undeclared entities "
                                        f"{unknown}")
            if problems:
                raise SchemaMismatchError("storage schema does not match the registry; "
                                          "migrate the database before starting", problems)
        self._registry = registry
        self._family = family

    @staticmethod
    def _expected_columns(root: ResolvedEntity) -> dict[str, tuple[str, int]]:
        cols = {f.id: (_SQL_TYPES[f.type], int(f.id == root.primary_key)) for f in root.fields}
        cols[ENTITY_KEY] = ("TEXT", 0)
        cols[ATTRS_COLUMN] = ("TEXT", 0)
        return cols

    def _create_sql(self, root: ResolvedEntity) -> str:
        parts = []
        for name, (sql_type, is_pk) in self._expected_columns(root).items():
            suffix = " PRIMARY KEY NOT NULL" if is_pk else ""
            if name == ENTITY_KEY:
                suffix = " NOT NULL"
            elif name == ATTRS_COLUMN:
                suffix = " NOT NULL DEFAULT '{}'"
            parts.append(f"{_q(name)} {sql_type}{suffix}")
        return f"CREATE TABLE {_q(table_name(root.id))} ({', '.join(parts)})"

    def _column_problems(self, root: ResolvedEntity, existing: list[sqlite3.Row]) -> list[str]:
        table = table_name(root.id)
        expected = self._expected_columns(root)
        actual = {r["name"]: (r["type"].upper(), int(r["pk"] > 0)) for r in existing}
        out = [f"table '{table}' is missing column '{c}'" for c in expected if c not in actual]
        out += [f"table '{table}' has column '{c}' that the registry does not declare"
                for c in actual if c not in expected]
        out += [f"table '{table}' column '{c}' is {actual[c][0]}"
                f"{' (primary key)' if actual[c][1] else ''}, registry expects {want[0]}"
                f"{' (primary key)' if want[1] else ''}"
                for c, want in expected.items() if c in actual and actual[c] != want]
        return out

    # ------------------------------------------------------------ helpers

    def _entity(self, entity_id: str) -> ResolvedEntity:
        if self._registry is None:
            raise StoreError("store schema not ensured; call ensure_schema(registry) at startup")
        try:
            return self._registry.entity(entity_id)
        except KeyError as exc:
            raise StoreError(exc.args[0]) from None

    def _table(self, entity: ResolvedEntity) -> str:
        return _q(table_name(entity.lineage[0]))

    def _column(self, entity: ResolvedEntity, field_id: str) -> tuple[str, FieldSpec]:
        try:
            spec = entity.field(field_id)
        except KeyError as exc:
            raise StoreError(exc.args[0]) from None
        if any(f.id == field_id for f in entity.fields):
            return _q(field_id), spec
        return f"json_extract({_q(ATTRS_COLUMN)}, '$.{field_id}')", spec

    def _where(self, entity: ResolvedEntity, filters: Filters | None) -> tuple[str, list]:
        family = self._family[entity.id]
        clauses = [f"{_q(ENTITY_KEY)} IN ({', '.join('?' * len(family))})"]
        args: list[Any] = list(family)
        for field_id, wanted in (filters or {}).items():
            column, spec = self._column(entity, field_id)
            try:
                if wanted is None:
                    clauses.append(f"{column} IS NULL")
                elif isinstance(wanted, list | tuple | set | frozenset):
                    values = [_to_sql(coerce_value(spec, v)) for v in wanted]
                    clauses.append(f"{column} IN ({', '.join('?' * len(values)) or 'NULL'})")
                    args.extend(values)
                else:
                    clauses.append(f"{column} = ?")
                    args.append(_to_sql(coerce_value(spec, wanted)))
            except ValueError as exc:
                raise StoreError(f"filter on '{field_id}': {exc}") from None
        return " AND ".join(clauses), args

    def _order(self, entity: ResolvedEntity, sort: Sort | None) -> str:
        keys = [sort] if isinstance(sort, str) else list(sort or [])
        parts = []
        for key in keys:
            column, _ = self._column(entity, key.lstrip("-"))
            parts.append(f"{column} {'DESC' if key.startswith('-') else 'ASC'}")
        parts.append(f"{_q(entity.primary_key)} ASC")  # stable paging
        return ", ".join(parts)

    def _row(self, entity: ResolvedEntity, row: sqlite3.Row) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for spec in entity.fields:
            value = row[spec.id]
            out[spec.id] = bool(value) if spec.type is FieldType.BOOL and value is not None \
                else value
        out.update(json.loads(row[ATTRS_COLUMN] or "{}"))
        out[ENTITY_KEY] = row[ENTITY_KEY]
        return out

    # ------------------------------------------------------------ reads

    def list(
        self,
        entity_id: str,
        *,
        filters: Filters | None = None,
        sort: Sort | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page:
        entity = self._entity(entity_id)
        where, args = self._where(entity, filters)
        limit, offset = max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))
        table = self._table(entity)
        with self.db.read() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {where} ORDER BY {self._order(entity, sort)} "
                "LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return Page(rows=[self._row(entity, r) for r in rows], total=total[0])

    def get(self, entity_id: str, pk: Any) -> dict[str, Any] | None:
        entity = self._entity(entity_id)
        page = self.list(entity_id, filters={entity.primary_key: pk}, limit=1)
        return page.rows[0] if page.rows else None

    def count(self, entity_id: str, *, filters: Filters | None = None) -> int:
        entity = self._entity(entity_id)
        where, args = self._where(entity, filters)
        with self.db.read() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {self._table(entity)} WHERE {where}", args)
            return row.fetchone()[0]

    def column_values(self, entity_id: str, field_id: str) -> builtins.list[Any]:
        """Distinct non-null values of one field (core column or attr) across the family."""
        entity = self._entity(entity_id)
        column, _ = self._column(entity, field_id)
        where, args = self._where(entity, None)
        with self.db.read() as conn:
            rows = conn.execute(f"SELECT DISTINCT {column} FROM {self._table(entity)} "
                                f"WHERE {where} AND {column} IS NOT NULL", args).fetchall()
        return [r[0] for r in rows]

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
              ) -> builtins.list[dict]:
        """Read-only SQL for plugin authors: one SELECT over entity tables, nothing else.

        Enforced by SQLite's authorizer (not by parsing), so writes, pragmas, ATTACH and reads
        of system tables (audit log, grants) are refused by the engine itself.
        """
        if not _SELECT_START.match(_SQL_COMMENT.sub(" ", sql).strip()):
            raise StoreError("query() only runs a single SELECT statement")
        with self.db.read() as conn:
            conn.set_authorizer(_read_only_authorizer)
            try:
                cursor = conn.execute(sql, params)
                names = [d[0] for d in cursor.description or ()]
                return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            except (sqlite3.Error, sqlite3.Warning) as exc:
                raise StoreError(f"query refused or failed: {exc}") from None
            finally:
                conn.set_authorizer(None)

    # ------------------------------------------------------------ writes

    def _keep_stored(self, conn: sqlite3.Connection, entity: ResolvedEntity,
                     record: Mapping[str, Any], clean: dict[str, Any],
                     errors: dict[str, str]) -> str | None:
        """For ``merge``: lay ``record`` over the stored row with the same key. A field the
        record leaves out takes the stored value (and the "required" error its omission caused
        goes with it); a field the record names, ``None`` included, keeps the record's value.
        Returns the problem when the key is stored under another category."""
        key = clean[entity.primary_key]
        if key is None:
            return None  # a record without a key is refused by validate_record
        stored = conn.execute(
            f"SELECT * FROM {self._table(entity)} WHERE {_q(entity.primary_key)} = ?",
            (_to_sql(key),)).fetchone()
        if stored is None:
            return None
        if stored[ENTITY_KEY] != entity.id:
            return (f"the row is stored as '{stored[ENTITY_KEY]}'; writing it as '{entity.id}' "
                    "would change its category")
        kept = self._row(entity, stored)
        for spec in entity.all_fields:
            if spec.id not in record:
                clean[spec.id] = kept.get(spec.id)
                errors.pop(spec.id, None)
        return None

    def upsert_many(self, entity_id: str, records: Iterable[Mapping[str, Any]], *,
                    merge: bool = False) -> int:
        """Validate every record, then insert-or-replace them all in one transaction.

        All or nothing: one invalid record rejects the batch. By default a record is the whole
        row (a missing field becomes NULL) and the row's ``_entity`` becomes ``entity_id``.

        With ``merge`` a record holds only the fields to change: the fields it leaves out keep
        the stored row's values, read in the same transaction as the write so that a concurrent
        change is not overwritten; a field it names with ``None`` is cleared; a key not stored
        yet starts a row with NULLs. A key stored under another category is refused: a merge
        never changes what a row is.
        """
        entity = self._entity(entity_id)
        columns = [*(f.id for f in entity.fields), ENTITY_KEY, ATTRS_COLUMN]
        updates = ", ".join(f"{_q(c)} = excluded.{_q(c)}" for c in columns
                            if c != entity.primary_key)
        sql = (f"INSERT INTO {self._table(entity)} ({', '.join(map(_q, columns))}) "
               f"VALUES ({', '.join('?' * len(columns))}) "
               f"ON CONFLICT({_q(entity.primary_key)}) DO UPDATE SET {updates}")
        with self.db.transaction() as conn:
            rows, problems = [], []
            for index, record in enumerate(records, 1):
                clean, errors = validate_record(entity, record)
                clash = self._keep_stored(conn, entity, record, clean, errors) if merge else None
                if clash:
                    problems.append(f"record {index}: {clash}")
                    continue
                problems += [f"record {index}: {message}" for message in errors.values()]
                attrs = {a.id: clean[a.id] for a in entity.attrs if clean[a.id] is not None}
                rows.append([*(_to_sql(clean[f.id]) for f in entity.fields), entity.id,
                             json.dumps(attrs, sort_keys=True, ensure_ascii=False)])
            if problems:
                raise StoreError(f"{len(problems)} invalid value(s); nothing was written",
                                 problems)
            conn.executemany(sql, rows)
        return len(rows)

    def transaction(self) -> AbstractContextManager[Any]:
        return self.db.transaction()
