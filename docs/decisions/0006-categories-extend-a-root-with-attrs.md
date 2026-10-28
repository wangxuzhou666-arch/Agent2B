# ADR 0006: Plugins declare entities; categories extend a root with attrs JSON

## Status

Accepted. `tests/core/test_registry.py`, `tests/store/test_store_sqlite.py` and
`tests/plugins/test_drop_in_cold_chain.py` pin the contract, the storage and the drop-in example.

## Context

Items come in categories with fields of their own: a spare part fits a machine model, packaging
has a material, a cold-chain item has a storage temperature and a shelf life. Categories appear
after deployment. Adding one should need no core change and no migration; every existing report
should keep working on it and be able to filter by it; restricted fields should stay restricted.

## Options considered

1. **A table per category, with DDL when a category is added.**
   - For: typed, indexable columns.
   - Against: a migration per category; every family-wide report must union tables; the schema
     drifts per deployment.
2. **An entity-attribute-value table.**
   - For: any field, no DDL.
   - Against: every read is a pivot; types are lost; filters are slow and awkward.
3. **One table per root entity, typed core columns, and the category's attrs as JSON** (chosen).
   - For: a category adds no column; family reports read one table; the attrs keep the types and
     rules their `FieldSpec` declares.
   - Against: attrs are validated in Python, not by SQLite, and are not indexed.

## Decision

- A plugin is a module exporting `PLUGIN = Plugin(name, version, ...)` with any of `entities`,
  `tools`, `workflows` and `preconditions` (`core/contract.py`). The registry loads the built-in
  plugins and every `*.py` file or package in the directories of `TWOTRACK_PLUGIN_PATHS`, sorted
  by name; names that start with `_` or `.` are skipped (`core/registry.py`).
- Every tool and workflow declares `roles`, `effect`, `approval`, `outputs` and `explorable`,
  none with a default. The registry refuses a combination the core does not allow (a role the
  effect is closed to, a write that does not require approval, a read that does). At run time the
  pipeline holds each result to its declared `outputs`: an aggregate key, column or row key the
  target did not declare is an error and nothing of the result is returned.
- An entity that `extends` another inherits its core fields and attrs and may add only `attrs`;
  its primary key must be the parent's. The registry reports a child that declares core fields,
  an unknown parent, a cycle, a duplicate field and an import header shared by two fields.
- Storage (`core/store/sqlite.py`): each root entity owns a table `e_<root>` with a typed column
  per core field, an `_entity` column naming the row's category and an `_attrs` JSON column. Reads
  of an entity cover its whole family (`_entity IN (...)`); a filter or sort on an attr uses
  `json_extract`. `upsert_many` validates every record against its entity's `FieldSpec`s and
  writes all or nothing.
- The registry fails closed: every problem of every plugin is collected into one `RegistryError`
  and the server does not start. At startup `ensure_schema` creates the table of a root that has
  none, and compares an existing table with the registry: it refuses to start on any column
  difference, and on rows of a category that is no longer declared. There is no automatic
  migration.
- Tool code reads through the `Store` protocol. Plugin SQL goes through `query()`, which SQLite's
  authorizer restricts to `SELECT` over entity tables, so a plugin cannot read the audit log or
  the grant table that way.

The drop-in example, `examples/plugins/cold_chain.py`, declares `cold_chain_item` (extends `sku`,
two attrs: `storage_temp_c`, `shelf_life_days`), its synonyms in two languages ("cold chain",
冷链) and an `expiring_soon` report. Started with `TWOTRACK_PLUGIN_PATHS=examples/plugins`, its
rows live in the SKU table, every built-in stock and purchase-order report accepts
`category="cold_chain_item"`, a question that says 冷链 is bound to that filter through the
synonyms alone, and the console's Data window shows the new category as a tab. No core or
built-in file changes.

## Consequences

- Attrs are not indexed. A filter on an attr is a `json_extract` over the family's rows; fine at
  console scale, a reason to promote a heavily filtered attr to a core field (which is a
  migration).
- Type and rule checks on attrs run in Python on every write; SQLite does not enforce them.
- A core field change needs a migration before start, by design: the store refuses to guess.
- A plugin is code and runs in-process with the server's privileges. The registry checks what a
  plugin declares, and the pipeline holds each call to its role, its approval and its declared
  `outputs`; nothing checks what a handler does besides returning a result. A read or external
  tool gets a read-only store, and the authorizer limits what its SQL can read.

## Evidence

- `tests/plugins/test_drop_in_cold_chain.py`: not loaded without the plugin path; the path adds
  the category without core change; rows live in the SKU table with no new columns; the report
  works.
- `tests/store/test_store_sqlite.py`: child rows live in the root table, a new category adds no
  columns, a changed core field fails startup, rows of a removed category fail startup, upserts
  validate attrs all or nothing, plugin queries are read-only and limited to entity tables.
- `tests/core/test_registry.py`: `test_all_problems_are_reported_not_just_the_first`,
  `test_load_failures_are_fail_closed`, `test_drop_in_plugin_dir_adds_entity_without_core_change`.
- `web/e2e/drop-in.spec.ts` runs the console against the API started with the cold-chain plugin.
