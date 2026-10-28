# ADR 0002: Two routing arms and a reconcile matrix

## Status

Accepted. `tests/routing/test_reconcile_matrix.py` pins every cell of the matrix. Contests inside
the rule arm are [ADR 0003](0003-keyword-contests-settled-by-evidence.md); a request that holds
a write back (a question, a negation, an exclusion) is [ADR 0005](0005-one-time-write-grants.md).

## Context

Routing picks one tool or workflow for a question. The console must work with no model key at
all; the same question should reach the same target so that the audit log means something; a
model should still be able to help with paraphrases; and a write must never run because a model
guessed. Cost and latency per question should be bounded and visible.

## Options considered

1. **Model-only routing** (native tool calling).
   - For: handles paraphrase and loose wording; no keywords to maintain.
   - Against: a model call per question; not reproducible run to run; nothing works without a
     key; a hallucinated or role-hidden name still has to be caught; a write is chosen by a
     guess.
2. **Rules only** (keywords and examples).
   - For: free, instant, deterministic, explainable in one line.
   - Against: misses paraphrases; keyword collisions between tools; no way to break a tie.
3. **Both arms, reconciled by an explicit matrix, with a BM25 fallback** (chosen).
   - For: deterministic whenever the rule arm is strong; the model adds coverage and breaks rule
     ties; a disagreement becomes a question to the user instead of a coin flip; the zero-key
     mode keeps the rules and BM25; every cell of the matrix is a test.
   - Against: two arms and their thresholds to maintain; with a real model, one routing call per
     question even when the rules are certain.

## Decision

`decide_route` (`core/routing/router.py`) builds the candidate set first; both arms only ever see
it. The LLM arm (`llm_arm.py`) drops a name outside the set and treats it, a confidence below
`LLM_MIN` (0.3) and a provider failure as no opinion; the audit records such a name as
`invalid`, never the raw string. A routing reply that cannot be read (not JSON, not an object, no
usable target or confidence, params that are not JSON values) is a provider failure too
(`UnparseableReply`): the opinion is traced as `failed` and the request's model errors list it.
A null or `none` target is an abstention, not a failure. Reconcile (`reconcile.py`), with R the rule arm's top hit
(strong at 0.9 and above) and L the model's opinion (strong at 0.8 and above):

| R \ L | silent | same target | strong, differs | weak, differs |
|---|---|---|---|---|
| silent | fallback | n/a | confirm L (*) | confirm L |
| weak | run R, write gated | run | confirm L, offer R | ask R or L |
| strong | run R | run | ask R or L | run R |
| tied R1..Rn | ask among R | run the R it names | ask R.. or L | ask R.. or L |

(*) A read-only target with confidence at or above `LLM_AUTO_ACCEPT` (0.9) runs directly with
source `llm`. Anything else the model proposes alone is offered back as a one-option
confirmation with its params kept, so one click confirms it.

Where the arms agree on a write, that is strong evidence unless the request holds the write back
(it reads as a question, negates the write's phrase or excludes a value, [ADR 0005](0005-one-time-write-grants.md)):
then the write is confirmed first, as without agreement.

When both arms are silent: a BM25 hit at a normalized score of at least 0.5 (source `semantic`),
else exploration, the `open_task` route, if enabled (source `agent`, off by default; the router
records a `handover` step, [ADR 0010](0010-exploration-runs-in-pi-behind-the-same-gate.md)),
else chat (source `fallback`). Writes: a write or external target reached without strong evidence (an explicit
call, both arms agreeing, or a strong rule hit) becomes a one-option confirmation. A model alone
never runs a write, at any confidence, and neither does a semantic hit. The pipeline's one-time
grant still guards the execution itself ([ADR 0005](0005-one-time-write-grants.md)).

## Consequences

- **Cost and latency.** With a real provider every question that names no explicit target makes
  one routing call, made after the rule arm and not skipped when the rules are certain. The call
  runs in JSON mode with no retries (`providers/openai_compat.py`) and waits
  `TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS` (default 10 s; the answering calls wait
  `TWOTRACK_LLM_TIMEOUT_SECONDS`, default 30 s; `docker-compose.yml` sets 90 and 120 for a model
  on a CPU), so a slow endpoint costs one timeout. Its temperature is 0 unless
  `TWOTRACK_LLM_TEMPERATURE` is a number from 0 to 2 (that value for every call) or `omit` (none
  is sent, for models that reject a non-default temperature). The request lists the capabilities
  first and the question last, so the system prompt and the capability list are one prefix shared
  by every question, which a server with prefix caching can reuse.
- **Determinism.** The rule arm is a pure function of the question and the candidate set; plugins
  load in sorted order and tool cards are sorted by name, so the routing prompt is identical
  across processes. The model's opinion is the only nondeterministic input, and the trace records
  it next to the rule arm's.
- **Zero-key mode.** The mock abstains from routing ([ADR 0009](0009-honest-zero-key-mode.md));
  the rule arm and BM25 carry everything. A paraphrase that avoids the keywords is left to BM25,
  and to chat when BM25 stays under its threshold. The dev set (`tests/routing/test_dev_phrasings.py`)
  keeps the paraphrase misses it knows about as strict expected failures, each with its reason:
  some end in chat, one in a report that shares a single declared word.
- **More questions back.** Disagreement and ties ask the user. The audit's per-source counts (`GET /api/audit/stats`) show how often each arm decided.
- **Hand-set thresholds**: 0.6 (one keyword), 0.9 (two keywords) and 1.0 (an example) for the
  rule arm; 0.3, 0.8 and 0.9 for the model. They are named constants, and every cell they decide
  is pinned by a test, so changing one is a visible decision.

## Evidence

- `tests/routing/test_reconcile_matrix.py`: one case per cell of the matrix and of the fallback,
  including `rule_silent__llm_very_high_write` (confirmed, not run) and
  `semantic_hit_write__gated`.
- `tests/routing/test_router.py`; `tests/core/test_registry.py` for the candidate filter (roles,
  preconditions, a raising precondition fails closed and is traced).
- `twotrack demo` shows a rule decision, a semantic decision (`rank our sources by rating`, no
  keyword matches) and a chat fallback, each with its route source in the trace and the audit.
