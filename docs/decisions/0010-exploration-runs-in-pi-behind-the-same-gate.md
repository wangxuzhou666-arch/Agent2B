# ADR 0010: Exploration runs in Pi, behind the same gate

## Status

Accepted. Where the pieces live: the tool declarations and the core-owned role and effect table
(`core/contract.py`), the pinned worker (`harness/pi`), the runtime and its shared call gate
(`core/explore/pi.py`, `core/explore/calls.py`), the image that carries Node and the worker
(`Dockerfile`), the exposed-tool filter and its allowlist (`core/explore/exposure.py`), the
hand-over and its guard (`core/explore/handover.py`), audited refusals in both runtimes
(`core/explore/calls.py`), model-facing schemas (`core/model_schema.py`), the run's clock
starting at the worker's hello (`core/explore/pi.py`), and the answer and the console saying why
a run stopped short (`core/agent_loop.py`, `web/src/lib/trace.ts`).

## Context

Fixed flows cover the questions a deployment knows about. A person will also ask for analysis no
flow was written for: compare two reports, follow one finding into another. A model that plans its
own tool calls can answer such a request, and it is also the first place where a model, not a
rule, decides what runs. The question is how to give it room without giving it the numbers, the
permissions or the irreversible effects.

The thesis stays the same ([ADR 0001](0001-code-computes-the-model-narrates.md)): models route and
narrate, code computes. Exploration extends it to planning: the model plans, and code decides
whether each step may run, computes it, and records it.

## Options considered

1. **A full agent harness for everything**: the model owns the loop, the tools and the session.
   - For: the least code of our own; any request is answerable, known or not.
   - Against: numbers, permissions and irreversible effects sit with the model. The trace is a
     transcript, not the ladder of gate decisions, so a run cannot be replayed and checked. An
     approval is advice a model can route around, not a grant the server checks.
2. **Only the hand-written loop** (`core/agent_loop.py`).
   - For: no Node, no subprocess, small enough to read in one sitting.
   - Against: it is the only loop our boundary is ever tested against, and every improvement to
     tool calling is ours to write.
   - Kept anyway, as the `builtin` explorer: the default that needs nothing installed, and the
     baseline the Pi path is compared with in tests, under the same gate and the same budgets.
3. **Pi's SDK session (`createAgentSession`) or `pi --mode rpc`.**
   - For: an embedding that Pi documents.
   - Against: both load Pi's built-in read, bash, edit and write tools, its configuration, AGENTS.md
     files and extensions by default. The embedding would start from everything and subtract.
     Rejected: the host must start from nothing and add only its own tools.
4. **The `Agent` of `@earendil-works/pi-agent-core` in a worker subprocess that speaks JSON Lines
   over stdio** (chosen). The worker (`harness/pi/worker.mjs`) declares only the tools the host
   sends in its `start` line. It has no tool of its own and runs no business logic.
   - For: an agent loop that is not ours, with nothing in it but host tools; a process boundary
     the operating system can restrict; one narrow, schema-checked protocol.
   - Against: see Consequences.

## Decision

- **Two tracks.** Fixed flows are routed by LangGraph and run by deterministic code, which owns
  the numbers, permissions, validation and irreversible effects. Exploration is the one place a
  model plans. `TWOTRACK_EXPLORER` picks its runtime, `builtin` (default) or `pi`; nothing falls
  back from one to the other. Both share plugin registration, the pipeline, the permission
  contract and the audit contract.
- **The boundary.**
  - A known step, and every write, goes to a fixed flow. Only "intent not covered" may hand over
    to exploration: both routing arms silent and no BM25 hit ([ADR 0002](0002-two-routing-arms-and-a-reconcile-matrix.md)),
    with `TWOTRACK_ALLOW_OPEN_TASK` set and at least one candidate for the asker. The router records a `handover` trace step with reason
    `intent_not_covered`.
  - A denial, a validation failure, a spent budget or an error never hands over. The explore node
    checks this itself before any runtime starts: for an explicit target, any earlier terminal
    outcome or a request the router never handed over it refuses, with stop reason
    `handover_refused`, so a refusal is never a second try by a model.
  - The explorer sees only the tools exposed to it: `registry.candidates(ctx)` (the asker's
    roles, preconditions met), filtered to targets that declare `explorable=True`, intersected
    with the optional `TWOTRACK_EXPLORE_TOOLS` allowlist (unset: every explorable target but an
    external one). An `external` target is exposed only when that list names it. The model sees aggregates through the narrator view, never rows.
  - A write is a proposal. Its call gets a one-time grant from the pipeline, the worker is told
    to stop, and the run ends as `needs_confirmation`; nothing executes until the requester
    approves.
