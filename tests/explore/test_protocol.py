"""The worker's line protocol: one JSON object per line, at most 1 MiB, every field checked.

``parse_worker_line`` accepts exactly the lines harness/pi/README.md lists and refuses anything
else with a ``ProtocolError`` that names the rule, never the line (it may hold model output).
``_Worker`` frames a real pipe: lines split on newlines, a line longer than the cap is refused
without reading it whole. The host's own lines never exceed the cap and never carry a key.
"""

import json
import subprocess
import sys

import pytest

from twotrack.core.explore.pi import _Gone, _Worker
from twotrack.core.explore.protocol import (
    MAX_LINE_BYTES,
    AbortLine,
    BudgetSpec,
    EventLine,
    FinalLine,
    Hello,
    ModelSpec,
    ProtocolError,
    StartLine,
    ToolCallLine,
    ToolResultLine,
    encode,
    parse_worker_line,
)


def line(**fields) -> bytes:
    return json.dumps(fields).encode()


# ---------------------------------------------------------------- worker to host


def test_every_worker_line_the_readme_lists_parses():
    hello = parse_worker_line(line(type="hello", protocol=1, node="24.13.0",
                                   versions={"@earendil-works/pi-ai": "1.2.0", "other": None}))
    assert isinstance(hello, Hello) and hello.versions["other"] is None
    call = parse_worker_line(line(type="tool_call", id="t1", name="low_stock",
                                  args={"sku_id": "SKU-1", "n": [1, 2.5, None, True]}))
    assert isinstance(call, ToolCallLine) and call.args["n"] == [1, 2.5, None, True]
    turn = parse_worker_line(line(type="event", name="assistant", turn=2, stop="toolUse",
                                  calls=["low_stock"]))
    assert isinstance(turn, EventLine) and (turn.turn, turn.calls) == (2, ["low_stock"])
    blocked = parse_worker_line(line(type="event", name="tool_blocked", id="t2", tool="x",
                                     reason="invalid_args", detail="bad", args={"a": 1}))
    assert (blocked.reason, blocked.args) == ("invalid_args", {"a": 1})
    late = parse_worker_line(line(type="event", name="tool_timeout", id="t3", tool="x"))
    assert late.id == "t3"
    final = parse_worker_line(line(type="final", text="ok", stop_reason="error", error="502",
                                   http_status=502, usage={"input": 3, "output": 4}, turns=1,
                                   tool_calls=2, forwarded=1))
    assert isinstance(final, FinalLine) and (final.http_status, final.usage.output) == (502, 4)


@pytest.mark.parametrize("raw", [
    b"not json",
    b"[1, 2]",
    b'{"type": "surprise"}',
    b'{"name": "assistant"}',  # no type
    b'{"type": "hello", "protocol": 2, "versions": {}, "node": "24"}',
    b'{"type": "tool_call", "id": "", "name": "x", "args": {}}',
    b'{"type": "tool_call", "id": "t1", "name": "x", "args": [1]}',
    b'{"type": "event", "name": "progress"}',
    b'{"type": "event", "name": "tool_blocked", "reason": "felt_like_it"}',
    b'{"type": "event", "name": "assistant", "turn": 0}',
    b'{"type": "final", "stop_reason": "finished"}',
    b'{"type": "final", "stop_reason": "stop", "http_status": 99}',
    b'{"type": "final", "stop_reason": "stop", "extra": 1}',  # unknown fields are drift
])
def test_a_line_outside_the_protocol_is_refused_without_repeating_it(raw):
    with pytest.raises(ProtocolError) as raised:
        parse_worker_line(raw)
    assert raw.decode() not in str(raised.value)


def test_a_worker_line_over_the_cap_is_refused():
    pad = MAX_LINE_BYTES - len(line(type="final", stop_reason="stop", text=""))
    at_cap = line(type="final", stop_reason="stop", text="x" * pad)
    assert len(at_cap) == MAX_LINE_BYTES
    assert parse_worker_line(at_cap).text == "x" * pad
    with pytest.raises(ProtocolError, match="size limit"):
        parse_worker_line(line(type="final", stop_reason="stop", text="x" * (pad + 1)))


