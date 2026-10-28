# ADR 0005: Every write waits for a one-time grant

## Status

Accepted. `tests/pipeline/test_pipeline_grants.py` and `tests/server/test_approvals.py` pin the
grant and the approval. Four refinements are part of Decision: a complete write goes straight
to its grant; every refusal of an approval answers with a precise status; a 403, 409 or 410
from the approvals endpoints leaves an audit record; and a request that reads as a question, a
negation or an exclusion never goes straight to a grant.

## Context

A write changes data everyone else reads. It must never run because routing guessed, and what a
person approves must be exactly what runs, even across a lost response, a second browser tab or
a computer whose clock is wrong.

The friction case came from the routing side. `create_po`'s keywords hold no bare verbs (create,
raise, 新建, 创建), because a verb alone does not name a write: "create a reorder plan" must reach
`reorder_plan`, and "创建一份采购计划" (create a purchasing plan) is not a purchase-order write.
Without the verbs, "please create a purchase order for 75 units of SKU-10205" matches one phrase
of the write, which counts as weak evidence, so it got two confirmations of the same request:
"did you mean Create purchase order?" from routing, then the grant's own approval.

## Options considered

For the gate:

1. **A client-side "are you sure?" dialog.**
   - For: simple.
   - Against: nothing binds the click to what runs; params can change between the dialog and the
     call; the server has nothing to verify or audit.
2. **Role permission alone.**
   - For: no friction.
   - Against: one misrouted question writes at once.
3. **A one-time grant bound to a digest of who, what and with which params** (chosen).
   - For: the server checks that the approved request is the one that runs, once.
   - Against: every write costs a click and a round trip.

For the friction:

1. **Put the bare verbs back.** Against: the misroutes above.
2. **Keep both confirmations.** Against: the second says nothing the first did not; the approval
   card already shows every param.
3. **A complete write is strong evidence** (chosen): one phrase of a write plus every param it
   requires goes straight to the grant; an incomplete request is confirmed first.

## Decision

- Every target declares its `effect` and its `approval`, with no default for either. The
  pipeline issues and consumes grants by `approval` alone, and the registry ties the two: a write
  must declare `approval='required'`, a read `approval='none'`, an external target either one
  (`core/contract.py`, `core/pipeline.py`). So every write waits for a grant, an external target
  that declares `approval='required'` gets one too, and no setting turns this off.
- A grant (`core/grants.py`) is a random token. The grants table stores only its SHA-256 (the server's pending-approvals
  table keeps the token itself, below). The grant is bound to the SHA-256 of the canonical JSON of
  user, target and validated params, valid 15 minutes, and consumed by one
  conditional `UPDATE`, so two concurrent attempts cannot both succeed. A consumed grant stays
  consumed even if the execution that follows fails.
- The browser never holds the token. Before anyone hears about a grant, the engine's pipeline
  wrapper stores the request and its token server-side under the public `grant_id`, the token in the
  clear until the approval is approved, rejected or expired (replaying the request needs it); if the database refuses it, it cancels the grant and reports an error (`server/approvals.py`).
- Only the requester can approve. The stored request replays through the same pipeline as the
  same user, so a role or precondition lost in the meantime, or params that no longer validate,
  stop it. Refusals (`server/routes/approvals.py`): 403 `not_requester`; 409 `grant_used` when it
  already ran, so it never runs twice; 409 `not_submitted` for an upload not yet submitted; 410
  `approval_expired`; 404 for an id never issued or a rejected request. An approval that loses a
  race to a concurrent approval is a late duplicate: it answers 409 `grant_used`, on record. One
  that loses to a concurrent rejection still answers 404: the grant was rejected, not used, so
  `grant_used` would be a false reason. Each 403, 409 and 410 the approvals endpoints answer is an audit record under
  the grant id, and a pipeline refusal of a presented grant names that grant as well. An import
  commit refused as expired is not recorded ([ADR 0008](0008-fail-closed-audit.md)).
- Time left (`expires_in_s`) is measured on the server's clock. The console counts down from it
  but the countdown alone expires nothing: a card expires when the server says so, with a 410 or by leaving the refreshed list.
- **Complete writes** (`rule_arm._complete_write`, `params.gives_required_params`). When the rule
  arm's ranking stands (no tie at the top; no contest, or a contest the hit won by
  `CONTEST_MARGIN` over rivals that matched only names of its own params,
  [ADR 0003](0003-keyword-contests-settled-by-evidence.md)) and its top hit is weak (one
  phrase, or a contest winner capped at weak) and belongs to a `write` target, and the question
  gives every required field of that target's params model, extracted and valid as given, the
  hit is strong. A target that requires nothing never
  qualifies; the lift is for effect `write` only, so an external target stays weak whatever it
  declares; a contest won over any other word stays weak; the lift comes after ranking, so it
  never outranks another hit. The trace records `strong_by: complete_params`.
