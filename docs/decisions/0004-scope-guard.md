# ADR 0004: Scope guard: a right number for the wrong question is worse than no answer

## Status

Accepted. `tests/plugins/test_scope_guard.py` and `tests/core/test_scope_vocabulary.py` pin the
guard and the vocabulary. A one-word value synonym matches only as written (the false positive
in Decision).

## Context

The case that raised this: no tool could filter by item category, so "how many packaging SKUs
are low on stock" matched `low_stock` by keyword, ran it with no filter and answered with the count
of every low-stock SKU, packaging and spare parts together. The answer did name its scope, "all
SKUs, summed across warehouses", but the figure it led with answered a question nobody asked.
The number was correct, for a different question. The numeric lock cannot catch this: the result backs the number
([ADR 0001](0001-code-computes-the-model-narrates.md)). The benchmark calls it a stand-in: a
figure equal to the asked quantity with one of the question's filters dropped
(`bench/README.md`, Scoring).

## Options considered

1. **Add the missing filters to the tools and stop there.**
   - For: answers the common case correctly.
   - Against: some tool will always lack some filter (criticality on late orders), and that gap
     stays silent.
2. **Let the model notice the gap and add a caveat.**
   - For: no new machinery.
   - Against: the wrong figure is still the headline; a caveat in prose is not enforced; zero-key
     mode has no model to notice.
3. **Refuse any question that names a filter the tool cannot apply.**
   - For: no wrong-scope answer can be shown.
   - Against: throws away the unfiltered answer, which is often what the user wants once told.
4. **A vocabulary built from the registry, declared bindings, and a clarification with an
   explicitly labelled unfiltered option** (chosen).
   - For: correct answers where a filter exists, an honest question where it does not, and the
     unfiltered figure still one click away, labelled as such.
   - Against: more questions back; plugins must declare their vocabulary.

## Decision

- **Vocabulary** (`core/scope.py`), built at registry load from declarations only. A category
  (an entity that extends a root) is named by its id, its label in every language and its
  synonyms. A value of an enum field declared `scope_key=True` is named by its value synonyms,
  and by itself when it reads like a code (`WH-EAST`); a value too short to match safely, like
  criticality `A`, is named only through phrases such as "class A" or "A类". Matching is
  conservative: Latin phrases match whole words, a phrase of only one- or two-letter words or only
  stopwords never matches, a Chinese phrase needs two characters, and overlapping matches keep
  the longest. A declared synonym that can never match, or a phrase that names two values of one
  constraint, fails the registry load.
- **Bindings** (`core/params.py`). A param declares the constraint it applies:
  `json_schema_extra={"binds": "category:sku"}`. Routing fills an unset bound param from the
  question; a param already set by an explicit call, the model or the extractor wins.
  `implied_scope` declares a constraint a target always applies (`create_po` only creates drafts;
  the drop-in cold-chain report only covers cold-chain items).
- **The guard** (`core/routing/scope_guard.py`) runs on the target the ladder chose, or the one
  an explicit call named. A named constraint the target cannot apply, an excluded one ("except packaging", "除了包装"), or two
  values for a param that takes one, turns the decision into a clarification. Its choices: the
  same target without the filter, labelled ("Show all categories") and marked unfiltered; up to
  two candidates of the same effect that can apply the filter (none for an exclusion, which no
  target applies); or, for a conflict, one run per value.
- **Acknowledgement.** The unfiltered choice sends `_scope_ack` with the ids of the constraints
  it runs without. Only the user's explicit pick may acknowledge (the key is stripped from any
  other params), it covers only the constraints it names, and the answer's provenance says the
  result is unfiltered.
- A `scope` trace step records what the question mentioned and what was bound, unbound or in
  conflict, whenever the question names a constraint or carries an acknowledgement; it stays in
  the audit replay.

**The false positive.** An earlier rule made "draft a replenishment plan for class B items" came back as
"“Reorder plan” can't be limited to status draft": "drafts" was a value synonym of the PO status
`draft`, and plural folding turned it into the bare word "draft", here a verb. The fix is in the
rule, not the word: a one-word value synonym matches only as written, while a longer phrase still
matches with its plural folded. The PO status declares phrases ("draft purchase orders",
"draft PO", and "drafts", which matches only as written) instead of the bare word "draft".

## Consequences

- More questions are asked back instead of answered; a real gap costs the user one click.
- The guard knows only the constraints plugins declare. A question routed to the wrong tool that
  happens to accept the named filter is not caught (threat 11 in `bench/README.md`).
- Plugin authors declare synonyms and scope keys in each language; core holds no domain words.
- The alternatives are ranked by the constraints they cover and by keyword and BM25 relevance, and
  can be loosely related. "How many late purchase orders are for class A SKUs?" offers
  the unfiltered late-shipments run first, then `reorder_plan` and `low_stock`, which can filter
  by criticality but answer about stock.
- A false mention turns an answer into a question, which is why matching errs toward missing a
  mention.

## Evidence

- The cases today: "how many packaging SKUs are low on stock" runs `low_stock` with
  `category=packaging` and the scope reads "Category: Packaging"; "How many late purchase orders
  are for class A SKUs?" is asked back, with the unfiltered run as the first choice. The tests
  below pin the mechanism.
- `tests/plugins/test_scope_guard.py`: a named category fills the filter, given params win, an
  excluded constraint is never applied as a filter, an acknowledged run goes through once and
  says it is unfiltered, only the user's explicit pick can acknowledge, the scope step is in the
  audit replay.
- `tests/core/test_scope_vocabulary.py`: `test_no_false_mentions` ("draft a reorder plan", "make a
  draft plan for the buyer"), `test_a_one_word_value_synonym_matches_only_as_written`,
  `test_ambiguous_or_unmatchable_phrases_fail_the_load`. `tests/server/test_scope_api.py`.
- Benchmark: the items the category filter makes answerable expect the filtered call, and items
  tagged `beyond_params` ask for filters no tool accepts, so the guard stays measured
  (`bench/README.md`).