# ---------------------------------------------------------------- host to worker


def start(task: str = "where do we stand") -> StartLine:
    return StartLine(task=task, system="sys", tools=[],
                     model=ModelSpec(id="mock", base_url="http://127.0.0.1:1/v1",
                                     api_key_env="TWOTRACK_LLM_API_KEY"),
                     budget=BudgetSpec(max_turns=1, max_tool_calls=1, wall_ms=1,
                                       request_timeout_ms=1, tool_timeout_ms=1))


def test_host_lines_are_one_json_object_each_with_no_empty_fields():
    data = encode(ToolResultLine(id="t1", ok=True, content="{}"))
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert json.loads(data) == {"type": "tool_result", "id": "t1", "ok": True, "content": "{}",
                                "terminate": False}
    assert json.loads(encode(AbortLine(reason="timeout"))) == {"type": "abort",
                                                              "reason": "timeout"}
    model = json.loads(encode(start()))["model"]
    assert model["api_key_env"] == "TWOTRACK_LLM_API_KEY" and "api_key" not in model
    assert b"\n" not in encode(start("two\nlines"))[:-1]  # a newline in data stays escaped


def test_a_host_line_over_the_cap_is_never_sent():
    with pytest.raises(ProtocolError, match="start line exceeds the size limit"):
        encode(start("x" * MAX_LINE_BYTES))


def test_host_lines_refuse_fields_the_worker_does_not_know():
    with pytest.raises(ValueError):
        ToolResultLine(id="t1", ok=True, api_key="k")


# ---------------------------------------------------------------- framing a real pipe


def worker_running(code: str) -> _Worker:
    """A process that runs ``code`` (``out`` is its stdout), then waits for stdin to close."""
    script = f"import json, sys\nout = sys.stdout.buffer\n{code}\nout.flush()\nsys.stdin.read()\n"
    proc = subprocess.Popen([sys.executable, "-I", "-c", script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return _Worker(proc, None)


def test_lines_are_split_on_newlines_and_a_closed_pipe_is_seen_by_every_read():
    hello = line(type="hello", protocol=1, versions={}, node="24")
    final = line(type="final", stop_reason="stop")
    rest = hello[10:] + b"\r\n" + final + b"\n"
    worker = worker_running(f"out.write({hello[:10]!r}); out.flush()\nout.write({rest!r})")
    try:
        assert isinstance(worker.read(10), Hello)
        assert isinstance(worker.read(10), FinalLine)
        worker.proc.stdin.close()
        for _ in range(2):
            with pytest.raises(_Gone):
                worker.read(10)
    finally:
        worker.stop(1)


def test_a_line_over_the_cap_is_refused_before_it_is_read_whole():
    worker = worker_running(f"out.write(b'x' * {MAX_LINE_BYTES + 10} + b'\\n')")
    try:
        with pytest.raises(ProtocolError, match="size limit"):
            worker.read(10)
    finally:
        worker.kill()
        worker.stop(1)
    assert worker.proc.returncode is not None


def test_a_line_at_the_cap_still_frames():
    pad = MAX_LINE_BYTES - len(line(type="final", stop_reason="stop", text=""))
    worker = worker_running("out.write(json.dumps({'type': 'final', 'stop_reason': 'stop', "
                            f"'text': 'y' * {pad}}}).encode() + b'\\n')")
    try:
        assert worker.read(10).text == "y" * pad
    finally:
        worker.stop(1)


def test_stderr_is_kept_only_up_to_its_cap():
    script = "import sys; sys.stderr.write('e' * 200000); sys.stderr.flush()"
    proc = subprocess.Popen([sys.executable, "-I", "-c", script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    worker = _Worker(proc, None)
    worker.stop(5)
    assert len(worker.stderr) == 64 * 1024
