"""Render a result file as a markdown report: ``python -m bench.report <result.json>``.

The headline row for twotrack is "what the user sees" (narration plus the figures and table the
console shows); the narration-only row sits right under it so the two can be compared. A run in
which a model call failed opens with a DEGRADED banner and marks each view it touched.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .score import VIEW_LABELS, VIEWS, summarize, wilson

DRY_RUN_BANNER = (
    "> **DRY RUN.** Systems A and C used the scripted fake LLM (`bench/fake_llm.py`, told the "
    "answers, hallucinating on purpose) and system B used the zero-key mock provider. These "
    "numbers test the harness only. They are not benchmark results and must not be quoted.")
FELL_BACK = ("Answers that needed those calls fell back (twotrack: routing to rules and BM25, "
             "narration to the template; the baseline: the question ended unanswered), so this "
             "is not a clean result and its numbers must not be quoted. Fix the cause and re-run.")
REJECTED_HINT = ("HTTP 400 on every call usually means the endpoint rejects a parameter: some "
                 "models reject a non-default temperature (re-run with --omit-temperature).")


def failure_lines(meta: Mapping[str, Any]) -> list[str]:
    """One line per kind of failed model call: system, call, error class, HTTP status, count."""
    lines = []
    for kind in meta.get("failed_call_kinds") or ():
        status = f" (HTTP {kind['http_status']})" if kind.get("http_status") else ""
        lines.append(f"{kind['system']} {kind['call']}: {kind['error']}{status} x{kind['count']}")
    return lines


def _rejected(meta: Mapping[str, Any]) -> bool:
    return any(kind.get("http_status") == 400 for kind in meta.get("failed_call_kinds") or ())


def degraded_banner(meta: Mapping[str, Any]) -> str:
    kinds = "; ".join(failure_lines(meta))
    hint = f" {REJECTED_HINT}" if _rejected(meta) else ""
    return (f"> **DEGRADED RUN.** {meta.get('failed_calls', 0)} model call(s) failed"
            f"{f' ({kinds})' if kinds else ''}. {FELL_BACK}{hint}")


def degraded_warning(meta: Mapping[str, Any]) -> str:
    """What ``bench.run`` prints last when a model call failed."""
    rule = "=" * 72
    lines = [rule, f"DEGRADED RUN: {meta.get('failed_calls', 0)} model call(s) failed",
             *(f"  {line}" for line in failure_lines(meta)), FELL_BACK]
    if _rejected(meta):
        lines.append(REJECTED_HINT)
    lines += ["Exit code 3.", rule]
    return "\n".join(lines)


def _pct(k: int, n: int) -> str:
    return f"{100 * k / n:.1f}%" if n else "—"


def _pct_ci(k: int, n: int) -> str:
    if not n:
        return "—"
    low, high = wilson(k, n)
    return f"{100 * k / n:.1f}% [{100 * low:.0f}–{100 * high:.0f}]"


def _int(value: int | None, estimated: bool = False) -> str:
    if not value:
        return "0"
    return f"{'≈' if estimated else ''}{value:,}"


def _cost(block: Mapping[str, Any]) -> str:
    cost = block.get("cost_usd")
    if cost is None:
        return "n/a"
    note = " (= A)" if block.get("cost_shared_with_A") else ""
    return f"${cost:,.2f}{note}"


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def headline(summary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = ["| System | n | Correct [95% CI] | Wrong-number [95% CI] | Abstain | Routing acc "
             "| Avg latency ms | Prompt tok | Completion tok | Est. cost |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for view in VIEWS:
        block = summary.get(view)
        if not block:
            continue
        routing = block.get("routing")
        route = (f"{_pct(routing['ok'], routing['n'])} (strict "
                 f"{_pct(routing.get('strict_ok', 0), routing['n'])})" if routing else "—")
        label = VIEW_LABELS[view] + (" **(headline)**" if view == "B_seen" else "") + (
            " **(degraded)**" if block.get("degraded") else "")
        estimated = block.get("tokens_estimated", False)
        lines.append(
            f"| {label} | {block['n']} | {_pct_ci(block['correct'], block['n'])} | "
            f"{_pct_ci(block['wrong_number'], block['n'])} | "
            f"{_pct(block['abstained'], block['n'])} | {route} | "
            f"{_ms(block.get('latency_ms_avg'))} | "
            f"{_int(block.get('prompt_tokens'), estimated)} | "
            f"{_int(block.get('completion_tokens'), estimated)} | {_cost(block)} |")
    return lines


def breakdown(summary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = ["| System | Unsupported | Contradiction | Caveated stand-ins | Abstain (answerable) "
             "| Correct (unanswerable) | Tool hit (A/C) | p95 latency ms | Model calls "
             "| Errors | Spelled-out numbers |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for view in VIEWS:
        block = summary.get(view)
        if not block:
            continue
        hit = block.get("tool_hit")
        errors = block.get("errors", 0)
        failed = block.get("call_errors") or {}
        if failed:
            errors_text = f"{errors} (calls: " + ", ".join(
                f"{k} {v}" for k, v in sorted(failed.items())) + ")"
        else:
            errors_text = str(errors)
        lines.append(
            f"| {VIEW_LABELS[view]} | {_pct(block['unsupported_answers'], block['n'])} | "
            f"{_pct(block['contradiction_answers'], block['n'])} | {block['caveated']} | "
            f"{_pct(block['abstained_answerable'], block['answerable'])} | "
            f"{_pct(block['correct_unanswerable'], block['unanswerable'])} | "
            f"{_pct(hit['ok'], hit['n']) if hit else '—'} | "
            f"{_ms(block.get('latency_ms_p95'))} | {block.get('model_calls', 0)} | "
            f"{errors_text} | {block.get('spelled_out', 0)} |")
    return lines


def by_group(summary: Mapping[str, Mapping[str, Any]], key: str, title: str) -> list[str]:
    views = [v for v in VIEWS if v in summary]
    groups: list[str] = []
    for view in views:
        for group in summary[view].get(key, {}):
            if group not in groups:
                groups.append(group)
    if not groups:
        return []
    header = f"| {title} | n | " + " | ".join(VIEW_LABELS[v].split("  ")[0] + (
        " seen" if v == "B_seen" else " text" if v == "B_text" else "") for v in views) + " |"
    lines = [header, "|---|---:|" + "---:|" * len(views)]
    for group in groups:
        cells, n = [], 0
        for view in views:
            block = summary[view].get(key, {}).get(group)
            if not block:
                cells.append("—")
                continue
            n = max(n, block["n"])
            cells.append(f"{_pct(block['correct'], block['n'])} / "
                         f"{_pct(block['wrong_number'], block['n'])}")
        lines.append(f"| {group} | {n} | " + " | ".join(cells) + " |")
    return lines


def route_sources(summary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    sources = (summary.get("B_seen") or {}).get("route_sources")
    if not sources:
        return []
    lines = ["| Decided by (source:kind) | Answerable questions | Routed to an accepted target |",
             "|---|---:|---:|"]
    for key, row in sources.items():
        lines.append(f"| {key} | {row['n']} | {_pct(row['routed_ok'], row['n'])} |")
    return lines


def repeat_spread(summary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = []
    for view in VIEWS:
        block = summary.get(view)
        runs = (block or {}).get("by_repeat", {})
        if len(runs) < 2:
            continue
        correct = [r["correct"] / r["n"] for r in runs.values() if r["n"]]
        wrong = [r["wrong_number"] / r["n"] for r in runs.values() if r["n"]]
        lines.append(f"- {VIEW_LABELS[view]}: correct {100 * min(correct):.1f}–"
                     f"{100 * max(correct):.1f}%, wrong-number {100 * min(wrong):.1f}–"
                     f"{100 * max(wrong):.1f}% across {len(runs)} repeats")
    return lines


def render(results: Mapping[str, Any]) -> str:
    meta = results["meta"]
    summary = results.get("summary") or summarize(results["records"])
    data = meta["data"]
    qs = meta["question_set"]
    git = meta.get("git") or {}
    settings = meta["settings"]
    lines = [f"# Wrong-number benchmark: {meta['provider']} / {meta['model']}", ""]
    if meta["provider"] == "mock":
        lines += [DRY_RUN_BANNER, ""]
    if meta.get("degraded"):
        lines += [degraded_banner(meta), ""]
    timeouts = ""
    if settings.get("route_timeout_s") is not None:
        timeouts = (f", timeouts {settings['timeout_s']:g} s per call and "
                    f"{settings['route_timeout_s']:g} s for twotrack's routing opinion")
    lines += [
        f"- Date: {meta['started_at']} → {meta['finished_at']}",
        f"- Model: `{meta['model']}` via `{meta['endpoint_host']}`",
        f"- Data: seed {data['rng_seed']}, scale {data['scale']}, today {data['today']}; "
        f"data unchanged by the run: {'yes' if data.get('unchanged') else 'NO'}",
        f"- Questions: {qs['run']} of {qs['total']} (set digest `{qs['digest']}`), "
        f"repeats: {meta['repeats']}",
        f"- Settings: temperature {settings['temperature']}{timeouts}, baseline max turns "
        f"{settings['max_turns']}, twotrack open task "
        f"{'on' if settings['allow_open_task'] else 'off'}",
        f"- Harness {meta['harness_version']}, commit `{git.get('commit', 'unknown')[:12]}`"
        f"{' (uncommitted changes)' if git.get('dirty') else ''}",
        "",
        "## Results",
        "",
        *headline(summary),
        "",
        "Headline: twotrack is scored on what the user sees (the locked narration plus the "
        "figures and table the console shows); the narration-only row is directly below it. "
        "Wrong-number = the answer shows at least one number that is neither expected nor "
        "derivable from that run's tool outputs, or a wrong-scope stand-in for the asked "
        "figure. Read it together with Correct and Abstain: abstaining on everything scores 0% "
        "wrong-number. Brackets are 95% Wilson intervals.",
        "",
        "## Breakdown",
        "",
        *breakdown(summary),
        "",
        "### By category (correct / wrong-number)",
        "",
        *by_group(summary, "by_category", "Category"),
        "",
        "### By language (correct / wrong-number)",
        "",
        *by_group(summary, "by_lang", "Language"),
        "",
        "### By tag (correct / wrong-number)",
        "",
        *by_group(summary, "by_tag", "Tag"),
    ]
    sources = route_sources(summary)
    if sources:
        lines += ["", "### twotrack routing by deciding arm", "", *sources, "",
                  "`rule` = keyword/example match, `llm` = the model arm, `semantic` = BM25 "
                  "fallback, `*:clarify` / `*:chat` = asked back or no report ran. Keyword "
                  "routing benefits from questions phrased in the tools' own vocabulary."]
    spread = repeat_spread(summary)
    if spread:
        lines += ["", "### Spread across repeats", "", *spread]
    lines += ["", "Definitions, question set and threats to validity: `bench/README.md`."]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.report",
                                     description="Render a benchmark result as markdown.")
    parser.add_argument("result", help="a result JSON file from bench.run")
    parser.add_argument("--out", default=None, help="write the markdown here")
    args = parser.parse_args(argv)
    results = json.loads(Path(args.result).read_text(encoding="utf-8"))
    text = render(results)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
