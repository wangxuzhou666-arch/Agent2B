# ADR 0009: An honest zero-key mode

## Status

Accepted. `tests/providers/test_providers.py` pins the mock and its labels. Failed model calls are
reported in the trace, the answer, the console and MCP results
(`tests/graph/test_model_failures.py`, `tests/server/test_model_failures.py`,
`tests/mcp_export/test_mcp_model_failures.py`). The one scripted exploration case is
`tests/providers/test_mock_script.py`.

## Context

Most people who try the repo will not configure a model first, and CI has no key. The console
should run fully without one. The risk is a demo that looks smarter than it is: if template text
is presented as model output, every other claim of the project becomes suspect. A second risk
runs the other way: a half-configured real provider that quietly falls back to canned answers
hides a misconfiguration. A third: a configured model that fails while answering and is
replaced by template text without a word.

## Options considered

1. **Require a key.**
   - For: one code path.
   - Against: nobody can evaluate the repo in a minute; CI needs a secret.
2. **A mock that imitates a model** (canned "smart" answers, routing by heuristics dressed up as
   a model).
   - For: demos well.
   - Against: overstates the system, and the audit's per-source shares would credit a model
     that was never there.
3. **A mock that abstains wherever a model would decide, and labels itself everywhere**
   (chosen).
   - For: the zero-key console is the real system minus the model; nothing is attributed to a
     model that did not run.
   - Against: zero-key routing is rules and BM25 only, and zero-key answers are plain templates.

## Decision

- `MockProvider` (`providers/mock.py`): `route` always abstains, so the rule arm and BM25 carry
  routing; `narrate` returns the deterministic template, whose numbers come only from the
  narrator view; `chat` and `agent_step` say plainly that no model is configured and do not plan, with one
  exception (the scripted case below).
  Its one extra, `hallucinated_number`, appends an invented figure so the numeric lock can be
  demonstrated ([ADR 0001](0001-code-computes-the-model-narrates.md)).
- **One scripted case** (`providers/mock_script.py`). For exactly one request, the documented
  exploration case (`EXPLORATION_CASE`, with any supplier and SKU ids;
  [docs/EVIDENCE.md](../EVIDENCE.md)), `agent_step` follows a fixed script: three reads, then a
  purchase-order proposal that stops at the one-time approval. Every other conversation still
  gets the abstaining answer, and the mock still routes nothing. Why: a zero-credential demo of
  the exploration path, end to end, through the real Pi worker. The cost: the script asserts one
  plan, so it shows plumbing, not planning. It copies every value it sends from the request and
  the fenced results it was given, and nothing it does says how well any model explores.
- A provider may declare `name`, `model` and `mock`; `provider_info` reads them
  (`providers/base.py`). They are labels only: no code path behaves differently because of them,
  and pipeline code never asks which provider it talks to.
- The labels reach these surfaces (an MCP result carries none, see Consequences): `GET /api/health` reports `provider {name, model, mock}`
  (never an endpoint or a key); the `narrate` trace step and every workflow `llm` step record
  `provider` and `mock`, and the step that ends an exploration run records the model (`mock`
  without one); `twotrack demo` calls mock narration "mock template"; the console labels it
  "Template narration (no model configured)" and shows a "No model" indicator in the header and
  on the sign-in page while health says `mock`.
- `provider_from_env` (`providers/__init__.py`) defaults to the mock, raises when
  `TWOTRACK_PROVIDER=openai_compat` lacks `TWOTRACK_LLM_MODEL`, and raises on an unknown provider
  name. Swapping the mock for a real endpoint is configuration only.
- A configured model that fails is a third state, apart from the mock and from a model answer.
  A call that fails (an HTTP error, a timeout, an empty reply, a routing reply that is not the
  expected JSON) raises `ProviderError`, or `UnparseableReply` when a reply arrived but cannot be
  used; `route` raises too, so the router tells a failed opinion from an abstention
  (`providers/base.py`). The request's `GuardedProvider` records each failed call in `failures`
  as a `ModelCallError`: the call (`route`, `narrate`, `chat` or `agent_step`), the error's class
  and the HTTP status when there is one. It keeps neither the message, which may echo the
  request, nor the request or the key. A scrub that cannot run counts as a failed call too:
  nothing is sent. The answer still falls back as before (routing to the rule arm and BM25;
  narration, chat and exploration to template text), and the fallback is named:
  - the `narrate` trace step carries the last failed call that writes text as `model_error`
    when template text stands in for it (`GuardedProvider.text_failure`), and the answer's
    `answer_source` reads `template`; the `respond` step lists every failed call of the request
    as `model_errors`. Both steps are in the audit record;
  - the answer carries the same list as `model_errors`: `QueryOut` and `ApprovalOutcome`
    (`server/schemas.py`; the second is the narration of an approved write) and the results of
    the MCP tools (`ask`, a tool or a workflow, a refusal that reached a model);
  - the console puts a warning line under the answer ("Model call failed (narration); this
    answer was narrated from a fixed template", or "routing used rules and keyword search" when
    only routing failed), and its trace lines name the call, the error class and the status.
  The zero-key mock cannot fail a call, so it reports none and keeps its own label. Template
  text that exists because the numeric lock removed every sentence is not a failure and carries
  no `model_error`. Exploration, in either runtime, reports a failed model step as an
  `agent_step`; a run that ends in a runtime failure such as a timeout is recorded under that
  call with its own class (`ExplorerTimeout`).