- **Every call meets the same gate** (`core/explore/calls.py`, `run_tool_call`): lookup,
  authorization, preconditions, parameter validation, grant, execution, audit, with route source
  `agent`. A call refused before the pipeline (an unknown or unexposed tool, unusable arguments
  (not a JSON object, or refused by Pi's own schema check), a spent budget) is traced as a
  `gate` step and audited in both runtimes, and the run goes on only within its budget. The
  schemas a model is shown keep standard JSON Schema keywords only, so the annotations that core
  reads from a param stay with the host.
- **The plugin trust model.** An in-process plugin is trusted code, not a sandbox
  ([ADR 0006](0006-categories-extend-a-root-with-attrs.md)). What it declares is checked, not
  trusted: `roles`, `effect`, `approval`, `outputs` and `explorable` are required, and the
  registry refuses to start on a combination the core does not allow (a write that does not
  require approval, a read that does, an explorable external tool that does not). They are
  enforced again on every call: the role check, the grant, and the comparison of each result with
  the declared `outputs`. A core-owned table (`ROLE_EFFECTS`, `core/contract.py`) says which
  effects a role may run (viewer: read; operator and admin: read, write, external). A target can
  narrow it and never widen it, so installing a plugin grants no one a permission.
- **Data minimization.** A tool declares the aggregate keys and columns its result may carry; an
  undeclared key is an error and nothing of that result is returned. The model sees only the
  fenced narrator view of a result. The task text and every reply pass the egress scrub, and the
  strings tool code wrote from data arrive between `<<<UNTRUSTED` and `<<<END UNTRUSTED` lines
  that the prompt tells the model to treat as information only.
- **Human approval.** A grant is bound to the user, the target and a digest of the validated
  params, and is valid for 15 minutes ([ADR 0005](0005-one-time-write-grants.md)). Approving a
  second time answers 409; params that changed after the request are rejected; an approval
  replays the stored request through the pipeline as its requester.
- **The worker is restricted** (`core/explore/pi.py`; Consequences says what that is worth):
  - it starts as `node --permission --allow-fs-read=<harness dir>`, which grants no file writes, no
    child processes, no worker threads and no native addons, and its stdout carries protocol lines only, each at most 1 MiB and
    checked against a schema that refuses unknown fields;
  - its working directory and `HOME` are a fresh empty temporary directory, deleted after the
    run, and its environment is an allowlist (`HOME`, `LANG`, and `TWOTRACK_LLM_API_KEY` when a
    model needs it); the key reaches the worker only through that variable, no protocol line
    carries it, the worker redacts it from everything it writes and the host from what it logs;
  - one process per run, in its own process group; when the run ends, times out or breaks the
    protocol the host closes its stdin and kills the group if it has not exited after a short
    grace (at once after a protocol violation);
  - the three Pi packages are pinned to one exact version in `harness/pi/package.json`, the worker
    reports the installed versions in its `hello` line, and both the worker and the host refuse
    anything else;
  - start-up fails closed: a missing Node 22.19 or newer, a missing worker, packages that are
    not the pins, or a worker that does not say hello stops the server (`ExplorerUnavailable`).
- **Budgets are twotrack's code, not Pi's.** Turns (`TWOTRACK_EXPLORE_MAX_TURNS`, 4), tool calls
  the model asks for, refused ones included (`TWOTRACK_EXPLORE_MAX_CALLS`, 6) and the run's wall
  clock (`TWOTRACK_EXPLORE_TIMEOUT_SECONDS`, 120) apply to both runtimes. For Pi, one host answer
  to a tool call (`TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS`, 30) and the worker's start-up
  (`TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS`, 20) have limits of their own. The built-in loop
  enforces the budgets itself and checks its clock between steps; for Pi the worker enforces
  them and the host enforces the call budget and the clock again. A spent turn or call budget
  ends the run as `budget` with stop reason `max_turns` or `max_tool_calls`; a run past its clock
  ends as an error with stop reason `timeout`, and so does a worker that does not come up in time
  (`worker_unavailable`). The wall clock starts when the worker says hello. Nothing falls back
  to the built-in loop.

## Consequences

- **Two paths to wire and keep in step.** Every change to the call gate, the budgets or the trace
  facts touches both runtimes, and the tests run both.
- **Flows and contracts to maintain.** A deployment keeps its fixed flows for what it knows, and
  every tool declares `approval`, `outputs` and `explorable`. Exploration does not replace them.
- **A Node runtime in the API image**, 22.19 or newer. The image carries the `node` binary and the
  worker's pinned install, and nothing else of Node.
- **One process per exploration run.** Start-up time and memory are paid per question.
- **Pi's API changes inside minor versions**, so the packages are pinned at 1.2.0 and the
  lockfile fixes every transitive version. Upstream 1.3.0 fixes two tool-argument integrity
  issues, #10897 (a malformed final tool-call JSON ran with its parsed prefix) and #10868 (an
  invalid `\u` escape dropped fields), and has a regression of its own, #11095 (a valid JSON
  object followed by trailing characters is rejected). The pin moves once 1.3.0 has been
  evaluated here.
