# Benchmark results

`python -m bench.run` writes one JSON file here per run (`<UTC stamp>-<provider>-<model>.json`).
Everything in this directory except this README and its `.gitignore` is git-ignored: results are local until someone
decides to publish one.

Each file holds:

- `meta`: harness version, start and end time, provider, model id, endpoint **host** (never the
  full URL, never a key), data seed / scale / anchor date, data digests before and after each
  system ran, question-set digest and composition, settings (temperature, max turns, open task),
  the scripted-fake settings for dry runs, git commit and dirty flag, Python version;
- `summary`: per scored view (`A`, `C`, `B_seen`, `B_text`) the counts behind every rate;
- `records`: one per view, question and repeat: the question, the answer shown, every number in
  it with its role (`expected`, `echo`, `supported`, `unsupported`) and distractor match, the
  outcome, latency, token usage per model call, tool calls (A) or routing decision (B).

Render one with `uv run python -m bench.report bench/results/<file>.json`.

A file from `--provider mock` is a harness dry run (scripted fake model, mock provider). Its
numbers are not benchmark results; the report says so at the top.
