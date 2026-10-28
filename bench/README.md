# Wrong-number benchmark

twotrack's thesis is *LLMs route and narrate; code computes*. This benchmark measures what that
buys a user: how often an answer shows a number that is wrong, compared with a standard
tool-calling agent that has the same model, the same tools and the same data.

This repository publishes no results. This document is the benchmark's design and the way to run
it. Numbers from the harness may be quoted only from a real run (`--provider openai_compat`),
with the model id, date, commit and question-set digest that the result file records, and with
the Correct and Abstain columns next to the wrong-number rate. A dry run (`--provider mock`)
tests the harness and says so at the top of its report; nothing from a dry run is a result.

The benchmark measures the fixed-flow track: questions that a registered tool or workflow
answers, routed by the rule arm, a model and lexical search, computed by code. The exploration track (the
open-task route) is off in system B by default, and `--open-task` turns it on planned by the
built-in loop; the harness does not start the Pi worker. Exploration in Pi against a real model
is a separate run, documented in its own file when it exists.

## Quick start

```bash
# dry run: scripted fake model for A/C, zero-key mock for B; no network
uv run python -m bench.run --system all --provider mock

# render any result file
uv run python -m bench.report bench/results/<file>.json

# budget for a real run (prices: bench/config.py PRICES or --prices FILE)
uv run python -m bench.estimate --models <model id> --repeats <n>

# real run, one line, after the budget is approved (refuses to start without --confirm)
TWOTRACK_LLM_BASE_URL=... TWOTRACK_LLM_API_KEY=... uv run python -m bench.run \
    --system all --provider openai_compat --model <model id> --repeats <n> --confirm

# inspect the question set
uv run python -m bench.questions            # composition
uv run python -m bench.questions --jsonl    # every item with its truth
```

The key is read from `TWOTRACK_LLM_API_KEY` only (never a flag), never printed and never
written; result files record the endpoint host, not the URL. Without `--confirm` a real run
prints its plan and token estimate and exits with status two.

## Usage

Temperature and timeout flags (`uv run python -m bench.run --help` lists every flag):

- `--omit-temperature` sends no temperature at all, for models that reject the parameter. By
  default every call is pinned to temperature zero; `--no-temperature-pin` keeps each system's
  own instead (twotrack's per-call values, the endpoint's default for the baseline). The two
  flags exclude each other. The harness ignores `TWOTRACK_LLM_TEMPERATURE`: these flags set
  twotrack's provider the way that variable does in a deployment (`--omit-temperature` is its
  `omit`).
- `--timeout SECONDS` bounds every call except twotrack's routing opinion (the baseline's turns,
  twotrack's narration). Default: `TWOTRACK_LLM_TIMEOUT_SECONDS`, else the harness's own.
- `--route-timeout SECONDS` bounds twotrack's routing opinion. Default:
  `TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS`, else the product's routing default.

A timeout variable is read only when its flag is absent; flags and variables are held to the
rule the server applies to those variables. `--help` prints the defaults; the plan and the result
file (`meta.settings`) show the values in force.

### Exit status

| Status | Meaning |
|---|---|
| zero | Clean: the run finished and no model call failed. |
| one | A setting is missing or malformed: a real run without a model id (`--model` or `TWOTRACK_LLM_MODEL`), or a timeout variable that is not a valid number of seconds. No model was called. |
| two | Bad arguments (an unknown flag, both temperature flags, a timeout flag that is not a valid number of seconds), or a real run without `--confirm`. No model was called. |
| three | Degraded: at least one model call failed (an HTTP error, a timeout, an unparseable reply). The result file is still written and says so (`meta.degraded`, `meta.failed_calls`, `meta.failed_call_kinds`), the report opens with a DEGRADED banner, and a warning goes to stderr last. Its numbers must not be quoted. |

Any other error (filters that match no question, an unreadable `--prices` file) ends with a
Python traceback, also with exit status one.

### Running against Claude

