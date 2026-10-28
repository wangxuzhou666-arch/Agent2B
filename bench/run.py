"""Run the benchmark and write one result file.

Dry run (no network, scripted fake for A/C, zero-key mock for B)::

    uv run python -m bench.run --system all --provider mock

Real run (only after the budget is approved; refuses to start without ``--confirm``)::

    TWOTRACK_LLM_BASE_URL=... TWOTRACK_LLM_API_KEY=... \\
        uv run python -m bench.run --system all --provider openai_compat \\
        --model <model id> --repeats 3 --confirm

The API key is read from ``TWOTRACK_LLM_API_KEY`` only (never a flag, so it stays out of shell
history), is never written to disk or printed, and the result file is scrubbed for it before
it is written. Result files record the endpoint host, never the full URL.

Temperature: every call at 0 by default. ``--no-temperature-pin`` keeps each system's own
(twotrack's per-call values, the endpoint's default for the baseline); ``--omit-temperature``
sends none at all, for models that reject a non-default temperature. twotrack gets either
through its provider's ``temperature`` setting, the one ``TWOTRACK_LLM_TEMPERATURE`` sets in a
deployment.

Timeouts: ``--timeout`` (every call but twotrack's routing opinion: the baseline's turns,
twotrack's narration) and ``--route-timeout`` default to ``TWOTRACK_LLM_TIMEOUT_SECONDS`` and
``TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS``, checked as the server checks them; unset, 60 s and the
product's 10 s.

A model call that fails (an HTTP error, a timeout, an unparseable reply) degrades the run: the
answers that needed it fell back, so its numbers are not a clean result. The result file says
so (``meta.degraded``, ``meta.failed_calls``), the report opens with a banner, and the exit code
is 3. Exit codes: 0 a clean run; 1 a setting is missing or malformed; 2 bad arguments, or a real
run without ``--confirm`` (nothing was called); 3 a degraded run.
"""

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from twotrack.providers import LLM_ROUTE_TIMEOUT_ENV, LLM_TIMEOUT_ENV, MIN_TIMEOUT_SECONDS
from twotrack.providers.mock import MockProvider
from twotrack.providers.openai_compat import OpenAICompatProvider
from twotrack.settings import OMIT_TEMPERATURE, SettingsError, _number

from . import HARNESS_VERSION
from .baseline import run_baseline
from .config import (
    DEFAULT_MAX_TURNS,
    DEFAULT_ROUTE_TIMEOUT,
    DEFAULT_TIMEOUT,
    RNG_SEED,
    SCALE,
    TODAY,
    estimated_cost,
    load_prices,
)
from .fake_llm import MODES, ScriptedLLM
from .metering import MeteredClient
from .questions import CATEGORIES, Item, build_questions, composition, question_digest
from .score import View, score, summarize
from .systems import (
    ablate,
    baseline_detail,
    baseline_evidence,
    make_engine,
    model_errors_of,
    routed_ok,
    run_twotrack,
    tool_hit,
    twotrack_detail,
    twotrack_evidence,
    twotrack_views,
)
from .world import build_world, data_digest

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPO_ROOT = Path(__file__).resolve().parents[1]
KEY_ENV = "TWOTRACK_LLM_API_KEY"
MODEL_ENV = "TWOTRACK_LLM_MODEL"
BASE_URL_ENV = "TWOTRACK_LLM_BASE_URL"
OFFLINE_MODEL = "offline"
SYSTEMS = ("A", "B", "C")
REFUSED = 2  # exit code when a real run is requested without --confirm
DEGRADED = 3  # exit code when a model call failed: the run finished but is not a clean result


@dataclass
class RunConfig:
    systems: tuple[str, ...] = SYSTEMS
    provider: str = "mock"
    model: str = OFFLINE_MODEL
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)  # never serialized
    limit: int | None = None
    ids: tuple[str, ...] = ()
    category: str | None = None
    lang: str | None = None
    repeats: int = 1
    max_turns: int = DEFAULT_MAX_TURNS
    timeout: float = DEFAULT_TIMEOUT  # every call but twotrack's routing opinion
    route_timeout: float = DEFAULT_ROUTE_TIMEOUT  # twotrack's routing opinion
    pin_temperature: float | None = 0.0  # None: each system's own
    omit_temperature: bool = False  # no temperature in any request (wins over the pin)
    route_json_mode: bool = True
    allow_open_task: bool = False
    fake_rate: float = 0.25
    fake_mode: str = "append"
    fake_seed: int = 7
    mock_hallucinated_number: float | None = None
    prices: dict[str, dict[str, float | None]] = field(default_factory=dict)
    # tests inject offline clients for the openai_compat path: system name -> client
    client_factory: Callable[[str], Any] | None = field(default=None, repr=False)


