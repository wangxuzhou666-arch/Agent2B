#!/usr/bin/env python3
"""Smoke test for a running twotrack stack (`make up`), with no key and no model.

It signs in as the demo operator, asks one question that a fixed flow answers and one that no
fixed flow covers, and checks that the second ran in the real Pi worker against the mock model:
the trace of that turn says runtime "pi" and model "mock". Standard library only, any python3
from 3.9 on.

    python3 scripts/smoke.py [API_URL]    # default: $TWOTRACK_API_URL, else http://127.0.0.1:8000
    make smoke                            # finds the published port of the running stack

Exit status: 0 every check passed, 1 a check failed, 2 the API could not be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8000"
# The keyword rule routes this one to a report; no model takes part.
FIXED_FLOW_QUERY = "which SKUs are low on stock"
# No report matches it, so it goes to the exploration branch (the open_task route).
OPEN_ENDED_QUERY = "give me a quick overview of where things stand"
PI_PIN = "@earendil-works/pi-agent-core"  # the pinned package that names the Pi release


class Unreachable(Exception):
    """The API did not answer at all: it is not up, or the URL is wrong."""


class CheckFailed(Exception):
    """The stack answered, but not the way a working zero-key stack does."""


def need(ok: bool, message: str) -> None:
    if not ok:
        raise CheckFailed(message)


class Api:
    def __init__(self, base: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> Any:
        request = urllib.request.Request(self.base + path, headers={"Accept": "application/json"})
        return self._send(request)

    def post(self, path: str, body: dict[str, Any], token: str = "") -> Any:
        auth = {"Authorization": f"Bearer {token}"} if token else {}
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json", **auth})
        return self._send(request)

    def _send(self, request: urllib.request.Request) -> Any:
        label = f"{request.get_method()} {request.full_url}"
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:  # the API answered, with an error
            detail = exc.read(300).decode("utf-8", "replace").strip()
            raise CheckFailed(f"{label} answered {exc.code}: {detail}") from None
        except TimeoutError:
            raise CheckFailed(f"{label}: no answer within {self.timeout:g} s") from None
        except OSError as exc:  # refused, reset, no route: urllib wraps them in URLError
            raise Unreachable(f"{label}: {exc}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise CheckFailed(f"{label} answered something that is not JSON") from None

    def wait_until_up(self, seconds: float) -> None:
        """`make up` waits for the health check itself, but a stack just started by hand may
        still be coming up."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.get("/api/health")
                return
            except (Unreachable, CheckFailed) as exc:
                if time.monotonic() >= deadline:
                    raise Unreachable(f"no healthy answer within {seconds:g} s ({exc})") from None
                time.sleep(1)


