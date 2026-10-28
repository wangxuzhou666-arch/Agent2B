# ADR 0001: Code computes, the model narrates

## Status

Accepted. `tests/guards/test_numeric_lock.py` and `tests/graph/test_graph.py` pin the lock and
the narration path; `tests/server/test_untrusted_fence.py` pins the fence on `message` and
`scope` (Evidence lists the rest).

## Context

People act on the numbers this console shows: how many SKUs to reorder, how many purchase
orders are late, what a plan will cost. A wrong number is worse than no answer, because it looks
exactly like data. A model that receives rows will filter, add up and estimate in its own text,
and an error there is silent. The question is where a number is allowed to come from.

## Options considered

1. **The model sees the rows and answers.** The common tool-calling pattern, and the
   benchmark's system A (`bench/baseline.py`: every tool result goes back as JSON, rows
   included).
   - For: the simplest design; any question the rows can answer is answerable, including ones no
     tool anticipated.
   - Against: every figure is model arithmetic over a table; row values, restricted ones
     included, leave the process; the context grows with the table.
2. **The model sees the rows, and its numbers are checked afterwards.** The benchmark's system
   C: system A's answer through the same numeric lock, with every number in the tool outputs
   allowed.
   - For: catches a number that appears nowhere in the data.
   - Against: the allowed pool holds every cell, so a number copied from the wrong row, or a
     figure for the wrong filter, passes; the rows still leave the process.
3. **The model sees computed aggregates only, and a lock removes any number they do not back**
   (chosen).
   - For: every number a user reads was computed by code (an aggregate, a row count) or copied
     by code (the table); rows never reach a provider; the allowed pool is small, so a mismatch
     means something.
   - Against: answers are shaped by the aggregates a tool declares; a question about a figure no
     tool computes gets an account of what the figures do show, not an answer.

## Decision

- A tool returns a `ToolResult` (`core/contract.py`): `rows` for the table, `aggregates` (finite
  numbers only, validated), `columns`, `provenance` and a `message`.
- A narrator sees `narrator_view()`: aggregates, row count, column names, scope and message,
  plus a readable label per aggregate and the reply language (`narration.result_view`). It never
  sees a row.
