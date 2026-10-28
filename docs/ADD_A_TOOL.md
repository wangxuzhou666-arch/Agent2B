# Add a tool

A tool is a question twotrack can answer with code: a Pydantic model for its params, a handler
that computes figures from stored data, and a declaration that says who may run it, what it may
return, whether a person approves each run, and how a question finds it. This tutorial walks through [`examples/plugins/supplier_scorecard.py`](../examples/plugins/supplier_scorecard.py),
one new read tool built from the purchase orders the built-in plugins already store. No core,
server, provider or built-in file changes, and no new entity, column or seed row.

[ADD_A_CATEGORY.md](ADD_A_CATEGORY.md) adds data to existing tools; this adds a report of its
own. Every claim below has a test: the scorecard's in `tests/plugins/test_drop_in_supplier_scorecard.py`,
the contract's in `tests/core/test_tool_contract.py` and `tests/pipeline/test_pipeline_contract.py`.

## Read this first

- **An in-process plugin is trusted code.** It runs inside the server process with the server's
  rights. The registry checks what a plugin *declares*, not what its handler does
  ([SECURITY.md](../SECURITY.md)). Install only plugins you have read.
- **Installing a plugin grants no extra permissions.** Who may run a tool is the `roles` list it
  declares, checked by the pipeline on every call; `admin` passes every list. The core caps that
  list with a role/effect table: a `viewer` may only be given `read` tools, so a plugin that
  lists `viewer` on a `write` or `external` tool does not start. The scorecard lists `admin` and
  `operator`, so a `viewer` is refused and the refusal is audited.
- **A tool declares whether a run needs approval, and a write always does.** The pipeline hands
  a tool whose `effect` is `read` or `external` a `ReadOnlyStore`: its write methods raise,
  `transaction()` is unavailable and `query()` runs one `SELECT` under SQLite's authorizer. That
  bounds the store handle, not the handler, which is trusted in-process code (the first item
  above). A tool that declares `approval="required"` gets a one-time grant first, bound to the
  user, the tool and the params ([ADR 0005](decisions/0005-one-time-write-grants.md)). A `write`
  must declare `required` and a `read` must declare `none`. The scorecard is a read.

## 1. What the tool answers

For one supplier, or for all, over the last `window_days` days (1 to 365, default 90): how many
purchase orders were placed, how many are due, how many of those arrived, and how many days the
rest are past their ETA. The output is figures only: `rows` stays empty, so everything a model
or a table could show is an aggregate the code computed
([ADR 0001](decisions/0001-code-computes-the-model-narrates.md)).

It does not repeat the built-in reports. `late_shipments` lists overdue orders (no window, no
denominator); `supplier_compare` ranks suppliers by rating and lead time. The scorecard is the
one that divides, over a window. It reuses `days_late` and the id reader of the built-in tools,
so "late" means the same everywhere.

The base model keeps no receipt date, so "on time" cannot be measured. The share is "due orders
received", an upper bound of the on-time rate, and its label says so.

## 2. Params with validation

```python
class ScorecardParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: Code | None = None
    window_days: int = Field(default=90, ge=1, le=365)
```

The pipeline validates them before the handler runs: `window_days=0` or `366`, a misspelled
key (`extra="forbid"`) and an empty id come back as `invalid`, audited. An unknown supplier is an
`InputProblem`, which the user can fix, so it is `invalid` too.

## 3. The handler

`supplier_scorecard(ctx, params) -> ToolResult` reads orders through `ctx.store`, tallies them
and returns `aggregates` (numbers only, the only figures a narrator may cite), a `message` and a
`provenance.scope`. A share or an average with nothing to divide is left out, not reported as 0.
`message` and `scope` carry identifiers and ISO dates, never a free number, and follow
`ctx.language`.

## 4. The declaration

```python
SUPPLIER_SCORECARD = Tool(
    name="supplier_scorecard", title={...}, description={"en": ..., "zh": ...},
    keywords=("scorecard", "on-time delivery", "评分卡", "准时交货", ...),
    examples=("supplier scorecard for the last 90 days", "各供应商的履约表现", ...),
    param_extractor=read_params,
    roles=("admin", "operator"), effect=ResourceEffect.READ, approval=Approval.NONE,
    outputs=ToolOutputs(aggregates=("po_count", "due_count", "received_count", ...), columns=()),
    explorable=True,
    requires=("has_purchase_orders",), params_model=ScorecardParams,
    handler=supplier_scorecard, entity="supplier", aggregate_labels=labels(...))
PLUGIN = Plugin(name="supplier_scorecard", version="0.1.0", tools=(SUPPLIER_SCORECARD,),
                preconditions=(HAS_PURCHASE_ORDERS,))
```

- `roles`, `effect`, `approval`, `outputs` and `explorable` have no default: a forgotten write
  cannot become a read, and a tool cannot skip a declaration by leaving it out (the file fails
  to load). `params_model` must set `extra="forbid"`, or a misspelled filter would run wider
  than asked.
