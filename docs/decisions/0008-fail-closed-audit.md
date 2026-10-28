# ADR 0008: Fail-closed audit in the write's transaction, with full-ladder replay

## Status

Accepted. `tests/pipeline/test_pipeline_audit_failclosed.py` pins the audit record in the
write's transaction; `tests/server/test_audit_replay.py` pins the whole decision ladders, the
clarify and chat records and the request chain.

## Context

The console's claims (every number came from code, every write was approved, nobody saw a field
their role hides) can only be checked if every run that returns data or changes it leaves a
record, and the record says how the decision was made. An audit log that fails open has gaps
exactly when something goes wrong.

## Options considered

1. **Best-effort logging**: write the record, log and carry on if that fails.
   - For: the console stays available when the audit table does not.
   - Against: a write can happen with no record of it, the one case the log exists for.
2. **A separate audit store or queue, written asynchronously.**
   - For: decoupled, scales independently.
   - Against: the data change can commit while its record is lost; closing that gap needs a
     two-phase protocol.
3. **The same database connection, the outcome record in the write's transaction, and every
   failure fatal** (chosen).
   - For: a write and its record commit or roll back together; no protocol needed.
   - Against: when the audit log cannot be written, nothing runs.

## Decision

- One `Database` (one SQLite connection, a re-entrant lock, savepoints for nested transactions)
  serves the store, the audit log, the grants, the pending approvals and the users
  (`core/database.py`, `runtime.py`).
- `AuditSink.record` raises `AuditError` instead of swallowing errors, and a broken sink counts
  as a failed write (`core/audit.py`). In the pipeline (`core/pipeline.py`): a write records
  `started` first and does not run if that fails; the data change and its `ok` record share one
  transaction, so a failed record rolls the change back; a read whose record fails withholds its
  result; a confirmation whose record fails revokes the grant it just issued; a refusal whose
  record fails is reported as an error. An import commit writes its rows and its record in one
  transaction too.
- **Replay.** The engine hands the routing steps to the pipeline as `prior_trace`, so each record
  holds the whole ladder (candidates, understand, scope, gate, execute), and the graph's `record`
  node appends the steps after the last run (narrate, respond). A question that never reached the
  pipeline (a clarification, a chat answer, an explicit target outside the candidate set) gets
  one record of its own with its trace.
- **Request chain.** Every record of one request shares a `request_id`. An approved write's
  execution and an import's commit point at the record that asked for the confirmation through
  `parent_id`. Refusing or rejecting an approval leaves a record in the same chain, with one
  `gate` step at stage `approval`.
- Restricted params are masked before the record is written; the grant's public id, never its
  token, is stored.

## Consequences

- **Availability against accountability.** A broken audit table stops every read and write. That
  is the intended trade for a console whose value is that its answers can be checked.
- **What is best-effort.** A record is best-effort only where nothing was read or changed, or
  where the run it describes is already on record, and the code says so at each place:
  - the decision record of a query whose pipeline wrote none, such as a clarification, a chat answer or an explicit target outside the candidate set (`core/graph/builder.py`,
    `_record_decision`): the answer stands and a warning is logged;
  - the `error` record of an exploration hand-over the guard refused (`handover_refused`;
    `_refuse_handover` in the same file): nothing ran, the refusal is still in the trace and the
    answer, and a failure to record it is logged as a warning;
  - the narrate and respond steps appended after a run (`record` in the same file; `_attach` in
    `server/routes/approvals.py` for an approved run; `_append` in `mcp/server.py` for the steps after an MCP call): the answer stands and a warning is logged;
  - the `invalid` or `error` record written after a handler fails (`Pipeline` run, `failed()` in
    `core/pipeline.py`): the run already failed, nothing is returned, and a write was rolled
    back, so a failure to record the failure is dropped. A write's `started` record is already
    on file;
  - the `error` record of an import commit that failed and rolled back
    (`core/entities/importer.py`, `commit`): the failure is re-raised, the record's own failure
    is dropped.
- **What leaves no record.** A query refused at the door (empty, or longer than the limit) has no
  routing decision to describe, and an approval id that was never issued answers 404; neither writes one, and neither does browsing the Data window, signing in or the health check.
  Nor does an import commit refused as expired (`commit_import` in `server/routes/imports.py`): a
  preview past its expiry is no longer found, so the endpoint answers 404, and 410
  `approval_expired` is reachable only when the preview expires during the call. Approving an
  expired import through `/api/approvals/{id}/approve` is audited.
- An approved write replays as a chain, not one record: the request record holds the routing
  ladder up to the held gate, the execution record holds the gate, execution and narration, and
  `parent_id` joins them.
- One SQLite connection means one writer at a time. That suits a console; it is not a design for
  high write throughput.

## Evidence

- `tests/pipeline/test_pipeline_audit_failclosed.py`: an outcome audit failure rolls the write
  back, an intent audit failure means nothing executes, a read with a failing audit withholds the
  result, a blocked call with a failing audit is an error, a confirmation with a failing audit
  revokes the grant, a handler exception rolls back partial writes.
- `tests/server/test_audit_replay.py`: a deterministic query replays its whole ladder, the stream
  leaves the same record, clarify and chat leave records, an approval links its execution to its
  request, an import commit links back to its preview, a rejection links back, and a failed
  append or decision record keeps the answer and logs it.
- `uv run twotrack demo` prints each record's replay and, for the write, the chain
  needs_confirmation, started, ok, with the execution's `parent_id` pointing at the request.