- Two strings of that view are written by tool code from stored data and request values, so a
  stored record can try to speak to the model: `message` (create_po's names the supplier) and
  `scope` (it names the filter values). Each non-empty one is fenced as untrusted data on its way
  to a model (`narration.fenced_view`): wrapped in `<<<UNTRUSTED ...>>>` and
  `<<<END UNTRUSTED ...>>>` lines with a fresh random nonce, with anything inside that looks like
  a marker rewritten, and the prompts tell the model to use fenced text as information only.
  Nothing else is fenced: the aggregates are numbers, and the column names, labels and language
  come from declarations. The fence is applied where a view leaves for a model: the narration
  request of the OpenAI-compatible provider (a tool's answer, a workflow's last step) and the
  result of every tool call an exploration run hands back, in either runtime
  (`core/explore/calls.py`). The template and the console show both strings as they are.
- Model text goes through `lock_narrative` (`core/guards/numeric_lock.py`) against the
  aggregates, the row count and the number of rows. A sentence with any other number is removed
  whole; over-stripping is safe, under-stripping is not. Small integers are not exempt: an
  invented "3 orders are late" is the error the lock exists for.
- If nothing survives, or the model call fails, the answer is the deterministic template, which
  copies its numbers from the view (`narration.template_narration`). A failed call is named in
  the trace and the response, so the template does not pass for a model answer
  ([ADR 0009](0009-honest-zero-key-mode.md)).
- The same rule holds wherever twotrack asks a model to write text a user reads: the last step of
  a workflow; exploration, in either runtime (the built-in loop, `core/agent_loop.py`, or the Pi
  worker, picked by `TWOTRACK_EXPLORER`), whose answer is locked against the numbers of all the
  results its calls returned; and the chat fallback, locked against an empty set, so any figure
  in it goes.

## Consequences

- Tool authors must put every figure worth saying into `aggregates` (declared in the tool's
  `outputs`, or the pipeline rejects the result), should label each one per language
  (`aggregate_labels`; a missing label falls back to the English one, then to the key made
  readable), and keep free numbers out of messages (the messages in
  `plugins/supply_chain/tools.py` hold ids, a supplier's name and ISO dates, no quantity).
- The lock over-strips: a correct number the model derived (a difference the tool did not
  compute) is removed with its sentence.
- Known gaps, documented in the lock (`core/guards/numeric_lock.py`): numbers spelled out in words
  are not read (English "three" is never checked; Chinese numerals are read only when they carry
  a unit, 四十二 yes, 三 no; the prompts tell the model to write every number as digits, exactly as
  the result gives it), and bare years 1900-2100,
  ordinals, dates, clock times and identifiers such as `SKU-123` are not treated as quantities.
- The fence lives in the OpenAI-compatible provider and in `run_tool_call`, not in
  `GuardedProvider`: a provider written against the `Provider` protocol receives the unfenced
  view and has to fence the two strings itself. It covers the models twotrack calls. The result
  an MCP tool call returns carries `message` as the tool wrote it (`mcp/server.py`), for the
  client's own model to read.
- A right number for the wrong question passes the lock, because the result backs it. That
  failure needs its own guard ([ADR 0004](0004-scope-guard.md)).
- **The model also proposes parameters, and code validates them.** A routing opinion carries
  `params`; where both arms name the same target, the rule arm's own extracted values win over
  the model's (`{**llm.params, **hit.params}`, `core/routing/reconcile.py`), and a read that only
  the model names, at a confidence of 0.9 or more, runs with the model's params. In exploration
  every argument of every call is the model's, and a workflow's first model step produces its
  input. Nothing is trusted: the pipeline checks the params against the target's declared model,
  then every bound value against its constraint, before anything runs (`core/pipeline.py`), and a
  write also needs a grant bound to the validated params ([ADR 0005](0005-one-time-write-grants.md)).
  Validation checks the shape and domain of a value, not whether it is the one the user meant: a
  valid but wrong SKU id passes it, and the lock cannot see it either. The approval card shows
  every param of a write by its label before anything runs.
- With no row to read, an exploring model cannot discover an id, so the request has to name it
  ([ADR 0010](0010-exploration-runs-in-pi-behind-the-same-gate.md), the data-minimization cost).

## Evidence

- `uv run twotrack demo` tells the mock provider to invent the figure 31337. Its draft ends
  with "Next period should come in around 31337."; the lock removes that sentence, keeps every
  sentence whose figures the inventory result backs, and the trace records `stripped: 1`. The
  demo exits 1 if the invented figure reaches the answer
  (`src/twotrack/demo.py`, `tests/test_demo.py`).
- `tests/guards/test_numeric_lock.py`: small integers, precision as written, percentages as
  ratios, scale words, Chinese numerals and full-width digits.
- `tests/providers/test_providers.py::test_narrate_sends_only_the_view_and_raises_on_failure`,
  `tests/graph/test_graph_with_real_guards.py`.
- `tests/graph/test_graph.py`: `test_narration_never_receives_rows`,
  `test_chat_text_citing_figures_is_locked`, and `test_open_task_path_with_scripted_agent` (the
  built-in loop's answer loses the sentence with a figure no result backs).
- The fence, in `tests/providers/test_providers.py`:
  `test_a_forged_instruction_in_a_view_string_reaches_the_model_only_inside_the_fence`; and end
  to end in `tests/server/test_untrusted_fence.py`, where a supplier name stored with a forged
  end marker and an instruction reaches the model once, inside the fence with the real nonce,
  the forged marker rewritten.
- The benchmark (`bench/README.md`) compares A, B (twotrack) and C on synthetic questions with
  computed ground truth. A to C measures adding the lock to an ordinary agent; C to B measures
  the architecture. A dry run on the mock provider checks the harness only and is not a result.
