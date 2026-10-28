# Decision records

Each record explains one design choice in twotrack: the problem, the options that were on the
table and what each costs, what was chosen, the price accepted for it, and the evidence in this
repository (tests, source files, the routing dev set). [ARCHITECTURE.md](../ARCHITECTURE.md) shows how
the pieces fit; these records show why they are shaped this way.

| ADR | Decision | Status | In one line |
|---|---|---|---|
| [0001](0001-code-computes-the-model-narrates.md) | Code computes, the model narrates | Accepted | A model sees aggregates, never rows, and a numeric lock removes any sentence whose numbers the result does not back. |
| [0002](0002-two-routing-arms-and-a-reconcile-matrix.md) | Two routing arms and a reconcile matrix | Accepted | Rules and a model give independent opinions; a fixed matrix turns agreement into a run and disagreement into a question. |
| [0003](0003-keyword-contests-settled-by-evidence.md) | Keyword contests are settled by evidence, not keyword length | Accepted | When two tools' keywords match different words, BM25 over the rest of the question decides, by a 2x margin or not at all. |
| [0004](0004-scope-guard.md) | Scope guard | Accepted | A question that names a filter the routed tool cannot apply is asked back, with the unfiltered run as a labelled choice. |
| [0005](0005-one-time-write-grants.md) | Every write waits for a one-time grant | Accepted | A write runs only with a single-use grant bound to user, target and params; a complete request goes straight to it. |
| [0006](0006-categories-extend-a-root-with-attrs.md) | Categories extend a root with attrs JSON | Accepted | A new item category is a plugin file: no core change, no new column, every report works on it. |
| [0007](0007-deterministic-import.md) | Deterministic import | Accepted | Columns map only through declared headers; ambiguity rejects the file; commit re-validates the same bytes. |
| [0008](0008-fail-closed-audit.md) | Fail-closed audit | Accepted | A write and its audit record commit together, a failed record stops the operation, and the last record replays the whole decision. |
| [0009](0009-honest-zero-key-mode.md) | An honest zero-key mode | Accepted | Without a key, a mock abstains where a model would decide and and its text is labelled as a template in the trace, the demo and the console. |
| [0010](0010-exploration-runs-in-pi-behind-the-same-gate.md) | Exploration runs in Pi, behind the same gate | Accepted | Open-ended analysis that no fixed flow covers runs in a restricted Pi worker process; it sees only exposed tools, every call meets the pipeline, and a write is only a proposal. |

## Format

Every record has the same sections:

- **Status**: accepted, superseded or proposed, and what proves it: the tests or files that pin
  the decision. A record cites no commit hash; a test name, a source path or another record
  stays checkable in the tree you have.
- **Context**: the problem, with the concrete case that raised it where there was one.
- **Options considered**: each with what it gives and what it costs.
- **Decision**: what the code does, with file paths.
- **Consequences**: the costs accepted, stated plainly.
- **Evidence**: tests and source files in this repository, cited by file and name. A record carries no
  benchmark result and no case from a deployment; its examples come from twotrack's own synthetic
  data (the demo seed, the test fixtures, the example plugins), which the records do not repeat
  for each example. Where a claim rests on a run that needs more than the test suite (a real model),
  the record names the tier and says what is not recorded.

A new decision gets the next number and a short, specific title. A decision that replaces an
older one says so in both records' status lines. A later refinement of a decision is added to its
Status; a record is corrected in place only where it says something the code does not do.
