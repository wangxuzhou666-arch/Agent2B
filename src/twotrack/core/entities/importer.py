"""Two-phase data import: preview (parse, map headers, validate rows) -> commit.

Import is a write, so it follows the same rules as a write tool:

* only roles in ``ImportService.roles`` (default operator; admin always passes) may import;
* ``preview`` issues a one-time confirmation grant bound to the preview ``digest``
  (entity + file hash + column mapping + which rows are valid);
* ``commit`` re-parses and re-validates the same bytes, refuses if the digest differs, consumes
  the grant, then upserts every valid row and writes the outcome audit record in one
  transaction (a failed audit write rolls the rows back). Rows with errors are skipped.
* an update writes only the columns the file has: the fields it has no column for keep their
  stored values (``Store.upsert_many(merge=True)``), and a row stored under another category
  than the one imported into is a row problem, never reclassified.

Every preview, rejection and commit leaves an audit record; a failed audit write raises. The
records of one upload share a ``request_id`` (made by the preview), the commit's records point
at the preview record that asked for the confirmation (``parent_id``), and each record holds
the trace steps of its run (``parse``, ``validate``, ``gate``, ``execute``; see core.audit).
File-level problems raise ``ImportRejected`` with a stable ``code`` for the API to map; it words
itself in either reply language (``ImportRejected.text``). Row problems, column labels and the
confirmation's summary take the ``language`` the preview is asked for.
"""

import csv
import io
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

import openpyxl
from pydantic import BaseModel, JsonValue

from ..audit import AuditRecorder, AuditSink, AuditStatus
from ..canonical import canonical_json, sha256_hex
from ..contract import ENTITY_KEY, Principal, ResolvedEntity, TraceStatus, TraceStep
from ..grants import Grant, GrantStore, grant_id_for
from ..guards.upload import UploadLimits, UploadRejected, check_upload, decode_text
from ..i18n import as_language, join_and, say
from .values import FieldProblem, check_record

IMPORT_TARGET_PREFIX = "import:"
ImportRejected = UploadRejected  # same shape; codes below cover both file and gate problems
# codes: empty, too_large, file_type, zip_bomb, parse_error, too_many_rows, too_many_columns,
#        no_header, header_collision, missing_columns, restricted_column, forbidden,
#        digest_mismatch, grant_invalid


class ParsedTable(BaseModel):
    file_type: Literal["csv", "xlsx"]
    encoding: str | None
    headers: list[str]
    rows: list[tuple[int, list[Any]]]  # (row number in the file, cells)


class ColumnMapping(BaseModel):
    column: int
    header: str
    field_id: str
    label: str  # the field's display name in the language the preview was asked for


class FieldError(BaseModel):
    """One problem of one preview row, for the cell it is about."""

    field_id: str | None  # the field (cell); None: the row as a whole (e.g. a duplicate key)
    message: str  # what is wrong, without the field's name; never the value


class PreviewRow(BaseModel):
    row: int
    values: dict[str, JsonValue]
    errors: list[str]  # each problem as one sentence, "Criticality: must be one of A, B or C"
    field_errors: list[FieldError]  # the same problems, per cell
    action: Literal["new", "update"] | None = None


class ImportPreview(BaseModel):
    entity: str
    file_type: str
    encoding: str | None
    mapping: list[ColumnMapping]
    unmapped: list[str]
    rows: list[PreviewRow]
    valid_count: int
    error_count: int
    digest: str
    grant: Grant | None = None


class ImportResult(BaseModel):
    entity: str
    written: int
    skipped: int
    digest: str
    audit_id: int


# ---------------------------------------------------------------- parsing