## Consequences

- Zero-key routing misses paraphrases that avoid the declared keywords and stay under the BM25
  threshold; they fall through to the chat fallback, which says no model is configured (with
  `TWOTRACK_ALLOW_OPEN_TASK` on, to exploration first, where the mock says the same unless the request is the one scripted case).
- A zero-key answer is correct but plain: one sentence per figure.
- The answer's `answer_source` field reads `model` for text the mock wrote (narration, chat or
  exploration): the mock answers on the model's path, and its text passes the numeric lock like a
  model's. It reads `template` only for text that code wrote in place of a model's (after a
  failed call, when the lock removed every sentence, or when an exploration stopped short), and
  `system` for the console's own sentences (a clarification, a request for approval). What tells
  the mock from a model is in the trace: `mock: true` on the `narrate` step (the demo shows it as
  "mock template", `tests/test_demo.py`; the console as template narration), and `model_error`
  on that step with `model_errors` on the answer mark a failed call. A client that reads only
  `answer_source` takes the mock for a model, an MCP client included: an MCP result carries
  `answer_source` and `model_errors` but no mock label, which is in the audit record's `narrate`
  step.
- A failure is recorded as a call, a class and a status and nothing more: the endpoint's own
  message is kept in neither the trace, the answer nor the audit record, so an operator reads
  the reason from the endpoint.

## Evidence

- `tests/providers/test_providers.py`:
  `test_mock_abstains_from_routing_and_narrates_only_view_numbers`,
  `test_mock_chat_and_agent_are_honest_about_being_offline`,
  `test_provider_info_is_what_a_provider_declares`, `test_provider_from_env`.
- The one scripted case. `tests/providers/test_mock_script.py`: only the case's wording is
  scripted (any supplier and SKU ids; letter case and spacing are ignored), any other
  conversation gets the offline answer unchanged, every value the script sends is copied from
  the request or the fenced results, and it ends without a proposal when nothing is late, a
  read failed or the proposal was refused. `tests/cases/test_exploration_case.py` runs the case
  through the HTTP API on the mock tier: in the real Pi worker (the hand-over, three reads
  through the gate, a write that stops at the one-time approval and runs once when approved,
  the model shown fenced figures and no row), as a viewer (the write is never offered) and in
  the built-in loop. The Pi tests skip without the worker (`TWOTRACK_REQUIRE_PI=1` makes that a
  failure). `tests/cases/test_evidence_script.py` checks that the evidence script asks the
  wording the mock follows. [docs/EVIDENCE.md](../EVIDENCE.md) says what each case proves and
  what it does not.
- `tests/test_demo.py` runs the whole walkthrough on the mock; `uv run twotrack demo` prints
  "provider: mock (no API key)" and labels every narration step "mock template".
- `web/src/components/shell/model-indicator.test.tsx`, `web/src/lib/trace.test.ts` for the
  console labels.
- Failed calls. In `tests/providers/test_providers.py`,
  `test_guarded_provider_records_each_failed_call_and_re_raises` (the call, the class and the
  status are recorded and the error is raised again). `tests/graph/test_model_failures.py`: a
  refused call named in the trace and the answer, no model configured reading apart from a
  failing model, template text from the numeric lock not a failure, a failed chat call, a failed
  routing call alone. `tests/server/test_model_failures.py`: the REST answer and its stream, an
  approved write, the mock reporting none. `tests/mcp_export/test_mcp_model_failures.py`. For
  exploration, in `tests/explore/test_pi_runs.py`,
  `test_a_failed_model_call_is_reported_once_and_nothing_falls_back`.
- The console's three states: `web/src/components/hub/turn-view.test.tsx` ("who narrated: three
  states a reader can tell apart"), `web/src/components/approval/approval-outcome.test.tsx`,
  `web/src/lib/trace.test.ts`.