# ---------------------------------------------------------------- helpers


def endpoint_host(base_url: str | None, provider: str) -> str:
    if provider == "mock":
        return "offline"
    if not base_url:
        return "(sdk default)"
    return urlsplit(base_url).hostname or "(unparsed)"


def git_info() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True,
                                 text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit or "unknown", "dirty": bool(status) if status is not None else None}


def select_items(items: list[Item], config: RunConfig) -> list[Item]:
    chosen = [i for i in items
              if (not config.ids or i.id in config.ids)
              and (config.category is None or i.category == config.category)
              and (config.lang is None or i.lang == config.lang)]
    return chosen[:config.limit] if config.limit else chosen


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def baseline_temperature(config: RunConfig) -> float | None:
    """What the baseline sends: a number, or None for no temperature (the endpoint's default)."""
    return None if config.omit_temperature else config.pin_temperature


def twotrack_temperature(config: RunConfig) -> float | str | None:
    """twotrack's provider setting: one number for every call, None for each call's own,
    ``"omit"`` for none at all (``OpenAICompatProvider(temperature=...)``)."""
    return OMIT_TEMPERATURE if config.omit_temperature else config.pin_temperature


def temperature_label(config: RunConfig) -> float | str:
    if config.omit_temperature:
        return "omitted"
    return config.pin_temperature if config.pin_temperature is not None else "provider defaults"