- **`node --permission` does not restrict the network.** The worker can reach any address its
  container can. Limiting egress to the model endpoint needs a firewall or proxy outside the
  application; the compose file does not provide one.
- **`node --permission` is a safeguard, not a sandbox.** Node documents its permission model as a
  "seat belt" for trusted code that gives no security guarantee in the presence of malicious
  code, and its security releases have included permission-model bypasses. Here it stops the
  worker's own mistakes and limits what stray code can do on the machine; it is not a boundary
  against hostile code. The realistic threat it does not stop is a compromised dependency among
  the packages installed in `harness/pi`: it would run in the worker with the worker's
  environment (the model key, when a model needs one) and an unrestricted network. What stands
  against that is the exact pins, the lockfile, `npm ci --ignore-scripts` and an egress control
  outside the application. The tests check the command line and the environment the host
  launches the worker with (`tests/explore/test_fake_worker.py`,
  `tests/explore/test_selection.py`).
- An exploration answer is bounded by the declared aggregates, like any other
  ([ADR 0001](0001-code-computes-the-model-narrates.md)); a question that needs a figure no tool
  computes is answered with what the tools do show.
- **The explorer cannot discover an id.** It sees each tool's parameter schema and, for each call it
  makes, the aggregates, row count, column names and fenced `scope` and `message` of the result,
  never a row. So a SKU id reaches it only if the request names it or a tool writes it into a
  `message` or `scope` (no built-in tool writes a SKU id the call did not already carry). The request has to name the
  SKU, as the documented exploration case does ([docs/EVIDENCE.md](../EVIDENCE.md)), and a run
  cannot chain one tool's rows into another's input. This is the price of the data-minimization
  decision above, and a deliberate, strict choice for v0. A possible change is
  contract-declared model-visible id fields: a tool's `outputs` would declare which id columns the
  model may see ([ROADMAP](../../ROADMAP.md)).
- The zero-key mock does not plan, except for one scripted request
  ([ADR 0009](0009-honest-zero-key-mode.md)). With no model configured the worker talks to a per-run endpoint
  on 127.0.0.1 backed by the mock, whose answer to any other request says no model is configured
  ([ADR 0009](0009-honest-zero-key-mode.md)).

## Evidence

Each tier is listed on its own; none stands in for another.

- **Built-in loop with the mock model.** `tests/graph/test_workflow_and_loop.py` (tool calls go
  through the pipeline and the model sees views, a write stops at `needs_confirmation`, the turn
  and call budgets stop the loop, unknown, hidden and malformed calls are refused, a provider
  failure gives deterministic text).
- **Real Pi with a mock or scripted model.** `tests/explore/test_pi_runs.py` runs the real worker
  end to end through the server (skipped unless the worker is installed; `TWOTRACK_REQUIRE_PI=1`
  turns the skip into a failure): the turn and call budgets, a viewer who cannot reach a write,
  a write that waits for approval and runs once, an approval whose params changed, a worker that
  dies, a run past its clock, a failed model call, the key staying in the worker's environment,
  a call Pi refuses for its schema being audited. `make smoke` (`scripts/smoke.py`) checks the
  running compose stack with no key: a question no flow covers runs in Pi on the mock model.
- **Our side of the line, without the real worker.** `tests/explore/test_protocol.py`,
  `tests/explore/test_endpoint.py`, `tests/explore/test_fake_worker.py` (a worker that breaks the
  rules) and `tests/explore/test_selection.py` (settings and the start-up checks) need no Node.
  The boundary has its own tests, which run the built-in loop and the host side of the Pi
  runtime against a fake worker: `tests/explore/test_exposure.py` (the exposed set and the
  start-up check of the allowlist), `tests/explore/test_handover.py` (the hand-over and its
  guard), `tests/explore/test_refusals.py` (refusals are final and on record; one case also
  drives the real worker where it is installed) and `tests/explore/test_model_schema.py`
  (standard keywords only).
- **Real Pi with a real model.** Not part of this repository's tests. It is a separate run, and
  [docs/EVIDENCE.md](../EVIDENCE.md#real-model-not-run-yet) holds the commands. No real-model
  run is recorded in this repository.
