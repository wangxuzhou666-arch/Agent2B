# Roadmap

Technical work that is known and not done. No promises: an item is here because the code lacks
it. Items marked **good first issue** are small, self-contained and have a clear test. For how
to start, see [CONTRIBUTING.md](CONTRIBUTING.md).

The console for v0.1 was cut back on purpose so the release could concentrate on the
architecture ([ARCHITECTURE.md](docs/ARCHITECTURE.md)). Most of the console items below are what
was cut.

## Known limits in v0.1

What the code does not do today, stated so that nobody has to find out by running into it. The
security-relevant ones are also in [SECURITY.md](SECURITY.md).

### Routing

- **A read that excludes an id runs unfiltered.** "Late shipments except SUP-…" drops the excluded
  id as a param, so the read runs without that filter, wider than asked. A contraction with no
  verb of using or choosing ("late shipments that aren't from SUP-…") leaves the id a param, so
  the read is filtered **to** the supplier it rules out.
- **A model's proposed params can carry an excluded id.** The exclusion rule reads what the rule
  arm extracts; it does not edit the params a model proposes. A write still shows its exact params
  on the approval card.
- **Some negations and exclusions are not recognized.** 先不 and 暂不 ("not for now"), and 除外
  after its value, are not read as a negation or an exclusion, so a write phrased that way can go
  straight to its approval card. Nothing runs until the requester approves it.
- **"I wonder if…" is not read as a question.** A question that opens with "if" gets the same
  consequence as the item above.
- **Synonyms and plurals cost an extra confirmation.** Only declared keywords, examples and value
  synonyms are rule evidence, and a one-word value synonym matches only as written. A request
  worded otherwise falls to lexical search or a model. A write found that way is confirmed once
  more before its approval card, and nothing runs until the requester approves it: one more
  click, never a write nobody approved. A read found that way is not confirmed. Lexical search,
  or a model that picks it with high confidence, runs it at once (`gate_writes` gates writes
  only), so a read can answer from a report the user did not mean.
- **An external tool that requires approval can be confirmed twice.** The complete-request lift
  covers writes only ([ADR 0005](docs/decisions/0005-one-time-write-grants.md)), so a request that
  gives an `external` tool its phrase and every param is still a weak hit: routing asks for a
  confirmation first, and the pipeline then holds the run for the approval that
  `approval='required'` declares. Two keywords or a matching example make the hit strong and go
  straight to the approval card.

### Models

- **A model's proposed params are validated, not verified.** Validation rejects malformed
  params, not plausible wrong ones. A write shows its exact params on the approval card.
- **No native Anthropic provider for twotrack's own calls.** Claude is reached through Anthropic's
  OpenAI-compatible endpoint.
  Some models reject a non-default temperature; set TWOTRACK_LLM_TEMPERATURE=omit to leave it out of requests.
  The Pi worker sends none.
- **No real-model benchmark yet.** The harness is built and tested against the mock provider only
  ([bench/README.md](bench/README.md)). A real run needs an endpoint, a key and an approved
  budget, and nothing is claimed until one is documented in its own file. The same holds for
  exploration: it is tested with the real Pi worker against the mock and scripted models, and no
  run against a real model is documented yet.
- **Streamed tool arguments are untested.** The mock endpoint sends a call's arguments whole, in
  one delta (`tests/explore/test_endpoint.py` asserts that), so no test streams them in pieces.
  The worker reports a call that Pi refused after a reply cut off at the model's length limit
  (stop reason `length`) as `truncated` (`harness/pi/worker.mjs`), and no test exercises that.

### Exploration runtime

- **The Pi pin.** The worker pins Pi at 1.2.0. Upstream 1.3.0 fixes two tool-argument integrity
  issues, #10897 (a malformed final tool-call JSON ran with its parsed prefix) and #10868 (an
  invalid `\u` escape dropped fields), and has a regression of its own, #11095 (a valid JSON
  object followed by trailing characters is rejected). Moving the pin means a regenerated
  lockfile and a rerun of the worker's tests.