- **Held-back requests** (`rule_arm._hold`). A request that reads as a question, negates the
  write's phrase or excludes a value is not asking for a new write, however completely it names
  one. For a target that changes data (a write or an external target) the top hit is then capped
  at weak on every path to strength: an example, two keywords, complete params. The target stays,
  and routing confirms it before anything is held for approval, also when a model agrees with
  it. The rules read grammar, not topic, from closed classes of words, and err toward holding: a
  wrong hold costs one click, a wrong release would put an approval in front of someone who did
  not ask for it. The `understand` step records `question`, `negated`, `excluded` and `capped`
  (what the hit was capped from, and by which of the three).
  - *Question* (`routing/interrogative.py`): a question mark, ASCII or full-width; in English, a
    clause that opens with an auxiliary or modal verb or a wh-word (after any of and, but, or,
    so), or "whether" anywhere; in Chinese, a particle that ends a clause (吗, 呢, 么), an
    A-not-A form (X不X or X没X, such as 有没有, 是不是, 能不能; also 是否, 能否, 可否) or a
    question word (什么, 谁, 哪 except in 哪怕, 怎, 为何, 如何, 何时, 多少, 多久).
  - *Negation* (`routing/negation.py`): a negator stands anywhere before the phrase of the top
    hit, or of a hit tied with it. English: don't (also dont), do not, never, no need. Chinese:
    别, 不要, 勿, 甭, 禁止, 不准, 不许, 不用, 不必, 无需, 无须, 不需要, 不需. A bare "not" or 不 is
    no negator. 别 negates wherever it is written, except inside a closed list of words where it
    is no imperative (特别, 类别, 别人, 别的 and others).
  - *Exclusion* (`routing/exclusion.py`): an identifier (a token with a digit, such as SUP-003, or
    capitals joined by a hyphen, such as WH-EAST) follows an exclusion marker in its clause, which
    runs to the next punctuation mark. English markers: not (as written, and every n't
    contraction), cannot, dont, never, no need, except, other than, excluding, instead of, rather
    than, without, avoid; "but" is none. Chinese markers: 不要用, 别用, 不用, 除了, 排除, 不选,
    不包括, 不从, and every Chinese negator above.
  - A param extractor skips a value more narrowly than this rule holds a write back: only when a
    marker stands right before it with nothing but connecting words between (articles and
    prepositions, a verb of using or choosing, the value's kind word, earlier values of a list).
    A "n't" contraction (also "dont") counts only with such a verb in between ("don't use
    SUP-003"); without one it belongs to a question or negates its own verb ("didn't SUP-003 deliver late?"). So
    "don't order from SUP-002" keeps SUP-002 as a param, and the request is still held back.

## Consequences

- Every write costs a person a click, even when the request is complete and they are sure.
- An approval lives 15 minutes; after that the user asks again.
- Waiting tokens sit in the clear in the pending-approvals table, in the same database as the
  audit log and the password hashes, inside the owner-only data directory; the grants table holds
  only their hashes. Whoever can read that file can read a waiting token. The browser never
  holds one.
- The complete-write rule trusts the plugin's extractor: one that reads the wrong number makes a
  request look complete. The approval card still shows every param, by its label, before
  anything runs.
- A command phrased as a question ("can you create a PO for ...") is confirmed once before its
  approval: one extra click, by design.
- A read takes no grant. An external target takes one only when it declares
  `approval='required'`; otherwise it runs at once on a read-only store, and routing confirms it
  only when the evidence for it is weak.

## Evidence

- `tests/pipeline/test_pipeline_grants.py`: reuse, another user's grant, tampered params,
  equivalent params, expiry, a forged token, a concurrent consume that succeeds once, a grant
  store failure that fails closed.
- `tests/server/test_approvals.py`: two simultaneous approvals run the write once; another user
  cannot approve; params that fail validation on replay do not write; a failed recording cancels
  the grant. `tests/server/test_approval_refusals.py`: 409 `grant_used`, 410 on every attempt, 404
  for an id never issued, each refusal audited under its grant id.
  `tests/server/test_approval_expiry.py`: the countdown follows the server's clock.
- `tests/routing/test_complete_writes.py`, and the dev set's `COMPLETE_WRITES`,
  `INCOMPLETE_WRITES` and `PLANS_BESIDE_WRITES` groups in `tests/routing/test_dev_phrasings.py`,
  written before the rule: complete requests reach the grant with nothing written; incomplete
  ones are confirmed first; a plan named next to a SKU and a quantity is not a write. Complete
  requests whose contest no target wins by the margin stay strict expected failures, because that
  contest is asked back.
- The held-back requests: `tests/routing/test_interrogative.py`, `tests/routing/test_negation.py`,
  `tests/routing/test_exclusion.py`, and in `tests/routing/test_complete_writes.py` that a
  question or a negated write is never lifted, that an external target is capped like a write
  (`test_an_external_target_is_capped_like_a_write`) and that a model that agrees does not make a
  held write strong (`test_a_model_that_agrees_does_not_make_a_held_write_strong`). The dev set's
  `QUESTIONS_ABOUT_WRITES`, `NEGATED_WRITES` and `EXCLUSIONS` groups cover the phrasings.
- Grants follow the declared approval: `tests/pipeline/test_pipeline_contract.py`
  (`test_an_external_target_that_requires_approval_uses_the_write_grant_flow`,
  `test_an_external_target_without_approval_runs_at_once`) and `tests/core/test_tool_contract.py`
  (the registry refuses a write that does not require approval, a read that does, and an
  explorable external target that does not).
- `uv run twotrack demo`: "create a purchase order for 40 units of SKU-…" goes from routing
  straight to "Confirmation needed", the trace reason ending "the question gives every param it
  requires"; after approval the audit chain reads needs_confirmation, started, ok.
