"""A stand-in for harness/pi/worker.mjs that misbehaves on purpose, for the explorer tests.

    python -I fake_worker.py <mode> <versions as JSON>

It says hello with ``versions`` (the pins, unless a test wants a drift), reads the host's start
line, then does what ``mode`` says. Python only: no Node and no Pi are needed.
"""

import json
import os
import sys
import time
import urllib.parse

SLOW_START_SECONDS = 1.5  # how long "slow_hello" takes to come up


def say(message: object) -> None:
    sys.stdout.write((message if isinstance(message, str) else json.dumps(message)) + "\n")
    sys.stdout.flush()


def final(stop_reason: str, **fields: object) -> dict:
    return {"type": "final", "stop_reason": stop_reason, **fields}


def wait_for_abort() -> None:
    for raw in sys.stdin:
        if json.loads(raw).get("type") == "abort":
            return


def main(mode: str, versions: dict[str, str]) -> int:
    hello = {"type": "hello", "protocol": 1, "versions": versions, "node": "24.0.0"}
    if mode == "no_hello":
        say("starting up...")
    if mode == "slow_hello":  # a start-up under load: slow to say hello, then quick
        time.sleep(SLOW_START_SECONDS)
    say(hello)
    if not sys.stdin.readline():  # the start line; none: a version check
        return 0
    if mode == "answer":
        say(final("stop", text="All quiet.", turns=1))
    elif mode == "slow_hello":
        say(final("stop", text="Started late, answered in time.", turns=1))
    elif mode == "not_json":
        say("this is not json")
    elif mode == "unknown_type":
        say({"type": "progress", "done": 1})
    elif mode == "extra_field":
        say(final("stop", text="", note="a field the protocol does not have"))
    elif mode == "oversize":
        say(final("stop", text="x" * (1024 * 1024)))
    elif mode == "second_hello":
        say(hello)
    elif mode == "unasked_abort":
        say(final("host_abort"))
    elif mode == "exit":
        return 3
    elif mode == "leaky":  # repeats its key in every form, on stderr and in its final line
        key = os.environ.get("TWOTRACK_LLM_API_KEY", "")
        forms = " | ".join([key, json.dumps(key)[1:-1],
                            json.dumps(key, ensure_ascii=False)[1:-1],
                            urllib.parse.quote(key, safe=""),
                            urllib.parse.quote(key, safe="!'()*")])
        sys.stderr.write(f"diagnostics: {forms}\n")
        sys.stderr.flush()
        say(final("error", error=f"401 Incorrect API key provided: {forms}", turns=1))
    elif mode == "flood":  # ignores its call budget, so the host has to enforce it
        for n in range(1, 100):
            say({"type": "tool_call", "id": f"t{n}", "name": "low_stock", "args": {}})
            if json.loads(sys.stdin.readline() or "{}").get("terminate"):
                break
        wait_for_abort()
        say(final("host_abort", turns=1, tool_calls=n, forwarded=n))
    elif mode == "rogue":  # forwards a write it was never declared, twice, as a broken
        # worker could: the host has to refuse it itself
        for n in (1, 2):
            say({"type": "tool_call", "id": f"t{n}", "name": "create_po",
                 "args": {"sku_id": "SKU-0001", "qty": 5}})
            sys.stdin.readline()  # the refusal
        say(final("stop", text="Nothing was ordered.", turns=2, tool_calls=2, forwarded=2))
    elif mode == "late":  # gives up on its call before the host answers it
        say({"type": "tool_call", "id": "t1", "name": "low_stock", "args": {}})
        say({"type": "event", "name": "tool_timeout", "id": "t1", "tool": "low_stock"})
        sys.stdin.readline()  # the answer nobody waits for any more
        say(final("stop", text="Nothing came back in time.", turns=2, tool_calls=1,
                  forwarded=1))
    # "silent", and every mode above once it has spoken: wait until the host closes stdin
    sys.stdin.read()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], json.loads(sys.argv[2])))