def explorer_steps(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """The trace steps an explorer wrote: its `execute` steps carry a `runtime` fact."""
    return [s for s in answer["trace"] if s["step"] == "execute" and "runtime" in s["facts"]]


def last_audit(answer: dict[str, Any]) -> str:
    ids = answer["audit_ids"]
    return f"audit #{ids[-1]}" if ids else "no audit record"


class Smoke:
    """The checks, in order. Each returns the detail to print, or raises `CheckFailed`."""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.token = ""
        self.open_ended_answer = ""
        self.failed: list[str] = []
        self.passed = 0

    def ask(self, query: str) -> dict[str, Any]:
        return self.api.post("/api/query?stream=false", {"query": query}, self.token)

    def health(self) -> str:
        health = self.api.get("/api/health")
        provider = health["provider"]
        need(health["demo_mode"], "demo mode is off, so there is no passwordless sign-in to use")
        need(provider["mock"],
             f"the stack is not on the mock model (provider {provider['name']!r}, model "
             f"{provider['model']!r}); this smoke test is for the zero-key stack")
        return f"version {health['version']}, demo mode, provider mock"

    def sign_in(self) -> str:
        issued = self.api.post("/api/auth/demo", {"role": "operator"})
        user = issued["user"]
        need(user["roles"] == ["operator"],
             f"the demo sign-in issued roles {user['roles']!r}, not ['operator']")
        self.token = issued["access_token"]
        return f"{user['user_id']} (operator)"

    def fixed_flow(self) -> str:
        answer = self.ask(FIXED_FLOW_QUERY)
        decision = answer["decision"] or {}  # None when nothing was decided
        need(decision.get("kind") in ("tool", "workflow"),
             f"routed to {decision.get('kind')!r}, not to a report")
        need(not answer["error"], f"the answer carries an error: {answer['error']}")
        need(bool(answer["audit_ids"]), "the answer left no audit record")
        need(not explorer_steps(answer), "a fixed flow went through the explorer")
        return (f"{decision['kind']} {decision['target']} by {decision['source']}, "
                f"{len(answer['rows'])} rows, no explorer, {last_audit(answer)}")

    def open_ended(self) -> str:
        answer = self.ask(OPEN_ENDED_QUERY)
        kind = (answer["decision"] or {}).get("kind")
        need(kind == "open_task",
             f"routed to {kind!r}, not to the exploration branch (is TWOTRACK_ALLOW_OPEN_TASK "
             "on for the api service?)")
        # the step that ends a run is the one that carries its stop reason
        ends = [s for s in explorer_steps(answer) if "stop_reason" in s["facts"]]
        need(len(ends) == 1, f"expected one end-of-run step in the trace, found {len(ends)}")
        step = ends[0]
        facts = step["facts"]
        need(facts["runtime"] == "pi",
             f"the trace says runtime {facts['runtime']!r}, not 'pi' (is TWOTRACK_EXPLORER=pi "
             "set for the api service?)")
        need(facts["model"] == "mock", f"the trace says model {facts['model']!r}, not 'mock'")
        need(step["status"] == "ok" and facts["stop_reason"] == "answered",
             f"the run ended as {step['status']!r} / {facts['stop_reason']!r} "
             f"(error {facts.get('error')!r})")
        need(not answer["model_errors"], f"model errors: {answer['model_errors']}")
        need(bool(answer["answer"]), "the answer is empty")
        self.open_ended_answer = answer["answer"]
        versions = facts["runtime_version"]
        turns = facts["turns"]
        return (f"open_task, runtime pi {versions[PI_PIN]} on node {versions['node']}, "
                f"model mock, stop {facts['stop_reason']} after {turns} "
                f"turn{'' if turns == 1 else 's'}, {last_audit(answer)}")

    def run(self, label: str, check: Callable[[], str]) -> bool:
        try:
            detail = check()
        except CheckFailed as exc:
            self.failed.append(label)
            print(f"  FAIL  {label:<11} {exc}", flush=True)
            return False
        self.passed += 1
        print(f"  ok    {label:<11} {detail}", flush=True)
        return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("api_url", nargs="?",
                        default=os.environ.get("TWOTRACK_API_URL") or DEFAULT_URL,
                        help=f"base URL of the API (default: $TWOTRACK_API_URL or {DEFAULT_URL})")
    parser.add_argument("--wait", type=float, default=60, metavar="SECONDS",
                        help="how long to wait for the API to come up (default: 60)")
    parser.add_argument("--timeout", type=float, default=60, metavar="SECONDS",
                        help="how long to wait for one answer (default: 60)")
    args = parser.parse_args(argv)
    url = urllib.parse.urlsplit(args.api_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        parser.error(f"api_url must be an http(s) URL, got {args.api_url!r}")

    api = Api(args.api_url, args.timeout)
    print(f"twotrack smoke test against {api.base}")
    smoke = Smoke(api)
    try:
        api.wait_until_up(args.wait)
        smoke.run("health", smoke.health)
        if smoke.run("sign in", smoke.sign_in):  # every later check needs the token
            smoke.run("fixed flow", smoke.fixed_flow)
            if smoke.run("open-ended", smoke.open_ended):
                print(f"          {OPEN_ENDED_QUERY!r} -> {smoke.open_ended_answer!r}")
    except Unreachable as exc:
        print(f"  cannot reach the API: {exc}\n  is the stack up? (make up)", file=sys.stderr)
        return 2
    if smoke.failed:
        print(f"smoke test FAILED: {', '.join(smoke.failed)}")
        return 1
    print(f"smoke test passed: {smoke.passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
