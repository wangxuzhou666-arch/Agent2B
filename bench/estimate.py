"""Budget estimator: build every prompt a real run would send, offline, and size it.

``python -m bench.estimate --models <id>,<id> [--prices prices.json] [--repeats 3]``

How the numbers are made:

* Prompt tokens are *measured*: the baseline loop runs against the scripted fake (which calls
  the expected tool once, then answers), and twotrack runs through the real
  ``OpenAICompatProvider`` against a client that replies like the mock provider. Every request
  body (messages, tool schemas, response format) is serialized exactly as it would be sent and
  converted at chars / 4, plus one token per CJK character. Tokenizers differ by model, so
  treat this as +/- 25%.
* Completion tokens are *assumed* per call kind (``COMPLETION_TOKENS``), since offline replies
  say nothing about a real model's verbosity.
* ``expected`` follows the scripted call pattern. ``high`` multiplies by ``HIGH_FACTOR``: a real
  baseline model often makes a second tool call and re-sends the grown context; twotrack's
  call count is fixed (one route call, plus one narration when a tool ran).
* C adds no calls: it reuses A's answers. Repeats multiply everything.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from twotrack.providers.openai_compat import OpenAICompatProvider

from .baseline import run_baseline
from .config import DEFAULT_MAX_TURNS, estimated_cost, load_prices
from .fake_llm import MockEquivalentClient, ScriptedLLM
from .metering import CallRecord, MeteredClient
from .questions import Item, build_questions
from .systems import make_engine, run_twotrack
from .world import build_world

COMPLETION_TOKENS = {"route": 40, "narrate": 120, "chat": 80, "understand": 60, "agent": 120,
                     "baseline_call": 60, "baseline_answer": 150, "other": 100}
HIGH_FACTOR = {"A": 2.0, "B": 1.25}


@dataclass
class SystemEstimate:
    system: str
    questions: int
    calls: int
    prompt_tokens: int
    completion_tokens: int
    largest_prompt: int

    def high(self) -> tuple[int, int]:
        factor = HIGH_FACTOR.get(self.system, 1.0)
        return round(self.prompt_tokens * factor), round(self.completion_tokens * factor)


def measure(items: Sequence[Item], *, max_turns: int = DEFAULT_MAX_TURNS,
            systems: Sequence[str] = ("A", "B")) -> dict[str, list[CallRecord]]:
    out: dict[str, list[CallRecord]] = {}
    if "A" in systems:
        world = build_world()
        fake = ScriptedLLM(hallucination_rate=0.0)
        metered = MeteredClient(fake)
        out["A"] = []
        try:
            for item in items:
                fake.bind(item)
                run_baseline(metered, "estimate", item.text, world, max_turns=max_turns)
                out["A"] += metered.take()
        finally:
            world.close()
    if "B" in systems:
        world = build_world()
        metered = MeteredClient(MockEquivalentClient())
        engine = make_engine(world, OpenAICompatProvider(model="estimate", client=metered))
        out["B"] = []
        try:
            for item in items:
                run_twotrack(engine, world, item.text)
                out["B"] += metered.take()
        finally:
            world.close()
    return out


def _completion(system: str, call: CallRecord) -> int:
    if system == "A":
        key = "baseline_call" if call.tool_calls else "baseline_answer"
    else:
        key = call.purpose if call.purpose in COMPLETION_TOKENS else "other"
    return COMPLETION_TOKENS[key]


def summarize_calls(calls_by_system: dict[str, list[CallRecord]],
                    questions: int) -> dict[str, SystemEstimate]:
    return {system: SystemEstimate(
        system=system, questions=questions, calls=len(calls),
        prompt_tokens=sum(c.prompt_tokens or 0 for c in calls),
        completion_tokens=sum(_completion(system, c) for c in calls),
        largest_prompt=max((c.prompt_tokens or 0 for c in calls), default=0))
        for system, calls in calls_by_system.items()}


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def render_estimate(estimates: dict[str, SystemEstimate], *, models: Sequence[str],
                    prices: dict[str, dict[str, float | None]], repeats: int = 1,
                    systems: Sequence[str] = ("A", "B", "C")) -> str:
    lines = ["| Model | System | Calls | Prompt tok (expected / high) | "
             "Completion tok (expected / high) | Est. cost (expected / high) |",
             "|---|---|---:|---:|---:|---:|"]
    wanted = set(systems)
    if "C" in wanted:
        wanted.add("A")  # C is computed from A's answers: it needs A's calls, nothing more
    for model in models or ["(any model)"]:
        total = [0, 0, 0, 0]
        for system in ("A", "B"):
            est = estimates.get(system)
            if est is None or system not in wanted:
                continue
            prompt, completion = est.prompt_tokens * repeats, est.completion_tokens * repeats
            high_p, high_c = (v * repeats for v in est.high())
            low_cost = estimated_cost(prices, model, prompt, completion)
            high_cost = estimated_cost(prices, model, high_p, high_c)
            label = "A (+C, no extra calls)" if system == "A" and "C" in systems else system
            lines.append(f"| {model} | {label} | {est.calls * repeats:,} | {prompt:,} / "
                         f"{high_p:,} | {completion:,} / {high_c:,} | {_money(low_cost)} / "
                         f"{_money(high_cost)} |")
            total = [total[0] + prompt, total[1] + completion, total[2] + high_p,
                     total[3] + high_c]
        lines.append(f"| {model} | **total** | | {total[0]:,} / {total[2]:,} | "
                     f"{total[1]:,} / {total[3]:,} | "
                     f"{_money(estimated_cost(prices, model, total[0], total[1]))} / "
                     f"{_money(estimated_cost(prices, model, total[2], total[3]))} |")
    largest = max((e.largest_prompt for e in estimates.values()), default=0)
    lines += ["", f"Largest single request: ≈{largest:,} prompt tokens (the model's context "
              "window must hold it). Tokens ≈ chars/4 (+1 per CJK char); completions assumed. "
              "Costs need a price entry in bench/config.py PRICES or --prices."]
    return "\n".join(lines)


def plan_text(config: Any) -> str:
    """The pre-flight summary ``bench.run`` prints before a real run is confirmed."""
    from .run import endpoint_host, select_items, temperature_label

    world = build_world()
    try:
        items = select_items(build_questions(world), config)
    finally:
        world.close()
    wanted = set(config.systems)
    calls_needed = {"A"} if wanted & {"A", "C"} else set()
    if "B" in wanted:
        calls_needed.add("B")
    measured = measure(items, max_turns=config.max_turns, systems=sorted(calls_needed))
    estimates = summarize_calls(measured, len(items))
    head = [f"Planned real run: systems {', '.join(sorted(wanted))}, model `{config.model}` via "
            f"`{endpoint_host(config.base_url, config.provider)}`, {len(items)} questions x "
            f"{config.repeats} repeat(s).",
            f"Temperature: {temperature_label(config)}; timeouts: {config.timeout:g} s per call, "
            f"{config.route_timeout:g} s for twotrack's routing opinion.", ""]
    return "\n".join(head) + render_estimate(estimates, models=[config.model],
                                             prices=config.prices, repeats=config.repeats,
                                             systems=sorted(wanted))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.estimate",
                                     description="Estimate tokens and cost of a real run.")
    parser.add_argument("--models", default="", help="comma-separated model ids to price")
    parser.add_argument("--prices", default=None, help="JSON price table (see bench/config.py)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    args = parser.parse_args(argv)
    prices = load_prices(args.prices)
    models = [m for m in args.models.split(",") if m] or sorted(prices)
    world = build_world()
    try:
        items = build_questions(world)
    finally:
        world.close()
    items = items[:args.limit] if args.limit else items
    estimates = summarize_calls(measure(items, max_turns=args.max_turns), len(items))
    print(f"{len(items)} questions x {max(1, args.repeats)} repeat(s); systems A, B, C")
    print()
    print(render_estimate(estimates, models=models, prices=prices,
                          repeats=max(1, args.repeats)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
