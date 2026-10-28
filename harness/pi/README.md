# Pi explorer worker

`worker.mjs` runs twotrack's open-ended exploration branch (the `open_task` route) inside
[Pi](https://www.npmjs.com/package/@earendil-works/pi-agent-core)'s agent loop, one process per
run. Pi plans and calls tools; every call goes back to twotrack's Python host, which answers it
through the same controlled pipeline the built-in loop uses (lookup, authorization,
preconditions, validation, write grants, audit). The worker runs no business logic.

Select it with `TWOTRACK_EXPLORER=pi`; the default is `builtin`, which needs no Node.

## Install

Node 22.19 or newer. From this directory:

```sh
npm ci --ignore-scripts
```

`package.json` pins `@earendil-works/pi-agent-core`, `pi-ai` and `pi-telemetry` to exactly
`1.2.0`; `overrides` keeps their internal caret ranges on the pins, and the committed lockfile
fixes every transitive version. Install scripts are skipped; none is needed. At startup the
worker reads the installed versions, reports them in its `hello` line and exits if any differs
from the pins. The host checks them too, and refuses to start when Node or this install is
missing.

## How the host runs it

```sh
node --permission --allow-fs-read=<this directory> worker.mjs
```

- No file-system writes, child processes, worker threads or native addons. The permission model
  does not cover the network: restrict egress to the model endpoint at the OS or container level.
- cwd and `HOME` are a fresh empty temporary directory. The environment is an explicit allowlist
  (`HOME`, `LANG`, and the key variable when a model needs one); nothing else is inherited.
- The key reaches the worker only through that variable. It never appears in a protocol line;
  the worker also redacts it from everything it writes.
- One process per run, in its own process group. When the run ends, times out or misbehaves the
  host closes its stdin and kills the group if the worker has not exited within a short grace
  (at once after a protocol violation).

## Line protocol

UTF-8 JSON, one object per line, at most 1 MiB per line in either direction. stdout carries
protocol lines only; stderr is diagnostics (the host keeps a capped copy). A line that is not
JSON, has an unknown `type`, or breaks the field rules below is a protocol error: the reader
stops the run and reports a failure. Nothing falls back to another runtime.

### Worker to host

| `type` | when | fields |
|---|---|---|
| `hello` | first line, before `start` | `protocol` (1), `versions` {package: version} for the three pins, `node` |
| `tool_call` | the model called a declared tool and Pi accepted the arguments | `id` (worker-assigned, unique per run: `t1`, `t2`, ...), `name`, `args` (object) |
| `event` | progress | `name` plus the fields below |
| `final` | last line, then the process exits | `text`, `stop_reason`, `error`, `http_status`, `usage` {input, output}, `turns`, `tool_calls`, `forwarded` |

Events:

- `assistant`: a model turn ended. `turn` (1-based), `stop` (Pi's stop reason), `calls` (the tool
  names it asked for).
- `tool_blocked`: Pi refused a call itself, so it never reached the host as a `tool_call`.
  `id`, `tool`, `reason`, `detail` (a short message), `args` (when small enough). `reason` is
  `unknown_tool` (not a declared tool), `invalid_args` (the arguments fail the declared schema),
  `truncated` (the model hit its output limit mid-call), `max_turns` / `max_tool_calls` (a
  budget is spent) or `aborted` (the run was being stopped). The host audits each one as a
  refused call, except `aborted`: the run is already stopping, so nothing is recorded.
- `tool_timeout`: the host did not answer `id` within `tool_timeout_ms`; the model was told so,
  and a later answer is ignored.

`stop_reason` in `final`: `stop` or `length` (the model answered), `max_turns`,
`max_tool_calls`, `wall_clock`, `host_abort`, `host_gone` (stdin closed), `terminated` (a
`terminate` answer ended the run), `error` (the model call failed: `error` is a short redacted
message, `http_status` its HTTP status when it has one) or `aborted`.

### Host to worker

| `type` | when | fields |
|---|---|---|
| `start` | once, after `hello` | `task`, `system`, `tools` [{`name`, `description`, `parameters`: JSON Schema}], `model`, `budget` |
| `tool_result` | answers one `tool_call` | `id`, `ok`, `content` (text the model sees, when `ok`), `code` and `error` (when not `ok`; the model sees `code: error`), `terminate` |
| `abort` | the host stops the run | `reason` |

- `model`: `id`, `base_url` (an OpenAI-compatible chat-completions endpoint), `api_key_env` (the
  variable holding the key), optional `max_tokens`, `context_window`, `headers` (the worker also
  reads a `compat` object, which the host never sends). No temperature is sent. The SDK's fingerprint headers are dropped and `User-Agent` is
  `twotrack-explorer`.
- `budget`: `max_turns`, `max_tool_calls`, `wall_ms`, `request_timeout_ms` (one model request),
  `tool_timeout_ms` (one host answer).
- `terminate: true` ends the run after the current batch of calls; the host sends it with the
  answer that issued a write grant, followed by `abort`.
- An `id` the worker is not waiting for (unknown or late) is ignored. Closing stdin aborts the run.

## Budgets

Pi 1.2.0 has no turn or step limit, so the worker adds both:

- every call the model asks for counts against `max_tool_calls`, refused ones included;
- on the last allowed turn, and once `max_tool_calls` is spent, `beforeToolCall` blocks the call
  with `terminate`, so the host never runs a call whose result no turn would read;
- `finishTurn` ends the run after the turn that spent a budget;
- `wall_ms` aborts the whole run; it counts from the `start` line.

The host enforces the call budget and the wall clock again on its side and, when either is
exceeded, aborts the worker and stops it as above. Its clock starts at the `hello`, not at the launch: starting Node
and loading Pi has a limit of its own (`TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS`), so a slow
start never spends a run's budget. A worker that says nothing within that limit ends the run as
`worker_unavailable`.
