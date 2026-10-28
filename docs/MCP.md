# Agent2B over MCP

`twotrack mcp` serves the registry's tools and workflows to an MCP client (Claude Desktop,
Cursor, any stdio MCP client). Calls run through the same pipeline as the console: role and
precondition gates, parameter validation, write confirmations and the audit log.

## Run it

```sh
# as an existing user (create one with: uv run twotrack users add olga --role operator)
uv run twotrack mcp --user olga

# demo mode only: as the built-in demo user of a role
TWOTRACK_DEMO_MODE=1 uv run twotrack mcp --role operator
```

Point it at the console's data directory (`TWOTRACK_DATA_DIR`, or the global `--data-dir`,
which goes before the subcommand: `uv run twotrack --data-dir data mcp --user olga`):
approvals and the audit log live in that database, and the console and the MCP server can use
it at the same time (SQLite in WAL mode: one writer at a time).

`twotrack mcp` builds the same runtime as `twotrack serve`, so it checks the exploration
settings at start even when `TWOTRACK_ALLOW_OPEN_TASK` is unset. A value `Settings.from_env`
rejects prints `error: <message>` and exits 2: `TWOTRACK_EXPLORER` other than `builtin` or `pi`,
an exploration budget out of range ([ARCHITECTURE.md](ARCHITECTURE.md#exploration)), or a
`TWOTRACK_EXPLORE_TOOLS` that names no tool. A name in `TWOTRACK_EXPLORE_TOOLS` that no plugin
provides as a tool or workflow, or that is not explorable, prints `error: cannot start: ...` and
exits 1. So does `TWOTRACK_EXPLORER=pi` without Node 22.19 or newer, the worker and its pinned
packages, or when one restricted worker does not say hello, and, with a real model, when
`TWOTRACK_LLM_MODEL` or `TWOTRACK_LLM_BASE_URL` is unset. On every failure stdout stays empty,
because it carries the protocol; the message is on stderr.

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "twotrack": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/twotrack", "twotrack", "mcp", "--user", "olga"],
      "env": { "TWOTRACK_DATA_DIR": "/path/to/twotrack/data" }
    }
  }
}
```

### Cursor

`.cursor/mcp.json` in a project (or `~/.cursor/mcp.json`) takes the same `mcpServers`
block.

## Identity

- `--user <name>`: an existing, enabled account. Its roles are read from the users table on
  every call, so changing or disabling the account takes effect immediately.
- `--role admin|operator|viewer`: only when `TWOTRACK_DEMO_MODE=1`.
- No identity, an unknown or disabled user, or `--role` outside demo mode: the server
  refuses to start.

The tool list is exactly what that user may run right now (roles and preconditions applied).

## What the client model sees

By default a result carries the computed figures (`aggregates`, with a readable `labels`
entry for each), `columns`, `row_count`, the result's `message`, `provenance` (which holds the
`scope`), a short `summary` and `model_errors`. The `summary` is the template text built from
those figures; a workflow that ends in a narration step returns that narration instead, after
the numeric lock. **By default it carries no table rows**: the client is a language model, so
rows stay in the console unless you opt in with `--rows` (below). The console and both
exploration runtimes never send rows to a model.

`message` and `scope` are text a tool writes from stored records. The console and exploration
put them inside an untrusted-content fence before a model reads them; MCP does not, so the
client's model receives them as plain text ([SECURITY.md](../SECURITY.md#known-limits)).

- `--rows` (or `TWOTRACK_MCP_ROWS=1`) includes rows, at most `--max-rows`
  (`TWOTRACK_MCP_MAX_ROWS`, default 50), projected for the user. Fields marked `restricted`
  are always removed, whatever the user's role.
- The last audit record of an MCP call ends with an `export` step: `channel`, `rows_sent`,
  `rows_withheld` and `redacted` (values the egress guard replaced). A result that cannot be
  checked for restricted values is withheld, and the step is then blocked with `rows_sent` 0.
  The step is appended best effort: a failure to append it is logged, and the call goes on.

## Writes

A write tool never writes from MCP. The call returns `needs_confirmation` with a summary and
a public `grant_id`; the request appears in the console under **Approvals** for the same
user, who approves (or rejects) it there within 15 minutes. There is no approve tool and the
grant token never leaves the server.

## `ask`

`ask` takes a plain-language question (English or Chinese) and runs it through twotrack's
own router, narration and numeric lock. The same rows policy and write rule apply. It is
omitted with `--no-ask`, or when a registry target is itself named `ask`. If
`TWOTRACK_ALLOW_OPEN_TASK` is set where the server starts, a question no fixed flow covers is
explored as in the console (route source `agent`), with the same exposed tools and budgets
([ARCHITECTURE.md](ARCHITECTURE.md#exploration)). A direct tool call is the client's own choice
of tool, so a tool's `explorable` flag does not hide it from the tool list.

## Audit

Direct tool calls are recorded with route source `mcp`; their trace reads candidates ->
understand (source `mcp`) -> gate -> execute -> narrate -> respond -> export. `ask` keeps the
router's own source (`rule`, `llm`, `semantic`, ...).