- `approval` is `"required"` or `"none"`. The scorecard is a read, so `"none"`. An `external`
  tool (it leaves the deployment) picks one; with `"required"` it follows the same one-time,
  parameter-bound grant as a write.
- `outputs` lists every aggregate key and every row column the handler may return. The scorecard
  returns figures only, so `columns=()`. Every key in `aggregate_labels` must be one of them.
- `explorable` says whether exploration (the `open_task` route, where a model plans its own
  calls) may offer the tool to a model. The exposure filter reads it (`core/explore/exposure.py`):
  a tool that says `False` is never offered, whatever else allows it, the deployment's allowlist
  included. It is a plain opt-in: a read report says `True`. A tool that says `True` is offered
  when the asker's roles may run it, its preconditions hold and the allowlist
  `TWOTRACK_EXPLORE_TOOLS` is unset or names it; unset, the allowlist admits every explorable
  tool except an `external` one. An `external` tool is exposed only when the allowlist names it,
  and one that says `True` must also require approval, so each of its calls waits for a person.
- `keywords` name the report, never a bare "supplier" or "late" (those belong to
  `supplier_compare` and `late_shipments`). `examples` and the descriptions feed the fallback.
- `aggregate_labels` name every figure in both languages; the answer is built from them.
- `requires` hides the tool until there are purchase orders to read.

### Why these are declared, not inferred

**Approval.** The pipeline used to derive the grant from the effect, so a tool could not say
"this outbound call needs a person's approval" and a reviewer could not tell from the plugin
whether a run stops for one. Declaring `approval` puts that decision in the file under review,
and the registry ties it to the effect (a write says `required`, a read says `none`), so a
plugin cannot declare its way out of a grant. The pipeline issues and consumes grants by this
field alone.

**Outputs.** A handler returns more than a caller should see more easily than it returns less: a
column added for debugging, an aggregate that carries a cost. Declaring `outputs` is data
minimization at the contract. The pipeline compares each result with it and turns an undeclared
aggregate key, column or row key into an audited plugin error with no result (a write rolls
back), so only declared aggregates can reach the narrator or the console. The record names the
keys and never the values.

**The role/effect table lives in core.** Which role may run which effect (`viewer`: read;
`operator` and `admin`: read, write, external) is `ROLE_EFFECTS` in `core/contract.py`, not a
field a plugin sets. A plugin chooses among roles that exist and can narrow the table, never
widen it: the registry refuses a tool whose roles the table denies for its effect, and the
pipeline checks the same table on every call. Installing a plugin therefore cannot give a
viewer a write. An in-process plugin is still trusted code: the registry checks what a plugin
declares, not what its handler does, and this is not a sandbox.

## 5. Load it

```sh
TWOTRACK_DEMO_MODE=1 TWOTRACK_PLUGIN_PATHS=examples/plugins uv run twotrack serve --port 8766
```

Demo mode seeds the purchase orders the scorecard needs and allows the passwordless sign-in used
below.

Discovery imports every `*.py` file of the directory, in sorted order, and the registry checks
the whole set before the server starts. A tool that names an entity no plugin declares, a role
nobody knows, an unknown precondition or a declaration the contract refuses stops start-up with
every problem listed. A `close_po` write that lists `viewer`, declares `approval=Approval.NONE`
and takes a params model that ignores unknown keys:

```
error: cannot start: plugin registry is invalid (3 problem(s)):
  - tool 'close_po': role 'viewer' may not run a write target; core policy lets viewer run only ['read']
  - tool 'close_po': a write target must declare approval='required': it always waits for a person's one-time approval
  - tool 'close_po': params_model ClosePoParams accepts unknown keys; add model_config = ConfigDict(extra='forbid') so a misspelled param is refused instead of ignored
```

At run time the same rules hold: a result that carries an aggregate key or column the tool did
not declare comes back as an error and an audit record, never as data.

## 6. Prove it routes

A question reaches the tool by rule, with no model: the dev set in
`tests/routing/test_dev_phrasings.py` was written before the keywords, and what the first
wiring missed is kept as strict xfails. Loading the plugin moves none of the older phrasings.
Ask in either language, as an operator:

```sh
TOKEN=$(curl -s -X POST localhost:8766/api/auth/demo -H 'content-type: application/json' \
  -d '{"role":"operator"}' | jq -r .access_token)
for q in "supplier scorecard for SUP-001 over the last 30 days" "SUP-001 近 30 天的准时交货率"; do
  curl -s -X POST 'localhost:8766/api/query?stream=false' -H "authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' -d "{\"query\":\"$q\"}" | jq -c '{target: .decision.target, aggregates}'
done
```

Every number in the answer is an aggregate; a number a model adds is stripped by the numeric lock.

## What the contract does not have

The declarations name which keys a result may carry, not what is in them: `message`, `scope` and
the cells of a row are text and data the plugin writes, and an aggregate is checked only for
being a finite number. A plugin cannot depend on another plugin by name; it names entities and
preconditions, and the registry fails closed when they are missing. Both are noted for a later
change to core, not worked around here.