def _cell(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() \
            else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value.strip() if isinstance(value, str) else value


def _empty(cell: Any) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def _blank(cells: list[Any]) -> bool:
    return all(_empty(c) for c in cells)


def _header(cells: list[Any], limits: UploadLimits) -> list[str]:
    """The names of a header row that holds something, without the empty cells at its end (a
    styled row ends in dozens), as text. More than ``max_columns`` of them is refused."""
    width = len(cells)
    while _empty(cells[width - 1]):
        width -= 1
    if width > limits.max_columns:
        raise UploadRejected.of("too_many_columns", "upload_too_many_columns",
                                limit=limits.max_columns)
    return ["" if c is None else str(c) for c in cells[:width]]


def _collect(records: Iterable[Sequence[Any]], limits: UploadLimits
             ) -> tuple[list[str], list[tuple[int, list[Any]]]]:
    """The header (the first row that holds something) and the data rows after it, each with
    its row number in the file. The work is bounded by ``limits``, never by what the file
    claims: every record pulled counts toward ``max_rows_read``, empty ones included, and no
    record is looked at beyond ``max_columns`` + 1 cells."""
    headers: list[str] | None = None
    rows: list[tuple[int, list[Any]]] = []
    for number, record in enumerate(records, 1):
        if number > limits.max_rows_read:
            raise UploadRejected.of("too_many_rows", "upload_rows_read",
                                    limit=limits.max_rows_read)
        cells = [_cell(c) for c in record[:limits.max_columns + 1]]
        if _blank(cells):
            continue
        if headers is None:
            headers = _header(cells, limits)
            continue
        rows.append((number, cells[:len(headers)]))
        if len(rows) > limits.max_rows:
            raise UploadRejected.of("too_many_rows", "upload_too_many_rows", limit=limits.max_rows)
    if headers is None:
        raise UploadRejected.of("no_header", "upload_no_header")
    return headers, rows


def parse_upload(raw: bytes, limits: UploadLimits, filename: str | None = None) -> ParsedTable:
    """Guards first, then parse. Raises UploadRejected. ``filename`` can only contradict the
    bytes (a ``.xlsx`` name over a file that is no workbook is refused), never decide the type.

    An xlsx sheet is read for the rows and cells it really has: its ``<dimension>`` is a claim
    of the file's own, and openpyxl pads every row to it (a tiny file can claim 16384 columns),
    so the claim is dropped (``reset_dimensions``) and rows come back as long as their last
    cell. A dimension that claims too little therefore no longer hides the rows past it."""
    kind = check_upload(raw, limits, filename)
    try:
        if kind == "csv":
            text, encoding = decode_text(raw)
            dialect = csv.excel
            try:
                dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;\t")
            except csv.Error:
                pass
            headers, rows = _collect(csv.reader(io.StringIO(text), dialect), limits)
            return ParsedTable(file_type="csv", encoding=encoding, headers=headers, rows=rows)
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            sheet.reset_dimensions()
            headers, rows = _collect(sheet.iter_rows(values_only=True), limits)
        finally:
            workbook.close()
        return ParsedTable(file_type="xlsx", encoding=None, headers=headers, rows=rows)
    except UploadRejected:
        raise
    except Exception as exc:  # parser failures on untrusted bytes are a user-facing 400
        raise UploadRejected.of("parse_error", "upload_parse_error",
                                error=type(exc).__name__) from None


def _collision_text(entity: ResolvedEntity, clashes: dict[str, list[str]],
                    language: str | None) -> str:
    """'“Qty” and “Quantity” map to Quantity (qty)', one clause per field with several columns."""
    items = [say("upload_collision_item", language,
                 headers=join_and([f"“{header}”" for header in headers], language),
                 field=entity.field(field_id).label_in(language), id=field_id)
             for field_id, headers in clashes.items()]
    return say("upload_header_collision", language,
               clashes=("；" if as_language(language) == "zh" else "; ").join(items))


def map_headers(entity: ResolvedEntity, headers: list[str], language: str | None = None, *,
                principal: Principal) -> tuple[list[ColumnMapping], list[str]]:
    """Header -> field via id/label/aliases. A column that maps to a field ``principal`` may not
    see rejects the file (the rule of the Data window: a role writes what it can read), and so
    do two columns on one field and a required field no column maps to. ``language`` words the
    column labels and the rejection.

    The restricted columns are refused first: the collision and missing-column messages name
    fields, which a role that may not see them must not learn from a refusal."""
    mapping: list[ColumnMapping] = []
    unmapped: list[str] = []
    for column, header in enumerate(headers):
        if not header.strip():
            continue
        spec = entity.match_header(header)
        if spec is None:
            unmapped.append(header)
        else:
            mapping.append(ColumnMapping(column=column, header=header, field_id=spec.id,
                                         label=spec.label_in(language)))
    visible = {f.id for f in entity.visible_fields(principal)}
    barred = [m.header for m in mapping if m.field_id not in visible]
    if barred:
        raise UploadRejected.worded("restricted_column", lambda lang: say(
            "upload_restricted_columns", lang,
            columns=join_and([f"“{header}”" for header in barred], lang)))
    by_field: dict[str, list[str]] = {}
    for m in mapping:
        by_field.setdefault(m.field_id, []).append(m.header)
    clashes = {field_id: names for field_id, names in by_field.items() if len(names) > 1}
    if clashes:
        raise UploadRejected.worded(
            "header_collision", lambda lang: _collision_text(entity, clashes, lang))
    missing = [f for f in entity.all_fields
               if (f.required or f.id == entity.primary_key) and f.id not in by_field]
    if missing:
        raise UploadRejected.worded("missing_columns", lambda lang: say(
            "upload_missing_columns", lang, columns=join_and([f.label_in(lang) for f in missing],
                                                              lang)))
    return mapping, unmapped


def build_preview(entity: ResolvedEntity, table: ParsedTable, raw: bytes,
                  stored_entity: Callable[[Any], ResolvedEntity | None] | None = None,
                  language: str | None = None, *, principal: Principal
                  ) -> tuple[ImportPreview, list]:
    """Validate every row. Returns the preview and the clean records of the valid rows: each
    holds only the fields that have a column in the file, because an update writes just those
    (``upsert_many(merge=True)``). ``language`` is the language of the row problems and the
    column labels (English by default); it does not change which rows are valid, so the digest
    does not depend on it. ``principal`` is who imports: a column it may not write rejects the
    file (``map_headers``).

    ``stored_entity(key)`` is the entity the stored row with that key belongs to, or None when
    there is none; it tells new rows from updates. A row stored under another category than
    ``entity`` is a row problem: an import never changes what a row is. Without it the preview
    labels no row."""
    mapping, unmapped = map_headers(entity, table.headers, language, principal=principal)
    rows: list[PreviewRow] = []
    records: list[dict[str, Any]] = []
    seen: dict[Any, int] = {}
    for number, cells in table.rows:
        given = {m.field_id: cells[m.column] if m.column < len(cells) else None
                 for m in mapping}
        clean, problems = check_record(entity, given, language)
        key = clean.get(entity.primary_key)
        if key is not None and key in seen:
            problems.append(FieldProblem(None, "", say("value_duplicate_key", language,
                                                       row=seen[key])))
        elif key is not None:
            seen[key] = number
        stored = stored_entity(key) if stored_entity is not None and not problems else None
        if stored is not None and stored.id != entity.id:
            problems.append(FieldProblem(None, "", say(
                "value_category_change", language, pk=key, stored=stored.label_in(language),
                entity=entity.label_in(language))))
        wrong = {p.field_id for p in problems}
        shown = {fid: (given[fid] if fid in wrong else clean[fid]) for fid in given}
        action = None
        if not problems and stored_entity is not None:
            action = "new" if stored is None else "update"
        rows.append(PreviewRow(
            row=number, values=_jsonable_values(shown),
            errors=[p.text(language) for p in problems],
            field_errors=[FieldError(field_id=p.field_id, message=p.message) for p in problems],
            action=action))
        if not problems:
            records.append({field_id: clean[field_id] for field_id in given})
    valid = [r.row for r in rows if not r.errors]
    digest = sha256_hex(canonical_json({
        "entity": entity.id, "file": sha256_hex(raw),
        "mapping": [[m.column, m.header, m.field_id] for m in mapping], "valid_rows": valid}))
    preview = ImportPreview(entity=entity.id, file_type=table.file_type,
                            encoding=table.encoding, mapping=mapping, unmapped=unmapped,
                            rows=rows, valid_count=len(valid),
                            error_count=len(rows) - len(valid), digest=digest)
    return preview, records


def _jsonable_values(values: dict[str, Any]) -> dict[str, JsonValue]:
    return {k: v if v is None or isinstance(v, str | int | float | bool) else str(v)
            for k, v in values.items()}


# ---------------------------------------------------------------- service


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _step(step: str, status: TraceStatus, started: float | None = None,
          **facts: Any) -> TraceStep:
    duration = round((time.perf_counter() - started) * 1000, 3) if started is not None else 0.0
    return TraceStep(step=step, status=status, facts=facts, duration_ms=duration)


def import_summary(entity: ResolvedEntity, count: int, filename: str,
                   language: str | None = None) -> str:
    """What the confirmation of an import says: how many rows go into which entity, from which
    file, in plain words of ``language``."""
    return say("import_summary", language, count=count, entity=entity.label_in(language),
               filename=filename[:120])


def import_label(entity: ResolvedEntity, language: str | None = None) -> str:
    """The title of an import confirmation ("Import Inventory"), in ``language``."""
    return say("import_label", language, entity=entity.label_in(language))


class ImportService:
    def __init__(self, registry: Any, store: Any, audit: AuditSink, grants: GrantStore, *,
                 limits: UploadLimits | None = None, roles: tuple[str, ...] = ("operator",),
                 clock: Callable[[], datetime] | None = None) -> None:
        self.registry, self.store, self.audit, self.grants = registry, store, audit, grants
        self.limits = limits or UploadLimits()
        self.roles = roles
        self.clock = clock or _utc_now

    def _stored_entity(self, entity: ResolvedEntity, key: Any) -> ResolvedEntity | None:
        """The entity of the stored row with this key, whichever category of ``entity``'s
        family it is in (a key lives once per table, so a clash is found from any entity)."""
        row = self.store.get(entity.lineage[0], key)
        return None if row is None else self.registry.entity(row[ENTITY_KEY])

    def _recorder(self, principal: Principal, entity_id: str) -> AuditRecorder:
        return AuditRecorder(self.audit, self.clock, user_id=principal.user_id,
                             roles=sorted(r.value for r in principal.roles),
                             target=IMPORT_TARGET_PREFIX + entity_id, effect="write",
                             route_source="explicit")

    def _prepare(self, principal: Principal, entity_id: str, filename: str, raw: bytes,
                 links: dict[str, Any], language: str | None = None
                 ) -> tuple[ImportPreview, list, AuditRecorder, dict, list[TraceStep]]:
        """Role check, parse and validate. A refusal is audited (with ``links`` and the steps
        so far) before it raises; otherwise the steps are returned for the caller's record."""
        entity = self.registry.entity(entity_id)
        record = self._recorder(principal, entity_id)
        facts: dict[str, Any] = {"filename": filename[:256], "bytes": len(raw)}
        if not principal.can_access(self.roles):
            record(AuditStatus.BLOCKED, params=facts, problem="forbidden",
                   trace=[_step("gate", TraceStatus.BLOCKED, stage="authz")], **links)
            raise UploadRejected.of("forbidden", "upload_forbidden")
        steps: list[TraceStep] = []
        started = time.perf_counter()
        try:
            table = parse_upload(raw, self.limits, filename)
            steps.append(_step("parse", TraceStatus.OK, started, file_type=table.file_type,
                               encoding=table.encoding, rows=len(table.rows)))
            started = time.perf_counter()
            preview, records = build_preview(
                entity, table, raw, lambda key: self._stored_entity(entity, key),
                language=language, principal=principal)
        except UploadRejected as exc:
            failed = "validate" if steps else "parse"
            steps.append(_step(failed, TraceStatus.BLOCKED, started, code=exc.code))
            record(AuditStatus.INVALID, params=facts, problem=exc.code, trace=steps, **links)
            raise
        steps.append(_step("validate", TraceStatus.OK, started, columns=len(preview.mapping),
                           unmapped=len(preview.unmapped), valid=preview.valid_count,
                           errors=preview.error_count))
        facts.update(rows=len(preview.rows), valid=preview.valid_count,
                     errors=preview.error_count)
        return preview, records, record, facts, steps

    def preview(self, principal: Principal, entity_id: str, filename: str, raw: bytes, *,
                language: str | None = None, request_id: str | None = None) -> ImportPreview:
        """``language``: of the row problems, the column labels and the confirmation's summary.
        ``request_id``: shared by every record of this upload, its commit included (a new one
        when not given)."""
        links = {"request_id": request_id or uuid.uuid4().hex}
        preview, _, record, facts, steps = self._prepare(principal, entity_id, filename, raw,
                                                         links, language)
        grant = None
        if preview.valid_count:
            summary = import_summary(self.registry.entity(entity_id), preview.valid_count,
                                     filename, language)
            grant = self.grants.issue(principal, IMPORT_TARGET_PREFIX + entity_id,
                                      {"digest": preview.digest}, summary)
            steps.append(_step("gate", TraceStatus.BLOCKED, stage="confirmation",
                               grant_id=grant.grant_id))
        try:
            record(AuditStatus.NEEDS_CONFIRMATION if grant else AuditStatus.OK, params=facts,
                   params_digest=preview.digest, grant_id=grant.grant_id if grant else None,
                   row_count=preview.valid_count, trace=steps, **links)
        except Exception:
            if grant:
                self.grants.reject(grant.token, principal)
            raise
        return preview.model_copy(update={"grant": grant})

    def commit(self, principal: Principal, entity_id: str, filename: str, raw: bytes, *,
               expected_digest: str, grant_token: str, parent_id: int | None = None,
               request_id: str | None = None) -> ImportResult:
        """``parent_id``/``request_id`` link the commit's records to the preview record that
        asked for the confirmation (see ``core.audit``). Every record of the commit names the
        grant it presents, a refusal before that grant is consumed included, so the grant's
        records always show why nothing was written."""
        links = {"parent_id": parent_id, "request_id": request_id,
                 "grant_id": grant_id_for(grant_token) if grant_token else None}
        preview, records, record, facts, steps = self._prepare(principal, entity_id, filename,
                                                               raw, links)
        target = IMPORT_TARGET_PREFIX + entity_id
        fields = dict(params=facts, params_digest=preview.digest, **links)
        if preview.digest != expected_digest:
            record(AuditStatus.BLOCKED, problem="digest_mismatch", **fields,
                   trace=[*steps, _step("gate", TraceStatus.BLOCKED, stage="digest")])
            raise UploadRejected.of("digest_mismatch", "upload_digest_mismatch")
        if not self.grants.consume(grant_token, principal, target, {"digest": expected_digest}):
            record(AuditStatus.BLOCKED, problem="grant_invalid", **fields,
                   trace=[*steps, _step("gate", TraceStatus.BLOCKED, stage="confirmation")])
            raise UploadRejected.of("grant_invalid", "upload_grant_invalid")
        steps.append(_step("gate", TraceStatus.OK, stage="confirmation",
                           confirmation="consumed"))
        record(AuditStatus.STARTED, trace=steps, **fields)
        started = time.perf_counter()
        try:
            with self.store.transaction():
                written = self.store.upsert_many(entity_id, records, merge=True)
                audit_id = record(AuditStatus.OK, row_count=written, **fields, trace=[
                    *steps, _step("execute", TraceStatus.OK, started, written=written,
                                  skipped=preview.error_count)])
        except Exception as exc:
            try:
                record(AuditStatus.ERROR, problem=f"{type(exc).__name__}"[:200], **fields,
                       trace=[*steps, _step("execute", TraceStatus.ERROR, started,
                                            error=type(exc).__name__, rolled_back=True)])
            except Exception:
                pass  # the commit already failed and was rolled back
            raise
        return ImportResult(entity=entity_id, written=written, skipped=preview.error_count,
                            digest=preview.digest, audit_id=audit_id)