- **No egress restriction.** On Node 22 and 24 `node --permission` does not cover the network, and
  the compose stack does not restrict the API container's outbound traffic, so a compromised
  dependency in `harness/pi` could send out what the worker holds
  ([SECURITY.md](SECURITY.md#known-limits)). Needs a firewall or an egress proxy outside the
  stack, or a network policy in a deployment.
- **Memory and process limits are per container.** Compose sets `mem_limit` and `pids_limit` on
  `api` and `web`, which bound all running questions together. Nothing bounds one run's memory
  or one user's share of the container: a worker is a plain Node process, and the budgets bound
  only its turns, calls and time.
- **Unused provider SDKs in the image.** Pi's install carries SDKs for model vendors that the
  worker never calls (it speaks to an OpenAI-compatible endpoint). They are in the image and in
  the supply chain for nothing. Pruning them must leave the `package.json` of each pinned
  package, which the host's version check reads.
- **Contract-declared model-visible id fields.** A run is shown a tool's parameter schema and, for
  each call, the aggregates, row count, column names and fenced `scope` and `message` of the
  result, never a row ([ADR 0010](docs/decisions/0010-exploration-runs-in-pi-behind-the-same-gate.md)).
  It cannot discover a SKU id on its own: the request has to name it, as the documented
  exploration case does ([docs/EVIDENCE.md](docs/EVIDENCE.md)). That was a deliberate, strict
  choice for v0. The change under consideration: a tool's output contract declares which id
  columns the model may see, so exploration can chain on ids without exposing rows.
- **A worker that does not start is reported as a failed agent step.** The run ends as
  `worker_unavailable`, but it is recorded like any failed run: the console trace reads "Agent
  turn 1 failed (ExplorerUnavailable)" and adds a "Model call failed: agent step" line, though
  no turn and no model call happened. The stop-reason line under the answer names the real cause.

### Console

- **Chinese table headers and route reasons are still English.** The column headers of result
  tables and the reason lines of a route trace are not translated.

## Security and performance

- **A Content-Security-Policy with nonces and `report-to`.** `script-src` and `style-src` carry
  `'unsafe-inline'` because a static header cannot nonce the App Router's inline scripts or the
  theme script (`web/next.config.ts`; [SECURITY.md](SECURITY.md)). Needs per-request nonces,
  which means dynamic rendering, a violation report endpoint, and a measured list of which inline
  styles are really needed.
- **Row virtualization.** The Hub's result table renders every row a tool returned
  (`web/src/components/common/data-table.tsx`; only the Data window is paged). Windowing keeps a
  large result cheap.

## Platform

- **A per-user permission matrix.** Roles are the fixed set `admin`, `operator`, `viewer`
  (`Role` in `core/contract.py`); a tool lists roles and a restricted field lists `visible_roles`.
  A matrix would let an admin grant or withhold one tool, entity or field for one user.
- **Run status.** A query is a server-sent stream (`server/sse.py`): no run id, nothing to poll or
  resume, and a dropped connection stops the graph after the current step. A durable run record
  (state, trace so far) would let the console recover after a reload.
- **A Postgres store.** `Store` (`core/store/base.py`) is the seam for entity data, but audit,
  grants, approvals, users and the token deny-list are SQLite classes on one shared connection,
  which is what puts a write and its audit record in one transaction
  ([ADR 0008](docs/decisions/0008-fail-closed-audit.md)). A port must keep that guarantee.
- **Multi-tenancy.** There is one registry and one database per deployment; nothing scopes data,
  users or the audit log to a tenant.
- **PII masking.** Fields can be hidden by role, audit params are masked and the egress guard
  redacts stored restricted text values. Missing: showing a value in part to a role that may not
  see it whole, and redacting PII-shaped text that is not a stored value (a phone number typed in
  a question).
- **OpenTelemetry.** Only stdlib `logging` today. Traces and metrics for the pipeline, the
  provider calls, the exploration runs and the audit sink.
- **An end-to-end check against a model.** The CI `docker` job boots the zero-key stack and runs
  `make smoke`, which checks that an open-ended question runs in the real Pi worker against the
  mock model. No test or job runs a real or local model: `make up-ollama` starts one, but the
  suites use the mock provider. A job that boots that stack and asserts the invariants (numeric
  lock holds, `provider.mock` is false, route source `llm` appears).

## Console: Data, Import, Audit

- **good first issue** Show "N fields hidden for your role" from `EntityOut.hidden_fields`.
- **good first issue** Render `PreviewRow.field_errors` instead of parsing message prefixes
  (`cellErrors` in `web/src/lib/imports.ts`).
- **good first issue** Take the upload limits from `limits` in `/api/health` instead of the
  hard-coded 2 MiB (`web/src/lib/config.ts`).
- **good first issue** A duration column from `AuditEntryOut.duration_ms`.
- **good first issue** A label for the `none` route-source bucket ("No routing (approval
  steps)").
- A drill-down from the audit overview that carries the stats window into the URL.
- **good first issue** `FieldOut.description` as a tooltip on column headers.
- **good first issue** `ColumnMapping.label` in the import mapping, instead of looking the label
  up again.

## Console: design system

- **Console: raise soft-tone and secondary text contrast to WCAG AA.** Soft-tone text (pills,
  badges and notices), the faded ids and "· writes" tag on clarification chips, and inactive
  segmented options fall below 4.5:1 in at least one theme ([docs/DESIGN.md](docs/DESIGN.md),
  section 2.6 and Appendix B).
- Aged trace lines without opacity.
- One active-state recipe for pickers; one title recipe for sheets.
- Edge fade and a sticky key column on wide tables.
- One content width per window type.
- Replace the chart library (`recharts`) with CSS bars.
- `rem`-based sizing.

## Console: behaviour

- The audit URL round-trips every filter, page and open entry: push for explicit changes, replace
  while typing (it only replaces today).
- **good first issue** Clamp a page number past the end.
- Exact-match text filters committed on Enter or blur.
- Keep a sheet's content while it animates out.
- **good first issue** Ignore files dropped outside the import drop zone.
- **good first issue** Move focus to the composer after the first question.
- Split the hub's state and action contexts and memoize turns.
- Let only tables break out of the reading column.
- Focusable scroll regions and touch-target sizing.
- Map error codes to translated messages.
- ICU number formatting.
- A Chinese glossary with one term per concept.
- Time zones on timestamps.

## Narration

- Digit grouping and money decimals in template narration, with the numeric lock accepting grouped
  numbers in both languages.
- Units per language.
