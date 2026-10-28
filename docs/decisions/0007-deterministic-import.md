# ADR 0007: Deterministic import, with no LLM column mapping

## Status

Accepted. `tests/importer/test_importer.py` pins the mapping, the two phases and the upload
guards. Chinese labels count as header aliases, and refusals are bilingual.

## Context

Operators bring spreadsheets with their own headers, in English or Chinese. An import writes the
data every report reads afterwards. A wrong column mapping, say "Reserved" read as on-hand
stock, produces rows that validate, look normal and are wrong in every figure computed from
them. That is silent corruption: once written, the numeric lock cannot help, because the bad
numbers are now backed by data ([ADR 0001](0001-code-computes-the-model-narrates.md)).

## Options considered

1. **Let a model map the columns.**
   - For: accepts arbitrary headers.
   - Against: a guess, possibly different on the next run; headers and sample cells go to a
     provider; a wrong guess is invisible after commit.
2. **A mapping step where the user assigns every column.**
   - For: explicit.
   - Against: friction on every import; not built.
3. **Declared header texts, refusal on ambiguity, preview before commit** (chosen).
   - For: the same file always maps the same way; every mapping is visible in the preview; a
     header that could mean two fields stops the import instead of picking one.
   - Against: a header nobody declared is ignored until someone renames the column or adds an
     alias to the plugin.

## Decision

- **Mapping** (`core/entities/importer.py`, `map_headers`). A header maps to a field when, after
  NFKC folding, case folding and removing spaces, `_` and `-`, it equals the field's id, its label
  in any language or one of its aliases. Unknown headers are reported as unmapped and ignored. Two
  columns on one field reject the file (`header_collision`); a required field or primary key with
  no column rejects it (`missing_columns`). Two fields of one entity that share a header text
  fail the registry load, so within an entity one header can never mean two things.
- **Two phases.** The preview parses, maps, validates every row (typed coercion, enum domains,
  required fields, duplicate keys) and computes a digest over the entity, the file's SHA-256, the
  mapping and which rows are valid; the grant it issues is bound to that digest. The server stages
  the bytes. The commit re-parses and re-validates the staged bytes, refuses a digest that differs
  (409 `digest_mismatch`), consumes the grant (409 `grant_invalid` otherwise), then writes the
  valid rows and the outcome audit record in one transaction. Rows with errors are skipped and
  counted.
- **Upload guards** (`core/guards/upload.py`). Before any parser, an empty file or one over
  2 MiB by default (`TWOTRACK_MAX_UPLOAD_BYTES`) is refused. The type comes from the bytes
  (`sniff_file_type`), and a filename can only count against them: a file that starts like a zip
  is taken for an xlsx; a legacy `.xls`, a known binary signature (PDF, PNG, GIF, JPEG, gzip, an
  executable, RAR, 7z, an empty zip), a `.xlsx` name over bytes that are no zip (refused instead
  of read as CSV) and a NUL byte in the first 8192 bytes are refused; any other text is read as
  CSV. So the check does not tell a table from other text: a file of the wrong shape is refused
  later, for missing required columns. An xlsx is
  checked from its zip central directory before anything is decompressed: at most 200 entries,
  at most 20 MiB declared uncompressed, no entry above 1 MiB with a ratio above 200, no
  encrypted entries, and the workbook parts present. CSV decodes as UTF-8 (BOM optional),
  then GB18030, then Latin-1. Rows are counted as they are read (`core/entities/importer.py`):
  the first non-blank data row past 2,000 refuses the file, before any row is validated.
- Messages name fields and rules, never cell values, so a refusal is safe to show even for a
  restricted field (`core/entities/values.py`).

## Consequences

- A spreadsheet with unexpected headers needs a rename or a plugin alias. The plugins declare the
  common ones in both languages ("qty on hand", "库存", "物料编码", ...), and the preview lists
  what it did not map.
- The staged file is held server-side until commit or expiry, at most ten staged previews per
  user (`server/approvals.py`).
- Only the user who previewed can commit, and only roles allowed to import (operator; admin
  passes every role check) can preview.
- An import is a write and follows [ADR 0005](0005-one-time-write-grants.md): its confirmation is
  the same approval card, and its records form one chain in the audit log
  ([ADR 0008](0008-fail-closed-audit.md)).

## Evidence

- `tests/importer/test_importer.py`: aliases map and unmapped headers are reported, colliding
  headers reject the file, a missing required column rejects it, a changed file is refused at
  commit, the commit needs the uploader's grant, a viewer cannot import, a failing audit writes
  nothing, size and row caps, the file type decided by the content (a zip taken for a workbook,
  binary signatures and a NUL byte refused), a `.xlsx` name over other bytes, a zip bomb
  refused before parsing, encoding fallback.
- `tests/server/test_import.py` for the API; `web/e2e/import.spec.ts` for the console's wizard.
