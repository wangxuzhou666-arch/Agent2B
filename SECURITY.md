# Security policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting on this repository: open the **Security** tab, then
**Report a vulnerability**. Please do not open a public issue or pull request for a
vulnerability. Say what you ran, what you expected and what happened; a failing test or a
request that reproduces it is the most useful report.

## Supported versions

The latest release. Fixes are made on `main` and released; older releases are not patched.

## Threat model

twotrack's thesis is that a language model routes and narrates while code computes. The
security claims follow from it, and so do their limits. This section says what the code does,
with the file or test that shows it, and what it does not do.

It runs a request on one of two tracks. A **fixed flow** is a registered tool or workflow that
the router picks and deterministic code runs. **Exploration** is open-ended analysis that no
fixed flow covers: an agent loop plans it (Pi in a restricted Node worker, or the built-in
loop), and it is off unless `TWOTRACK_ALLOW_OPEN_TASK` is set. The tracks share plugins and one
contract: every call either of them runs goes through the pipeline (lookup, authorization,
preconditions, validation, grant, execute, audit; `core/pipeline.py`), so neither a permission
check nor an audit record is something a track can skip for a call it runs. A call that
exploration refuses before it gets there (an unknown or unexposed tool, unusable arguments, a
spent budget) never reaches the pipeline; it is traced and audited by the gate in front of it
([below](#exploration-the-boundary)).

### What the model sees, and what an injected instruction can do

| A model receives | Where |
|---|---|
| The question, after the egress guard replaced the current values of restricted text fields (three characters or more) with `[REDACTED]`. If those values cannot be loaded, no call is made. | `core/guards/egress.py`, `providers/base.py` |
| For routing: cards of the targets this user may run right now (name, kind, English description, route hint, up to three examples, params schema). Nothing else. | `core/routing/llm_arm.py` |
| For narration: the narrator view of one result: aggregates, row count, column names, scope, message, language, and a label per aggregate. No row. | `ToolResult.narrator_view`, `core/narration.py` |
| In the chat fallback: the descriptions of the candidate targets and the question. | `core/graph/builder.py` |
| In exploration: the question (scrubbed as above), the tools the host exposes (name, description, params schema), and for each call it made the narrator view of the result, or the problem. No row. Every call goes through the pipeline. | `core/explore/`, `core/agent_loop.py` |

Two details matter for injection. First, `message` and `scope` are free text that a tool
writes. The built-in tools put identifiers, ISO dates and filter labels in them; `create_po`'s
message also names the chosen supplier, a public field, so a supplier name can reach the
narrating model after an approved write (`plugins/supply_chain/tools.py`). A plugin author
decides what else goes there. Second, numeric restricted values are not redacted from text;
they are protected because they travel in rows, which no model receives in the console or in
exploration (over MCP with `--rows`, restricted fields are removed from the rows).

What an instruction hidden in data (a cell in an imported file, a name in the database) can do:
in the console, nothing beyond the case above, because no row reaches a model. With
`twotrack mcp --rows`, up to `--max-rows` rows, restricted fields removed, go to the MCP
client's model; that is opt-in ([MCP.md](docs/MCP.md)).

What an instruction typed into the question can do: it is the user's own text. A model that
follows it can pick another target, but only one in the candidate set (a name outside it is
dropped), its params are validated by the target's params model, and a write still waits for the
requester's own approval. A write reached without strong evidence is first turned into a
one-option confirmation ([ADR 0002](docs/decisions/0002-two-routing-arms-and-a-reconcile-matrix.md)).

What the lock guarantees and does not: every sentence of model text with a number the result does
not back is removed (`core/guards/numeric_lock.py`). It does not read spelled-out English
numbers, bare years 1900-2100, ordinals, dates, clock times or identifiers such as `SKU-123`, and
it does not judge prose: a model can still write misleading words around correct numbers. The
console renders every answer as Markdown with raw HTML dropped, and shows every link as text,
not as a link, whoever wrote the answer: a `template` answer repeats a tool's message and scope,
which can carry stored data or a param a model proposed
(`web/src/components/common/markdown.tsx`, `markdown.test.tsx`).

What reaches a model from stored records is fenced, except over MCP. `wrap_untrusted`
(`core/guards/egress.py`) puts a string inside a boundary with a fresh nonce and rewrites
anything that looks like the boundary's markers. `fenced_view` (`core/narration.py`) applies it
to the `message` and `scope` of a result, and `fenced_view` has two callers: the narrating
provider (`providers/openai_compat.py`) and the tool bridge that answers every exploration call
(`core/explore/calls.py`), for both runtimes (`tests/server/test_untrusted_fence.py`). A
problem the pipeline reports for an exploration call is fenced too (`fenced_problem` in
`core/explore/calls.py`), since a plugin may word it from stored data; a refusal made before the
pipeline is the platform's own wording about the model's own call and is sent unfenced. The MCP
export does not fence; see Known limits.

### The routing invariant: a question, a negation or an exclusion never yields a direct approval card

A request that reads as a question ("did we order 200 of SKU-… last week?"), negates a write's
phrase ("don't order …") or excludes a value ("order from anyone except SUP-…") never goes
straight to the approval card of a write. The rule arm caps a hit whose target changes data (a
write, or an external call) at weak evidence on **every** path to strength: a matching example,
two keywords, or complete params. A model that agrees with such a write does not make it strong
again (`core/routing/reconcile.py`). A weak write is first turned into a one-option confirmation;
only after that does the pipeline issue its grant. Reads keep their strength, and an excluded
identifier is not extracted as a param. The trace records what held the request
(`rule.question`, `rule.negated`, `rule.excluded`, `rule.capped`). Evidence:
`tests/routing/test_complete_writes.py`, `test_interrogative.py`, `test_negation.py`,
`test_exclusion.py`, `test_reconcile_matrix.py`.

What this is not: the authorization control. The control is the grant ([Writes](#writes)); the
invariant removes a shortcut to it. The checks read words, in English and Chinese. Where they do
not recognize a phrasing, the request can still reach an approval card without the confirmation
step, and nothing runs until the requester approves it. The card shows the exact params, and a
model can propose a param that the question excluded, so the card is what to read. The phrasings
known to be missed are listed in the [ROADMAP](ROADMAP.md).

### Exploration: the boundary

Exploration plans with a language model, so it is the part of the system where a model decides
what to call. The boundary below is what keeps that decision inside the same contract as a fixed
flow. The budgets and the write proposals are tested with the real Pi worker driven by the mock
model or by a scripted one (`tests/explore/`, and `make smoke` against the running stack). The
worker's restrictions are checked twice: as the command line, working directory and environment
the host launches it with (`tests/explore/test_fake_worker.py`,
`tests/explore/test_selection.py`), and by what a Node process started with those flags and that
environment can and cannot do (`tests/explore/test_lockdown.py`: it is refused a read outside the
harness directory, writes in its working and temporary directories, child processes and worker
threads with `ERR_ACCESS_DENIED` and native addons with `ERR_DLOPEN_DISABLED`, and it sees
exactly the allowlisted environment names). Those restrictions are Node's permission model, a safeguard and not a sandbox; "What the worker
can do" says what that leaves. Nothing here rests on a run with a real model.

**What a run may call.** Only tools that are explicitly allowed. A run is offered a tool or
workflow only if its plugin declares it `explorable`, the requester's roles may run it and its
preconditions are met, and the deployment's allowlist `TWOTRACK_EXPLORE_TOOLS` admits it. Unset,
the allowlist admits every such target except an external one (a call that sends data out of the
deployment): an external target is offered only when it is named there, it must declare that it
requires approval, and each of its calls still waits for a person. A name in the allowlist that
no plugin provides, or that is not explorable, stops the API at start-up. A tool the model names
outside the exposed set is refused as an unknown tool, traced and audited. Every parameter
schema that leaves for a model (the routing cards, the tool lists of both runtimes, the MCP tool
list) keeps standard JSON Schema keywords only, so the annotations that core reads from a
plugin's parameter model never reach a model.

**What a run may change: nothing, until a person approves.** A write that a run plans is only a
proposal. The pipeline issues the usual one-time grant, the host answers the call with a stop
instruction and aborts the worker, and the run returns `needs_confirmation`. Nothing executes
until the requester approves it through the same approval route as any other write. The grant is
bound to the user, the target and the validated params, so an approval whose params changed
after the model asked is refused
(`tests/explore/test_pi_runs.py::test_an_approval_whose_params_changed_after_pi_asked_is_refused`).
The model is never given the grant. Pi checks a call's arguments against the declared schema
before the call is forwarded, and the host validates them again against the params model: the
pipeline's validation is the gate, Pi's check a first filter.

**What is recorded when a call is refused.** A call refused before it reaches the pipeline (a
tool outside the exposed set, arguments that are not a JSON object or that Pi's schema check
refuses, a call past the budget) is traced as a `gate` refusal and written to the audit log, in
the Pi runtime and in the built-in loop. A call the pipeline refuses (denied, invalid
parameters, a failed precondition) is audited by the pipeline. If a refusal's record cannot be
written, the run stops (fail closed).

**The hand-over.** Exploration is the route of last resort. Whenever the router sends a request
to exploration it records a `handover` step with the reason (`intent_not_covered`) and its
evidence: what each arm said, the best semantic score against its threshold, and how many
candidates the user has. The explore node then runs a guard before any runtime starts, and
refuses (stop reason `handover_refused`, traced and audited) a request that names an explicit
target, that already reached a terminal outcome on a fixed path (denied, invalid, blocked, an
error, a spent budget, a pending approval, a result, or an audit record already written) or
that was never handed over. No graph edge leads into exploration except from the router today;
the guard keeps a future edge from turning a refusal into a second try by a model.

**What the worker can do.** With `TWOTRACK_EXPLORER=pi`, one process per run, started as
`node --permission --allow-fs-read=<the harness directory>`:

- File-system reads are limited to the worker's own install (`harness/pi`). No file-system
  writes, no child processes, no worker threads, no native addons.
- The network is not restricted. It can reach any address its container can ([Known
  limits](#known-limits)).
- Its working directory and `HOME` are a fresh, empty temporary directory, deleted after the run.
- Its environment is an explicit allowlist: `HOME`, `LANG`, and the key variable when a model
  needs one. Nothing else is inherited.
- The key (`TWOTRACK_LLM_API_KEY`) reaches the worker only through that variable. It is in no
  protocol line, trace, log or exception; the worker redacts it from what it writes and so does
  the host (`test_the_key_never_leaves_the_worker_environment`), each in three forms (as it is,
  JSON-escaped and URL-quoted). A key shorter than 8 characters is treated as a placeholder and
  not redacted (`MIN_SECRET_CHARS` in `pi.py` and `worker.mjs`).
- The packages are pinned: `@earendil-works/pi-agent-core`, `pi-ai` and `pi-telemetry` at exactly
  one release, with a committed lockfile and install scripts skipped (`npm ci --ignore-scripts`).
  The worker reports the versions it loaded in its `hello` line, and the host refuses a worker
  whose versions differ from the pins, at start-up and again at the start of every run.
- Start-up fails closed. A missing or too-old Node, a missing worker, packages other than the
  pinned ones, or a worker that does not report the pinned versions stops the API. Nothing falls
  back to the built-in loop.
- The worker runs in its own process group. When the run ends, times out or the worker
  misbehaves, the host closes its stdin and kills the group if it has not exited after a short
  grace (at once after a protocol violation). Its stdout carries protocol lines only: JSON, one object per line, size-capped and
  schema-checked, and a line that breaks the rules stops the run. Its stderr is kept up to a cap
  and logged, redacted, only when a run fails.
- With no model configured, the endpoint the worker talks to is bound to loopback for that run
  and accepts only the run's random bearer token.

**What these restrictions are worth.** Node documents its permission model as a "seat belt" for
trusted code that gives no security guarantee in the presence of malicious code, and its
security releases have included permission-model bypasses (CVE-2025-55130, a relative-symlink
bypass of the file-system permissions, is one). Here it stops the worker's own mistakes and
limits what stray code can do on the machine; it is not a boundary against hostile code. The
residual threat is a compromised dependency among the packages installed in `harness/pi`: it
would run in the worker with the worker's environment and an unrestricted network, and could send
out what the worker holds, which is the question, the tool results (narrator views, never rows)
and the model key when one is set. What stands against that is the exact pins, the committed
lockfile, `npm ci --ignore-scripts`, a current Node security release and an egress control
outside the application ([Known limits](#known-limits)).

**What a run may spend.** Budgets, for either runtime: model turns, tool calls (a refused call
counts) and a wall clock, set by `TWOTRACK_EXPLORE_MAX_TURNS`, `TWOTRACK_EXPLORE_MAX_CALLS` and
`TWOTRACK_EXPLORE_TIMEOUT_SECONDS` (defaults 4, 6 and 120). Two more apply to the Pi runtime:
`TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS` (default 30), how long the worker waits for the host to
answer one call, and `TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS` (default 20), how long the
worker may take to say hello. Pi 1.2.0 has no turn or step limit of its own, so the budgets are
ours: the worker enforces turns and calls, and the host enforces the call budget and the wall
clock again. A call forwarded past the call budget is refused (traced and audited) and the host
sends the worker an abort; past the clock it sends an abort too. When the run ends, the host
closes the worker's stdin and kills the process group if the worker has not exited after a short
grace. The built-in loop enforces the same three budgets itself and checks its clock between
steps. A spent turn or call budget ends the run with that stop reason;
a run past its clock is a failure, and so is a worker that does not come up in time. The run's
clock starts when the worker says hello. When a run ends on a spent budget, a failure or a
refusal, the console says so in a single line under the answer: amber for a spent budget, red
for the rest.

### Plugin trust model

An in-process plugin is trusted code, not a sandbox. Its handlers run inside the server process
with the server's rights: they can read the database file and the environment and make network
calls. The registry checks what a plugin declares, not what its handlers do, so install only
plugins you have read.

What the core does enforce is the declaration. Every tool and workflow states `roles`, `effect`,
`approval`, `outputs` and `explorable`, with no defaults, and the server does not start when a
plugin breaks the core's policy:

- **A core-owned role/effect table** (`ROLE_EFFECTS`, `core/contract.py`) lists the effects a role
  may be given: a viewer may run reads, an operator and an admin any effect. A tool's `roles` can
  narrow that and never widen it, and the role set is fixed, so installing a plugin grants nobody
  any permission they did not have. The pipeline checks the same table on every call.
- **Approval follows effect.** A write must declare that it requires approval and a read that it
  does not; an external call says which.
- **Outputs are enforced.** A result may carry only the aggregate keys and columns the tool
  declared. An undeclared key is an error, a write is rolled back and nothing of the result is
  returned.
- **`explorable` is enforced.** A tool that declares itself not explorable is never offered to a
  model planning on its own, whatever else allows it, the deployment's allowlist included
  (naming such a tool there stops the server at start-up); an explorable external tool must
  require approval.

Evidence: `tests/core/test_tool_contract.py`, `tests/pipeline/test_pipeline_contract.py`,
[docs/ADD_A_TOOL.md](docs/ADD_A_TOOL.md).

### Writes

A write never runs without a grant ([ADR 0005](docs/decisions/0005-one-time-write-grants.md)).
The grant is a random token whose SHA-256 is stored in `confirmation_grant`, bound to the
SHA-256 of the canonical JSON of (user, target, validated params), valid for 15 minutes, and
consumed by one conditional `UPDATE`, so two concurrent approvals cannot both succeed
(`core/grants.py`). It stays consumed even if the run that follows fails. Approving a grant
that already ran is refused with 409 `grant_used`, and so is a second approval that arrives while
the first is still running; an expired one is refused with 410, someone else's with 403, and each
of these refusals by the approvals endpoints is an audit record under the grant id
(`tests/server/test_approval_refusals.py`, `tests/pipeline/test_pipeline_grants.py`). The import
commit endpoint's refusal of an expired preview is not recorded
([below](#audit-what-fails-closed-what-does-not)).

**Accepted risk: pending grant tokens are stored in plaintext.** While a request waits for
approval, the server keeps its token in the `pending_request` table (`server/approvals.py`;
`tests/server/test_query.py` reads it back). The browser never sees it. A row leaves the table
when it is approved or rejected, or at the next insert after it expired. The reasoning for not
hashing it: the approval replays the request with the stored token, because the browser never
holds one, so the server cannot keep only a hash, and an encrypted copy would need a key kept
beside the same file; and anyone who can read the database file can already change it,
including the audit log and the user table. A stolen token is also bounded: 15 minutes,
single use, and bound to one user, one target and one set of params. The same table holds staged
upload bytes: at most ten imports per user across staged and awaiting states together.
A newer upload evicts the oldest import and revokes its grant; approve, reject or expiry also
removes it. Ordinary question approvals do not count toward this import cap.

### Model failures are visible

A configured model that fails is never mistaken for one that answered, nor for the mock. A failed
call (an HTTP error, a timeout, a reply that cannot be parsed) degrades the answer, to rules and
lexical search for routing and to the template for text, and the answer says so: `model_errors`
names the call, the error class and the HTTP status, never the message or the request; the trace
records it; the console puts a warning line under the answer; an MCP tool result carries it; a
benchmark run that saw one ends as degraded. A provider that is half configured stops the API at
start-up rather than falling back to the mock. In exploration, a worker that crashes, a timeout,
a malformed protocol line, version drift, a failed model call and a failed audit write each end
the run as an error with deterministic text; nothing falls back to the other runtime. Evidence:
`tests/server/test_model_failures.py`, `tests/graph/test_model_failures.py`,
`tests/explore/test_pi_runs.py`, [ADR 0009](docs/decisions/0009-honest-zero-key-mode.md).

### Sessions

Sign-in returns an HS256 token (`sub`, `iat`, `exp`, a random `jti`; default lifetime 8 hours,
`TWOTRACK_TOKEN_TTL_SECONDS`). Roles are read from the user table on every request, so disabling
a user takes effect at once. `POST /api/auth/logout` puts the token's `jti` on a deny-list in
the database, which survives a restart and is pruned once the token would have expired anyway; it
revokes that token only (`server/auth.py`, `tests/server/test_auth.py`,
`tests/server/test_logout.py`). Passwords are
scrypt-hashed with a per-user salt, at least 10 characters; failed sign-ins are throttled per
client address and user name (5 in 5 minutes, in the memory of one process). Attempts are
reserved before password hashing, so parallel guesses cannot bypass the cap. Locked entries
are not evicted to make room for new keys; if all entries are locked, new keys are refused.

`twotrack users disable NAME`, `enable NAME`, `roles NAME` and `passwd NAME` manage existing
accounts. Disabling or changing roles takes effect on the next request. Re-enabling a user
restores their unexpired tokens; changing a password does not revoke existing tokens.
Behind a proxy, configure uvicorn's `FORWARDED_ALLOW_IPS` for the trusted proxy address, never
`*`: otherwise a client can rotate a forwarded address to evade throttling. Without trusted
forwarding, users behind the same proxy share the address-based throttle.

The console sends bearer tokens in the Authorization header with `credentials: "omit"`, not
cookies. Sign-out clears the local session before best-effort server revocation; if that call
fails, the token remains valid until expiry.

The signing secret is the file `jwt.secret` in the data directory. It is generated at the first
start (48 random bytes) and has no default. The file is created atomically with mode 0600
(`core/secrets.py`). The data directory is created with mode 0700 (`prepare_data_dir` in
`runtime.py`), but that mode applies only when the directory is created: the umask can narrow it,
and a directory that already exists keeps the permissions it has, so check one you made yourself.
The database file is set to mode 0600 at every start. If the secret file exists and group or
others have any access to it, or it holds fewer than 32 characters, the server refuses to start
(`core/secrets.py`, `tests/guards/test_secret_file.py`); that mode check and the database
chmod are skipped on Windows. A file its own user cannot read fails when it is read and the
server does not start. Replacing the file invalidates every token signed with the old one.

### Demo mode

`TWOTRACK_DEMO_MODE=1` signs anyone who can reach the API in as an admin, with no password, and
serves `/docs` and `/openapi.json`. It exists for local evaluation. Demo tokens are refused when
the mode is off. `twotrack serve` binds 127.0.0.1 by default and warns when demo mode meets
another host; [docker-compose.yml](docker-compose.yml) publishes both ports on 127.0.0.1 only.
In demo mode, and whenever `TWOTRACK_ALLOWED_HOSTS` is configured, the API also rejects an
unknown, missing, repeated or malformed Host header. Loopback names are allowed; additional
host names must be listed explicitly in `TWOTRACK_ALLOWED_HOSTS` (comma-separated, no ports).
This blocks DNS rebinding against passwordless demo login (`tests/server/test_hosts.py`).

Before exposing the stack to anyone else: remove `TWOTRACK_DEMO_MODE`, create accounts
(`twotrack users add NAME --role admin`; the compose header shows the `docker compose run`
form), set `TWOTRACK_CORS_ORIGINS` to the console's real origin, and put an authenticating TLS
proxy in front. The header of `docker-compose.yml` has the details.

### Uploads

Before any parser runs, `core/guards/upload.py` refuses: an empty file; more than
`TWOTRACK_MAX_UPLOAD_BYTES` (2 MiB); a file whose type `sniff_file_type` refuses; and an xlsx
with more than 200 entries, more than 20 MiB declared uncompressed, an entry over 1 MiB with a
ratio above 200, encryption, or missing workbook parts. `sniff_file_type` reads the bytes, and
the name can only count against them: a file that starts like a zip is taken for an xlsx; a
legacy xls, a known binary signature (PDF, PNG, GIF, JPEG, gzip, an executable, RAR, 7z, an
empty zip), a NUL byte in the first 8192 bytes (binary data or UTF-16 text) and a name ending in
`.xlsx` over bytes that are not a zip are refused; any other text is read as CSV. So the type
check does not tell a table from other text: a file of the wrong shape is refused later, for
missing required columns. Archive entries are also inflated in bounded chunks: contents larger
than their declared size, unsupported compression and invalid archives are refused. The reader
limits nonempty data rows to 2,000, total rows read (including blanks) to 4,001, and header width
to 1,024 columns. A workbook's declared dimensions are not trusted (`core/entities/importer.py`).
Import rejects columns the role cannot see and updates that would change an existing record's
category. An update preserves fields absent from the uploaded file. The rejection names the
submitted restricted header, revealing that the alias exists, but not its stored value. Each refusal is an audit record with the reason. The HTTP body cap in front of
it (upload limit plus 64 KiB) cuts off a larger body before the application reads it and writes
no audit record (`tests/server/test_import.py::test_huge_upload_is_cut_off_before_it_is_read`).

### The console's Content-Security-Policy

`web/next.config.ts` sets `default-src 'self'`, `connect-src` for the console's own origin and the API origin only,
`frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'self'`, no
third-party origin, plus `nosniff`, frame denial, `no-referrer`, same-origin opener and resource
policies, and HSTS in production builds (`web/test/next-config.test.ts` pins each directive and
header for both builds). Two directives still carry `'unsafe-inline'`:
`script-src`, because the App Router's inline bootstrap scripts and the theme provider's no-flash
script cannot carry a nonce in a static header, and `style-src`, for inline styles that the
console's components (`style=` in four of them) and its libraries emit. `'unsafe-eval'` is
added only by the dev server. The cost: if a script were ever injected into the console, the
policy would not stop it, and the bearer token sits in `sessionStorage`. The first line of
defence is that the console renders no raw HTML (no `dangerouslySetInnerHTML`, which lint
forbids with `react/no-danger`; every answer is sanitized as above). A CSP with per-request nonces and `report-to` is not built; it is listed in
the [ROADMAP](ROADMAP.md). API responses carry `default-src 'none'`; only the demo-mode `/docs`
page differs, with its script pinned by hash and Swagger UI assets loaded from jsDelivr.

### Audit: what fails closed, what does not

The log records pipeline runs (a refusal by the pipeline included: denied, invalid parameters, a
failed precondition), import previews, refusals and commits, approval decisions (rejections and
the 403, 409 and 410 refusals included) and the calls that exploration refuses before the
pipeline, each with the exceptions below. It is not a record of everything that happens.

Fails closed ([ADR 0008](docs/decisions/0008-fail-closed-audit.md)): a write whose `started`
record fails does not run; its `ok` record shares the data change's transaction, so a failed
record rolls the change back; a read whose record fails is withheld; a confirmation whose
record fails revokes its grant; an import's preview, refusals and commit are recorded the same
way (`tests/pipeline/test_pipeline_audit_failclosed.py`). A call that exploration refuses before
the pipeline (an unknown or unexposed tool, unusable arguments, a spent budget) is recorded like
a pipeline refusal (status blocked or invalid, route source `agent`), and a run whose refusal
cannot be recorded stops.

Best effort, logged as a warning, answer unchanged: the decision record of a clarification or
chat answer, the record of a request the explore node refused to start (`handover_refused`;
nothing ran), and the narrate and respond steps appended after a run (`core/graph/builder.py`),
also on the approval path (`server/routes/approvals.py`). In the code two more are best effort:
the `error` or `invalid` record of an execution that already failed (`core/pipeline.py`) and
of a failed import commit (`core/entities/importer.py`); the data change was rolled back.

Not recorded at all: sign-ins and sign-outs, reads through the Data window
(`GET /api/entities/...`), user changes made with the CLI, and an import commit refused as
expired (`POST /api/entities/{id}/import/commit`: 410 `approval_expired`, or 404 once the staged
upload is gone; approving an expired import through `/api/approvals/{id}/approve` is audited).
The log is an ordinary SQLite table: not tamper-evident, and not a complete record (the events
above leave none).

### Known limits

Each of these is a gap in what the code does today, not a design choice.

- **The worker's network is not restricted, and its restrictions are not a sandbox.** On Node 22
  and 24, `node --permission` does not cover the network, and the API container has open egress:
  this stack does not restrict its outbound traffic. (Node 25 and later extend the permission
  model to the network, and the host passes no flag to allow it; the image and CI use Node 24.)
  The worker is given no tool of its own (every call is forwarded to the host), but the
  permission model is a "seat belt" for trusted code, and Node's security releases have fixed
  bypasses of it. The residual threat is a compromised dependency in `harness/pi`: it could use
  the open network to send out what the worker holds, which is the question, the tool results
  and the model key if one is set. Keep Node on a current security release (the image pins
  `node:24.22.1`), keep the exact pins and the lockfile, and put a firewall or an egress proxy
  outside the stack that lets the container reach only the model endpoint.
- **Memory and process limits belong to the compose file.** The budgets bound a run's turns,
  calls and time, not its memory or the number of processes. `docker-compose.yml` sets
  `mem_limit` and `pids_limit` on `api` and `web`, per container: they bound all running
  questions together, not one run or one user. A deployment that does not use that file has to
  set its own limits. The container's root file system is read-only, its capabilities are
  dropped and `/tmp` is a `noexec` tmpfs.
- **MCP results are not fenced.** The console and both exploration runtimes put the `message`
  and `scope` of a result inside an untrusted-content fence. The MCP export returns them (and,
  with `--rows`, rows) to the client's model as plain text.
- **Audit gaps.** Sign-ins and sign-outs, reads through the Data window, user changes made
  with the CLI and an import commit refused as expired leave no audit record.
- **Routing reads words.** The checks that hold a question, a negation or an exclusion back from
  an approval card miss some phrasings; see the [ROADMAP](ROADMAP.md).

### Out of scope

- Anyone with access to the host, the data directory, the database file or the plugin path.
  Plugins are trusted Python that runs inside the server process (see the plugin trust model
  above). `twotrack mcp --user NAME` acts as that account without a password, so it belongs on
  a machine where the person starting it may act as that user.
- The security of the model endpoint the operator configures. What it does with the scrubbed
  questions and narrator views it receives is the operator's to judge.
- Denial of service by a signed-in user. There are size, row and body caps and budgeted
  exploration runs, but no per-user rate limits, and one SQLite connection means one writer.
