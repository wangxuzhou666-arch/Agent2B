# ADR 0003: Keyword contests are settled by evidence, not keyword length

## Status

Accepted. `tests/routing/test_contest.py` and the dev phrasing set
(`tests/routing/test_dev_phrasings.py`) pin it. The evidence is bilingual, and a contested
complete write is narrowed (the exception in Decision).

## Context

The rule arm ranked its hits by strength, then number of keywords, then matched characters. With
no API key, "late purchase orders" ran `po_status` and listed every purchase order: `po_status`
matched "purchase orders", `late_shipments` matched "late", and the longer keyword won. The same
race sent "延迟的采购订单" to `po_status` and "which suppliers deliver late" to
`supplier_compare`. How long a keyword is says how a plugin author wrote it, not what the user
asked for.

## Options considered

1. **Tune keywords** (add "late purchase orders" to `late_shipments`).
   - For: a one-line fix for the case at hand.
   - Against: every pair of tools needs its combined phrases, in two languages; a plugin author
     cannot foresee another plugin's words.
2. **Weight hits by specificity.**
   - For: small change to the ranking.
   - Against: still a length race; "purchase orders" is a legitimately specific phrase.
3. **Always ask when two targets match.**
   - For: never wrong.
   - Against: asks about questions whose answer is plain, many times a day.
4. **Defer to the model.**
   - For: models handle this well.
   - Against: there is no model in zero-key mode, and a call is spent on what the declared
     evidence already decides.
5. **Treat hits on different words as a contest and score it by evidence** (chosen).
   - For: decides the clear cases, asks on the close ones, uses only what plugins declare.
   - Against: a margin to choose; decisions now depend on how well tools describe themselves.

## Decision

- Every hit records where its keywords matched (`spans`, `core/routing/rule_arm.py`). Going down
  the ranking, a hit is independent when one of its spans overlaps no span of an independent hit
  above it. Overlapping matches still go to the more specific one: "create a purchase order"
  explains away "purchase order".
- Two or more independent hits are a contest. Each contender is scored on the words its own
  keywords did not match: BM25 of those words against its evidence, over the IDF mass of the
  whole question (`semantic.explanation_scores`). Evidence is the name, the description in every
  language, keywords, examples and the params it accepts, by name and by their label in the
  other language. The route hint is left out, because a hint names rivals ("for overdue ones use
  late_shipments") and its words would count for the wrong side.
- The best contender wins only with at least `CONTEST_MARGIN` = 2 times the runner-up's score;
  otherwise the close contenders go back to the user as a clarification.
- A contested result is a weak signal: the winner is capped at weak, a write it names is
  confirmed before it runs, and a model opinion can overrule it
  ([ADR 0002](0002-two-routing-arms-and-a-reconcile-matrix.md)).
- One exception, for a complete write ([ADR 0005](0005-one-time-write-grants.md)): the winner is
  a write and the question gives every param the write requires. It is lifted back
  to strong only if it cleared `CONTEST_MARGIN` and every keyword its losing contenders matched
  is the name, or a label in any language, of one of the winner's own params
  (`rule_arm.param_words`). A question that names its supplier also matches the word "supplier"
  (供应商) of a supplier report, and the approval card shows that word as the write's supplier
  param, so that contest does not hold the write back. A loser that matched anything else
  ("reorder plan", "vendor", "low on stock") may have asked for something the approval card does
  not show, so the hit stays weak and routing confirms the write first. A contest no target won
  by the margin is asked back.
- The evidence is bilingual: tool descriptions take `{"en", "zh"}`, and the
  particles and conjunctions 的 了 和 与 或 split a Chinese run before bigrams are made, so the
  grammar of a description is no evidence for the grammar of a question.

## Consequences

- Close calls ask. "suggested reorder quantity for low stock items" now asks between
  `reorder_suggestion` and `low_stock` (0.53 against 0.36 of the rest of the question, inside the
  margin); the dev set keeps it as a strict expected failure with those scores.
- A tool's description is routing evidence, in every language it declares, so writing it is part
  of building the tool.
- A contested question builds a BM25 index over the candidates' evidence on every call; nothing
  is cached. It is pure Python and the candidate set is small, but it grows with the registry.

## Evidence

- **Dev phrasing set** (`tests/routing/test_dev_phrasings.py`): English and Chinese phrasings
  written before each routing change, each stating what the user asked for. The items the router
  still gets wrong stay as strict expected failures with their reasons (close contests that now
  ask, a contest lost to a shared word, paraphrases outside the keywords), so a change that fixes
  one has to remove its mark. The Chinese items were written before the bilingual evidence; the dev set's
  docstring says which groups came before which change.
- The case itself: in "late purchase orders", `late_shipments` explains 0.34 of the
  words its keyword did not match and `po_status` explains 0, because nothing `po_status`
  declares mentions lateness; `late_shipments` wins and the reason in the trace names both.
- `tests/routing/test_contest.py`: contained matches are not contests, the contest is in the
  trace, route hints are not evidence, a contested write never runs on keywords alone.
- **Discipline.** The margin was set before the held-out benchmark set was run and has not
  changed. The held-out set is never used for tuning: it changes only when a capability
  changes, and the digest of the set a result was scored on is in its result file
  (`bench/README.md`).
- `tests/routing/test_complete_writes.py`, section "a contested write": a contest won over names
  of the write's own params lifts a complete write
  (`test_a_contest_won_over_names_of_its_params_lifts_a_complete_write`,
  `test_a_contest_won_over_the_supplier_label_lifts_it_like_no_contest`); one won over any other
  word is confirmed first (`test_a_contest_won_over_any_other_word_is_confirmed_first`,
  `test_a_contest_won_over_other_words_is_confirmed_first`); a contest no target won is never
  lifted (`test_a_contest_no_target_won_or_a_question_is_never_lifted`).