Claude answers at its OpenAI-compatible endpoint (the README's Models section has the URL). Put
the key in `TWOTRACK_LLM_API_KEY` (the harness reads it from there only), then:

```bash
uv run python -m bench.run --system all --provider openai_compat \
    --base-url <the endpoint> --model <model id> --omit-temperature --repeats <n>
```

Some models reject a non-default temperature; set TWOTRACK_LLM_TEMPERATURE=omit to leave it out of requests.
This harness ignores `TWOTRACK_LLM_TEMPERATURE`; the equivalent flag is `--omit-temperature`,
which the command above passes. Without it every call is pinned to temperature zero, and a model
that rejects that fails every call with an HTTP client error, so the run ends degraded.
`--no-temperature-pin` does not help: twotrack's own calls still send a temperature of their
own.

Without `--confirm` the command prints the plan (systems, model, endpoint host, questions,
repeats, temperature, timeouts), the token estimate and the cost, then exits with status two
without calling the model. The cost reads `n/a` until the model id has a price entry, in `PRICES`
(`bench/config.py`, empty as shipped) or in a JSON file of the same shape passed as
`--prices FILE`. Add `--confirm` once the budget is approved.

## Systems under test

All three use the same model, the same seeded data and the same tools, and run every model call
at temperature zero (`--no-temperature-pin` keeps each call's own temperature instead).

| | System | What the user gets |
|---|---|---|
| **A** | Standard tool-calling agent (`bench/baseline.py`), OpenAI SDK. Every tool and the `reorder_plan` workflow is a function with the same JSON schema and description twotrack's own agent loop sends. Each call runs through twotrack's pipeline (same authz, preconditions, param validation, write grants). The tool result goes back as JSON **including every row**, the common pattern. A bounded number of model calls (`--max-turns`); the last one gets no tools. The system prompt is `BASELINE_SYSTEM`, a constant in `bench/baseline.py` (it asks for digits, says not to invent figures, to say so when the tools cannot answer); a result file's `meta.git` records the commit and a dirty flag (`git_info` in `bench/run.py`), which is how a result is tied to the prompt it ran with. | The model's final text. |
| **B** | twotrack's query path: `run_query` (`twotrack.core.graph`) on an `Engine` that the harness wires from core parts (`bench/world.py`, `make_engine` in `bench/systems.py`), not the running server. Built-in plugins only (`load_registry(extra_paths=[])`); a plain `Pipeline` over an in-memory database with a `SqliteAuditSink`; `EgressGuard` as the scrubber; the configured provider and the product's numeric lock. There is no HTTP server and no `ApprovalRecordingPipeline`, and `Engine.audit` and `Engine.explorer` are unset. Open tasks are off by default (the product default); `--open-task` turns them on, planned by the built-in loop. | The locked narration **plus** the figures and table the console shows. |
| **C** | Ablation: A's final answer put through `lock_narrative` after the fact, allowed numbers = every number present in A's tool outputs (rows included); when nothing survives, the product's withheld-answer text. No extra model calls. | The locked text. |

C separates the two ideas: A to C is "add the lock to a normal agent"; C to B is "the
architecture" (the model never sees rows, the router picks one computed result, the narrator may
only cite that result's aggregates).

All three ask as the same non-admin operator, on data seeded with a fixed seed, scale and anchor
date (the tools' clock is pinned on that date). Every run fingerprints the data before and after
each system; the report says whether anything changed (nothing should: no question asks for a
write, and writes need a confirmation token no system has).

## Question set

The questions are built by `bench/questions.py` from the seeded store; the set's digest is in
every result file, and `uv run python -m bench.questions` prints its composition. Six categories:

| Category | What it asks |
|---|---|
| lookup | one figure for a named SKU, warehouse, supplier or PO |
| aggregation | totals and counts per warehouse, status, all SKUs |
| formula | suggested order quantity, cost at standard unit cost, reorder plan |
| ranking | best-rated supplier, most overdue order, largest shortfall (figure + which one) |
| multi_condition | two filters the tools accept (item category included), or a filter no tool accepts |
| unanswerable | forecasts, sales, margins, staffing, ids that do not exist, "the order" |

Most items are answerable; a minority are unanswerable on purpose. Some are Chinese
translations of English items with the same entities and the same truth (`variant_of`), so the
language effect can be read in pairs. Tags mark deliberate paraphrases that avoid the tools'
vocabulary (`paraphrase`), answers that sit in one row of a longer table (`row_answer`),
questions no single call can answer (`beyond_params`), and items with a method distractor
(`method_distractor`).

**Ground truth is computed, never typed.** `bench/truth.py` reads the store and uses the
plugin's own formulas from `twotrack/plugins/supply_chain/logic.py` (stock positions, low stock,
`suggested_qty`, `money`, supplier ranking, `days_late`); it only filters rows and adds up.
A test runs each item's expected call through the pipeline and checks that its output holds
exactly the computed value at the stated place; the `beyond_params` ones are re-derived in the
test from the closest call's rows and the stored criticality or status. The category items are
also re-derived from the unfiltered call's rows and the stored categories, so the category
filter is checked by a second route.

Selection rules (all enforced by tests in `tests/bench/`): entities are drawn with a seeded RNG;
ids named in the tools' routing examples are never used, and no question repeats a routing
example; no expected value is zero (a spelled-out "none" is not machine-checkable), and integer
answers are not tiny unless the item also asks *which* entity (small counts are too easy to hit
by coincidence).

Each answerable item names:

- `expected`: the numbers, with an absolute tolerance (exact for every current item);
- `expected_text`: for rankings, the entity (id or display name, either counts);
- `expected_tool` + `expected_params`: the single call a correct system would make;
  `also_accepted`: other targets whose output measures the same quantity *by definition*
  (e.g. `reorder_suggestion` without a SKU covers exactly the low-stock SKUs);
- a locator, where that call's output shows the answer: `agg:KEY` (an aggregate), `cell:COL@
  KEYCOL=KEYVAL` (a cell of the row the question names), `top:COL` (the first row, only when the
  tool sorts by exactly the asked criterion);
- `distractors`: the asked quantity recomputed with one of the question's filters dropped (all
  SKUs instead of one, all warehouses, all suppliers, all statuses, all categories), plus, in a
  few items, the same figure by the other tool's method. Distractors equal to the answer or to a
  number in the question are dropped.

### Keeping the set held out

The question set is a held-out measuring set, never a tuning set. The discipline has these parts:

- **A dev set comes first.** Routing changes are driven by a separate dev phrasing set
   (`tests/routing/test_dev_phrasings.py`), written before the change, independently of this
   set's questions and of the tools' routing examples. Each expectation states what the user
   asked for, not what the router does now.
- **Known misses stay loud.** An item the router still gets wrong is a strict expected failure
   with its reason in the dev set, so a miss stays measured and a fix cannot go unnoticed: an
   item that starts passing fails the suite until its mark is removed.
- **This set is never tuned on.** It changes only when a system capability changes, never to fit
   a result, and a change to the set is listed in the changelog below. Parameters such as the
   contest margin are fixed before this set is run. The set must be frozen before a real run and
   never edited after seeing results.
- **The dev set and the digest show what moved.** Whether the router moved is read from the dev
   set: its strict expected failures fail the suite the moment a miss is fixed, and a regression
   fails its own item. Whether this set moved is read from its digest: every result file records
   a digest of the whole question set (`meta.question_set.digest`; the report prints it, and
   `uv run python -m bench.questions` prints the current one). Any edit to a question, its truth
   or its tags changes it, so a result names the exact set it was scored on.

## Scoring (exact definitions, `bench/score.py`)

**Shown numbers.** The answer text is split with the numeric lock's `split_sentences` and read
with its `extract_numbers`: the scorer and the lock agree on what a number is. Dates, clock
times, bare years, ordinals, list markers and digits glued to letters (a SKU id, a quarter
label) are not numbers to either. For B's "what the user sees" view the result's aggregates are
shown numbers too (as the console's figure tiles show them).

**Match.** A shown number matches an expected value when the lock's `is_backed` rule holds (the
written number is the value at the precision it was written: a figure written with fewer
decimals matches the value rounded to them, a `k` suffix scales by a thousand, a percentage
matches its fraction, signs ignored) or it is within the item's tolerance.

**Supported pool** (what is "derivable from that run's tool outputs", per tool result): every
number in it (aggregates, row count, cells, digit runs inside text cells); per numeric column
sum, min, max, mean and count; per column the distinct count; per text column, grouped by its
values (only a column with 2 to 200 distinct values and at least one repeated value), each
group's size and each numeric column's sum, mean, min, max and share
of the column total; pairwise sum, difference and ratio of the aggregates and the row count;
within each row of a short result, pairwise sum, difference, product and ratio of its numeric
cells. The pool is generous on purpose: it can only under-count wrong numbers.

**Roles.** Each shown number gets the first that applies:
`expected` (matches an expected value and is not a coincidence) → `echo` (a number of the
question) → `supported` (backed by the pool) → `unsupported`.
A match is a **coincidence** when the only tool outputs of the run that back the number come
from tools that do not measure the asked quantity (not in the item's accepted tools): a count
from the purchase-order report does not answer "how many orders are late" just because the
answer happens to be the same figure. Numbers backed by no output at all (derived by the model)
keep their match.

**Stand-in.** A supported or unsupported number that matches a distractor of an expected value
the answer does not show. It is a **contradiction**, unless the answer says it could not answer
(abstention language below); then it is *caveated* and counted separately.

**Abstained** = the answer shows no number other than echoes, **or** it matches the abstention
lexicon (`ABSTAIN_PATTERNS`: narrow English and Chinese phrases for cannot / no data / not
found / please clarify / withheld, including the product's own clarify and no-match texts).

**Per answer** (one outcome, decided in this order):

- First, `wrong_number`: at least one `unsupported` number, or a contradiction.
- Otherwise, for an answerable item: `correct` if every expected number and every expected text
  is shown; else `abstained` if abstained; else `incomplete`.
- Otherwise, for an unanswerable item: `correct` if abstained; else `answered` (gave figures it
  should not have).

So "correct" never contains a wrong number, and abstaining on an answerable question is not
correct.

**Rates** are shares of all answers: Correct, Wrong-number (with its two parts, Unsupported and
Contradiction), Abstain (the flag, any item; the breakdown gives it for answerable items and the Correct share for unanswerable ones).
Wilson score confidence intervals are printed next to Correct and Wrong-number.

**B views.** `B_seen` ("what the user sees") scores the narration plus the aggregates, and gives
locator credit for the table: an expected number or text counts as shown when its `cell:` or
`top:` locator finds it in the visible table *and* the table comes from an accepted tool.
`B_text` scores the narration alone, the like-for-like comparison with A's text. **Headline
numbers use `B_seen`**, because that is the product's answer; `B_text` is always printed on the
next row.

**Routing accuracy** (B, answerable items): the decision runs one of the item's accepted targets
(tool or workflow kind). "strict" counts only the expected tool. A one-option confirmation, and
a scope clarification (the question names a filter the routed tool cannot apply), count as not
routed. For A and C the breakdown shows **tool hit**: an accepted tool was called
with an ok result.

**Latency** is wall time per question (A: the whole loop including tool execution; B:
`run_query`). **Tokens** are the endpoint's `usage` per call, summed; when an endpoint omits
usage they are estimated from characters and marked `≈`. C reuses A's calls, so its tokens are
A's. **Cost** = tokens x the price entry for the model id; with no entry it is "n/a" (the harness
never guesses a price).

## Result files

One JSON per run in `bench/results/` (git-ignored except its README): `meta` (versions, times,
provider, model, endpoint host, seed / scale / date, data digests, question-set digest and
composition, settings, git commit), `summary` (the counts behind every rate, per view) and
`records` (per view, question and repeat: answer, every shown number with its role, outcome,
latency, per-call usage, tool calls or routing decision). The writer scrubs the API key value
from the text before writing, as a second line of defense; no field ever holds it.

## Budget

`bench/estimate.py` builds every request a real run would send, offline (the baseline loop
against the scripted fake; twotrack through the real `OpenAICompatProvider` against a client
that replies like the mock), and sizes it from a characters-per-token ratio plus one token per
CJK character. Completions are assumed per call kind. `expected` follows one tool call per
answerable question; `high` doubles the baseline (a second tool call re-sending the grown context) and adds
a quarter to twotrack. The estimate moves with the code (prompts and tool cards are measured,
not hard-coded), so no figure is quoted here: re-run the estimator right before asking for a
budget.

## Threats to validity

Read these before quoting a number.

- **Small, synthetic, single-domain.** One seeded dataset and a modest question set, so a
  confidence interval on a rate is wide. Use several repeats: temperature zero is not
  deterministic on most hosted endpoints, and the report prints the spread across repeats.
- **Same author for tools and questions.** Many questions use the tools' own vocabulary, which
  favors twotrack's keyword router. Mitigations: no question repeats a routing example or uses
  their ids; paraphrases are tagged; the report splits B's routing by deciding arm (rule,
  model, lexical search). The question set must be frozen before real runs, never edited after seeing
  results.
- **Shared formulas.** Truth uses the same `logic.py` formulas as the tools, so a formula bug
  would be invisible to the benchmark. It measures whether a system reports what the formulas
  give for the scope the user asked about.
- **Value-based scoring.** The scorer does not parse what a number is said to be. A correct
  figure for a different quantity presented as the asked one is `incomplete`, not
  `wrong_number`, unless it is a scope distractor; distractors only cover dropped filters and a
  few method swaps. The coincidence guard needs the tool behind a number and cannot catch a
  coincidence inside the right tool's output. Heavily rounded numbers match by the lock's
  precision rule.
- **The supported pool is dense after big tables.** After an unfiltered large table, nearly
  every small integer and a large share of the next range are "derivable", so a small invented
  number there is usually not flagged. The baseline's wrong-number rate is therefore a lower
  bound; this leniency favors the baseline.
- **Shared blind spots with the lock.** Numbers written as English words (or as Chinese
  numerals without a unit), bare years and ordinals are invisible to both. The baseline prompt asks for digits; the report counts answers with
  spelled-out numbers so the gap is visible.
- **Abstention is detected with a phrase list** (English and Chinese). Unusual phrasings can be
  missed (an unanswerable question counted `answered`, a stand-in counted as a contradiction) or
  over-matched.
- **Different modalities.** B shows a table and figure tiles, A only text. That is why both B
  views are reported; `B_text` against A is the text-only comparison.
- **One baseline.** One prompt, full rows, a bounded number of turns, no code execution, no row
  truncation. Other agent designs may do better or worse; the comparison is with this common
  pattern.
- **Product settings.** B runs with product defaults except the temperature pin (narration
  normally runs at a small positive temperature) and the narration timeout, which follows
  `--timeout`. Routing asks for `response_format=json_object` (turn off with
  `--no-route-json-mode` for endpoints without it) under the product's routing timeout, unless
  `TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS` or `--route-timeout` sets another. A failed model call is
  recorded, never silent: the answer lists it in `model_errors`, the run counts it once and the
  report lists failed calls by kind. The answer that needed it fell back (routing to rules and
  lexical search, narration to the template), so any failed call makes the run degraded (exit status
  three, see Usage). Open task is off; `--open-task` turns it on, planned by the built-in loop.
- **By design B cannot be correct on the `beyond_params` questions** (the lock forbids figures
  no tool computed). They measure whether it shows a wrong-scope figure instead of saying it
  cannot filter (the scope guard's job, see [ADR 0004](../docs/decisions/0004-scope-guard.md)).
  The guard only sees constraints the plugins declare (categories, warehouse, PO status,
  criticality); a mis-routed question whose wrong tool happens to accept the named filter is not
  caught.
- **Token counts** in estimates are approximations; real runs use the endpoint's reported usage,
  which differs by tokenizer.
- **Core is still moving.** The harness was verified against the working tree it was built on;
  rerun the tests (`uv run pytest tests/bench`) before any real run.

## Dry-run check (harness only, not results)

With `--provider mock`, the fake hallucinates on a configurable share of items (`--fake-hallucination-rate`).
The tests run it at full strength in several modes and require: every invented number flagged in
A and nothing else flagged; every invented number stripped by the lock in C; every wrong-scope
answer counted as a contradiction; no unsupported number from twotrack on the mock, including
when the mock's narrator appends an invented figure; no secret in a result file.

## Changelog

The question set is held out: it changes only where a system capability changed, never to fit a
result. A change to the set is listed here, with what changed in the product and what changed in
the set. A change to the router or the tools that leaves the set as it was is not listed: the
set's digest, recorded in every result file, does not move, and the dev set
(`tests/routing/test_dev_phrasings.py`) tracks the routing. Counts and digests are not repeated
here. The notes on the items the entry below changed cite this changelog.

**Item-category filter and scope guard** (the digest changed; this is the one change that
edited the set). The stock and purchase-order tools and the `reorder_plan` workflow gained
a `category` filter, and twotrack gained a scope guard (a question that names a constraint the
routed tool cannot apply is asked back, with the unfiltered run as one explicit choice, instead
of being answered with the broader figure). Ids are stable; no existing question was reworded
and no expected value changed (these items always held the filtered truth; what changed is where
a correct system shows it):

- The multi-condition items that name an item category now expect a call with the `category`
  filter and read the answer from an aggregate; they are no longer `beyond_params`.
- The items that count low-stock SKUs in a category also accept `reorder_suggestion`, which without a
  SKU covers exactly the low-stock SKUs, now within the category (the rule an aggregation item
  already uses).
- Items were added, tagged `beyond_params`, so the set still measures questions whose filter no
  tool supports (criticality on late orders and on stock, status on late orders), with a
  Chinese translation of one. Truth is the closest call's rows filtered again (re-derived in
  `tests/bench`); the distractor is the unfiltered figure. One of them uses the class with the
  most late orders. They were written for this change by the same author as the scope
  vocabulary (see the threats to validity).
- Routing source: a query that falls through to chat is now recorded as `fallback`, not
  `semantic`, so the routing-by-arm table shows `fallback:chat` rows.