def failure_kinds(failures: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failed calls grouped by system, call, error class and HTTP status, most frequent first."""
    counts = Counter((f["system"], f["call"], f["error"], f.get("http_status")) for f in failures)
    ordered = sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1], kv[0][2],
                                                     str(kv[0][3])))
    return [{"system": system, "call": call, "error": error, "http_status": status, "count": n}
            for (system, call, error, status), n in ordered]


def _tokens(calls: list[dict[str, Any]], shared_with: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "prompt": sum(c["prompt_tokens"] or 0 for c in calls),
        "completion": sum(c["completion_tokens"] or 0 for c in calls),
        "estimated": any(c["estimated"] for c in calls)}
    if shared_with:
        out["shared_with"] = shared_with
    return out


def _record(view: str, item: Item, repeat: int, shown: View, result: Any, *,
            latency_ms: float | None, calls: list[dict[str, Any]], error: str | None,
            detail: dict[str, Any], routed: bool | None = None, hit: bool | None = None,
            shared_with: str | None = None,
            model_errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    record = {"view": view, "system": view[0], "item_id": item.id, "repeat": repeat,
              "category": item.category, "lang": item.lang, "answerable": item.answerable,
              "tags": list(item.tags), "question": item.text, "answer": shown.text,
              "score": result.model_dump(mode="json"), "latency_ms": latency_ms,
              "tokens": _tokens(calls, shared_with), "calls": calls, "error": error,
              "routed_ok": routed, "tool_hit": hit, "detail": detail}
    if model_errors:  # only when a call failed, as twotrack's trace lists them
        record["model_errors"] = model_errors
    return record


def _real_client(config: RunConfig) -> Any:
    from openai import OpenAI

    return OpenAI(base_url=config.base_url, api_key=config.api_key or "unused",
                  timeout=config.timeout, max_retries=0)


def _client(config: RunConfig, system: str) -> Any:
    if config.client_factory is not None:
        return config.client_factory(system)
    if config.provider == "mock":
        return ScriptedLLM(hallucination_rate=config.fake_rate, mode=config.fake_mode,
                           seed=config.fake_seed)
    return _real_client(config)


# ---------------------------------------------------------------- systems


def run_baseline_system(config: RunConfig, items: list[Item], repeat: int,
                        records: list[dict[str, Any]],
                        failures: list[dict[str, Any]]) -> dict[str, Any]:
    """A and C over one repeat. Each failed model call goes into ``failures`` once, though
    both views' records carry it (C reuses A's calls)."""
    world = build_world()
    before = data_digest(world)
    raw = _client(config, "A")
    metered = MeteredClient(raw, pin_temperature=None)  # the baseline sends its own temperature
    try:
        for item in items:
            if hasattr(raw, "bind"):
                raw.bind(item)  # the scripted fake is told which item it is playing
            run = run_baseline(metered, config.model, item.text, world,
                               max_turns=config.max_turns, timeout=config.timeout,
                               temperature=baseline_temperature(config))
            calls = [c.as_dict() for c in metered.take()]
            failures.extend({"system": "A", **f} for f in run.failures)
            evidence = baseline_evidence(run)
            hit = tool_hit(item, run)
            if "A" in config.systems:
                shown = View(text=run.answer)
                records.append(_record("A", item, repeat, shown, score(item, shown, evidence),
                                       latency_ms=run.latency_ms, calls=calls, error=run.error,
                                       detail=baseline_detail(run), hit=hit,
                                       model_errors=run.failures))
            if "C" in config.systems:
                ablation = ablate(run)
                shown = View(text=ablation.answer)
                records.append(_record(
                    "C", item, repeat, shown, score(item, shown, evidence),
                    latency_ms=run.latency_ms, calls=calls, error=run.error,
                    detail={"stripped": ablation.stripped,
                            "allowed_numbers": ablation.allowed_count},
                    hit=hit, shared_with="A" if "A" in config.systems else None,
                    model_errors=run.failures))
        after = data_digest(world)
    finally:
        world.close()
    return {"before": before, "after": after}


def run_twotrack_system(config: RunConfig, items: list[Item], repeat: int,
                        records: list[dict[str, Any]],
                        failures: list[dict[str, Any]]) -> dict[str, Any]:
    """B over one repeat, scored twice (B_seen, B_text); each failed model call goes into
    ``failures`` once."""
    world = build_world()
    before = data_digest(world)
    metered = None
    if config.provider == "mock":  # the product's own zero-key provider, never a fake client
        provider: Any = MockProvider(hallucinated_number=config.mock_hallucinated_number)
    else:
        # temperature and timeouts through the provider's own settings, as a deployment sets them
        metered = MeteredClient(_client(config, "B"), pin_temperature=None)
        provider = OpenAICompatProvider(model=config.model, client=metered,
                                        timeout=config.timeout,
                                        route_timeout=config.route_timeout,
                                        temperature=twotrack_temperature(config),
                                        json_mode=config.route_json_mode)
    engine = make_engine(world, provider, allow_open_task=config.allow_open_task)
    try:
        for item in items:
            run = run_twotrack(engine, world, item.text)
            calls = [c.as_dict() for c in metered.take()] if metered else []
            errors = model_errors_of(run.response)
            failures.extend({"system": "B", **e} for e in errors)
            evidence = twotrack_evidence(run.response)
            views = twotrack_views(run.response)
            detail = {**twotrack_detail(run.response),
                      "routed_strict": routed_ok(item, run.response, strict=True)}
            routed = routed_ok(item, run.response)
            for name in ("B_seen", "B_text"):
                records.append(_record(name, item, repeat, views[name],
                                       score(item, views[name], evidence),
                                       latency_ms=run.latency_ms, calls=calls, error=run.error,
                                       detail=detail, routed=routed, model_errors=errors))
        after = data_digest(world)
    finally:
        world.close()
    return {"before": before, "after": after}


def run_benchmark(config: RunConfig) -> dict[str, Any]:
    started = _now()
    world = build_world()
    try:
        all_items = build_questions(world)
    finally:
        world.close()
    items = select_items(all_items, config)
    if not items:
        raise ValueError("no question matches the filters")
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []  # every failed model call once: system, call, error
    digests: dict[str, list[dict[str, Any]]] = {}
    for repeat in range(config.repeats):
        if {"A", "C"} & set(config.systems):
            digests.setdefault("baseline", []).append(
                run_baseline_system(config, items, repeat, records, failures))
        if "B" in config.systems:
            digests.setdefault("twotrack", []).append(
                run_twotrack_system(config, items, repeat, records, failures))
    summary = summarize(records)
    for view, block in summary.items():
        block["cost_usd"] = estimated_cost(config.prices, config.model, block["prompt_tokens"],
                                           block["completion_tokens"])
        block["cost_shared_with_A"] = view == "C" and "A" in config.systems
    unchanged = all(d["before"] == d["after"] for runs in digests.values() for d in runs)
    meta = {
        "harness_version": HARNESS_VERSION,
        "started_at": started,
        "finished_at": _now(),
        "provider": config.provider,
        "model": config.model,
        "endpoint_host": endpoint_host(config.base_url, config.provider),
        "systems": sorted(config.systems),
        "repeats": config.repeats,
        # A model call that failed (an HTTP error, a timeout, an unparseable reply) degrades the
        # run: the answers that needed it fell back. Each call counts once: C reuses A's calls
        # and B_text B_seen's.
        "degraded": bool(failures),
        "failed_calls": len(failures),
        "failed_call_kinds": failure_kinds(failures),
        "data": {"rng_seed": RNG_SEED, "scale": SCALE, "today": TODAY.isoformat(),
                 "digests": digests, "unchanged": unchanged},
        "question_set": {"digest": question_digest(all_items), "total": len(all_items),
                         "run": len(items), "composition": composition(items),
                         "filters": {"limit": config.limit, "ids": list(config.ids),
                                     "category": config.category, "lang": config.lang}},
        "settings": {"max_turns": config.max_turns,
                     # every call but twotrack's routing opinion: the baseline's turns and
                     # twotrack's narration (and chat and agent steps)
                     "timeout_s": config.timeout,
                     "narration_timeout_s": config.timeout,
                     "route_timeout_s": config.route_timeout,
                     "temperature": temperature_label(config),
                     "omit_temperature": config.omit_temperature,
                     "route_json_mode": config.route_json_mode,
                     "allow_open_task": config.allow_open_task,
                     "baseline_tool_output": "full JSON result including every row",
                     "principal_roles": ["operator"]},
        "fake_llm": ({"hallucination_rate": config.fake_rate, "mode": config.fake_mode,
                      "seed": config.fake_seed,
                      "mock_hallucinated_number": config.mock_hallucinated_number}
                     if config.provider == "mock" else None),
        "git": git_info(),
        "python": sys.version.split()[0],
    }
    return {"meta": meta, "summary": summary, "records": records}


# ---------------------------------------------------------------- output


def secrets_in_env() -> list[str]:
    value = (os.environ.get(KEY_ENV) or "").strip()
    return [value] if len(value) >= 4 else []


def dump_results(results: dict[str, Any], secrets: Sequence[str] = ()) -> str:
    """JSON text of a result, with any secret value scrubbed (defense in depth: no field is
    ever meant to hold one)."""
    text = json.dumps(results, ensure_ascii=False, indent=1, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def default_out(config: RunConfig) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model = re.sub(r"[^A-Za-z0-9._-]+", "_", config.model)[:60]
    return RESULTS_DIR / f"{stamp}-{config.provider}-{model}.json"


def write_results(results: dict[str, Any], path: Path, secrets: Sequence[str] = ()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_results(results, secrets) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- CLI


def _seconds(raw: str) -> float:
    """A timeout flag, held to the rule the server holds the timeout variables to."""
    if not raw.strip():
        raise argparse.ArgumentTypeError("expected a number of seconds")
    try:
        return _number({"the value": raw}, "the value", 0.0, minimum=MIN_TIMEOUT_SECONDS)
    except SettingsError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _timeouts(args: argparse.Namespace) -> tuple[float, float]:
    """(per call, routing): the flags, else the product's variables, else the defaults. A
    malformed variable stops the run as it stops the server (``SettingsError``)."""
    env = os.environ
    timeout = args.timeout if args.timeout is not None else _number(
        env, LLM_TIMEOUT_ENV, DEFAULT_TIMEOUT, minimum=MIN_TIMEOUT_SECONDS)
    route_timeout = args.route_timeout if args.route_timeout is not None else _number(
        env, LLM_ROUTE_TIMEOUT_ENV, DEFAULT_ROUTE_TIMEOUT, minimum=MIN_TIMEOUT_SECONDS)
    return timeout, route_timeout


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m bench.run",
                                     description="Run the wrong-number benchmark.")
    parser.add_argument("--system", choices=["A", "B", "C", "all"], default="all")
    parser.add_argument("--provider", choices=["mock", "openai_compat"], default="mock")
    parser.add_argument("--model", default=None,
                        help=f"model id (default ${MODEL_ENV}; 'offline' for mock)")
    parser.add_argument("--base-url", default=None, help=f"endpoint (default ${BASE_URL_ENV})")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", default="", help="comma-separated item ids")
    parser.add_argument("--category", choices=CATEGORIES, default=None)
    parser.add_argument("--lang", choices=["en", "zh"], default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--timeout", type=_seconds, default=None,
                        help="seconds per model call: the baseline's turns and twotrack's "
                             f"narration (default ${LLM_TIMEOUT_ENV}, else {DEFAULT_TIMEOUT:g})")
    parser.add_argument("--route-timeout", type=_seconds, default=None,
                        help="seconds for twotrack's routing opinion (default "
                             f"${LLM_ROUTE_TIMEOUT_ENV}, else {DEFAULT_ROUTE_TIMEOUT:g})")
    temperature = parser.add_mutually_exclusive_group()
    temperature.add_argument("--no-temperature-pin", action="store_true",
                             help="keep each system's own temperature instead of 0")
    temperature.add_argument("--omit-temperature", action="store_true",
                             help="send no temperature at all, for models that reject a "
                                  "non-default temperature")
    parser.add_argument("--no-route-json-mode", action="store_true",
                        help="for endpoints that reject response_format=json_object")
    parser.add_argument("--open-task", action="store_true",
                        help="let twotrack fall back to its agent loop (off in the product)")
    parser.add_argument("--fake-hallucination-rate", type=float, default=0.25)
    parser.add_argument("--fake-mode", choices=MODES, default="append")
    parser.add_argument("--fake-seed", type=int, default=7)
    parser.add_argument("--prices", default=None, help="JSON price table overriding config")
    parser.add_argument("--out", default=None, help="result file (default bench/results/...)")
    parser.add_argument("--confirm", action="store_true",
                        help="required for --provider openai_compat: real calls cost money")
    parser.add_argument("--quiet", action="store_true", help="do not print the report")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> RunConfig:
    systems = SYSTEMS if args.system == "all" else (args.system,)
    real = args.provider == "openai_compat"
    model = args.model or (os.environ.get(MODEL_ENV, "").strip() if real else OFFLINE_MODEL)
    if real and not model:
        raise SystemExit(f"--provider openai_compat needs --model or ${MODEL_ENV}")
    try:
        timeout, route_timeout = _timeouts(args)
    except SettingsError as exc:
        raise SystemExit(str(exc)) from None
    return RunConfig(
        systems=systems, provider=args.provider, model=model,
        base_url=(args.base_url or os.environ.get(BASE_URL_ENV, "").strip() or None)
        if real else None,
        api_key=(os.environ.get(KEY_ENV, "").strip() or None) if real else None,
        limit=args.limit, ids=tuple(i for i in args.ids.split(",") if i),
        category=args.category, lang=args.lang, repeats=max(1, args.repeats),
        max_turns=args.max_turns, timeout=timeout, route_timeout=route_timeout,
        pin_temperature=None if args.no_temperature_pin or args.omit_temperature else 0.0,
        omit_temperature=args.omit_temperature,
        route_json_mode=not args.no_route_json_mode, allow_open_task=args.open_task,
        fake_rate=args.fake_hallucination_rate, fake_mode=args.fake_mode,
        fake_seed=args.fake_seed, prices=load_prices(args.prices))


def main(argv: Sequence[str] | None = None, *,
         client_factory: Callable[[str], Any] | None = None) -> int:
    """``client_factory`` is the offline seam ``RunConfig`` has (system name -> client)."""
    args = parse_args(argv)
    config = config_from_args(args)
    if client_factory is not None:
        config = dataclasses.replace(config, client_factory=client_factory)
    if config.provider == "openai_compat" and not args.confirm:
        from .estimate import plan_text

        print(plan_text(config))
        print("\nNot started: real model calls cost money. Re-run with --confirm once the "
              "budget is approved.")
        return REFUSED
    results = run_benchmark(config)
    path = Path(args.out) if args.out else default_out(config)
    write_results(results, path, secrets_in_env())
    from .report import degraded_warning, render

    if not args.quiet:
        print(render(results))
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path.name
    print(f"\nresults written to {shown}")
    if results["meta"]["degraded"]:
        # last, and on stderr: still seen when the report is piped away or --quiet is on
        print(degraded_warning(results["meta"]), file=sys.stderr)
        return DEGRADED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
