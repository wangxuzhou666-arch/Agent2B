"""The host side of the Pi runtime against a worker that breaks the rules (no Node needed).

``fake_worker.py`` stands in for the real worker: it says hello, takes the start line, then
misbehaves in one way per test. Whatever it does, the run ends as a reported failure (an
``execute`` ERROR step, a ``ModelCallError`` for ``agent_step``, a deterministic answer) or, for
budgets, as a stop with the reason; the worker is reaped; and the built-in loop never runs: the
request's model is not asked at all, since only the worker would ask it.
"""

import logging
import subprocess
import time

import pytest

from tests.explore.fake_worker import SLOW_START_SECONDS
from tests.explore.world import PINS, Launcher, end_of_run, fake_harness, fake_launcher
from twotrack.core.explore import pi
from twotrack.core.explore.pi import ExplorerUnavailable, PiModel, check_worker, secret_forms
from twotrack.core.i18n import say

# changes under JSON-escaping and URL-quoting, so that every form of it differs
KEY = 'sk-test "q"\\é/+!(x)~-0123456789'


@pytest.fixture
def harness(tmp_path):
    return fake_harness(tmp_path)


def assert_failed(body: dict, stop_reason: str, error: str, answer_key: str) -> None:
    step = end_of_run(body)
    assert step["status"] == "error"
    assert (step["facts"]["stop_reason"], step["facts"]["error"]) == (stop_reason, error)
    assert step["facts"]["runtime"] == "pi"
    assert body["model_errors"] == [{"call": "agent_step", "error": error, "http_status": None}]
    assert body["answer"] == say(answer_key, "en")
    assert body["approval"] is None


def test_a_worker_that_keeps_to_the_protocol_answers(make_world, harness):
    world = make_world(harness, launcher=fake_launcher("answer"))
    body = world.ask("olga")

    assert (body["answer"], body["answer_source"]) == ("All quiet.", "model")
    facts = end_of_run(body)["facts"]
    assert (facts["stop_reason"], facts["model"]) == ("answered", "mock")
    assert facts["runtime_version"] == {**PINS, "node": "24.0.0"}
    assert world.model_calls() == []  # the fake never asks; nothing else does either
    world.assert_reaped()


@pytest.mark.parametrize("mode", ["not_json", "unknown_type", "extra_field", "oversize",
                                  "second_hello", "unasked_abort"])
def test_a_line_that_breaks_the_protocol_stops_the_run(make_world, harness, mode):
    world = make_world(harness, launcher=fake_launcher(mode))
    body = world.ask("olga")

    assert_failed(body, "protocol_error", "ExplorerProtocolError", "explorer_failed")
    assert world.model_calls() == []
    world.assert_reaped()


def test_a_worker_that_exits_without_a_final_line_crashed(make_world, harness):
    world = make_world(harness, launcher=fake_launcher("exit"))
    body = world.ask("olga")

    assert_failed(body, "worker_crashed", "ExplorerCrashed", "explorer_failed")
    assert world.model_calls() == []
    world.assert_reaped()
    assert world.launcher.procs[0].returncode == 3


def test_a_worker_that_loaded_other_versions_is_not_used(make_world, harness):
    drifted = {**PINS, "@earendil-works/pi-ai": "9.9.9"}
    world = make_world(harness, launcher=fake_launcher("answer", drifted))
    body = world.ask("olga")

    assert_failed(body, "worker_unavailable", "ExplorerUnavailable", "explorer_failed")
    assert not world.sent("host", "start")  # it never got a task
    assert end_of_run(body)["facts"]["runtime_version"]["@earendil-works/pi-ai"] == "9.9.9"
    world.assert_reaped()


def test_a_worker_that_cannot_start_is_reported(make_world, harness):
    class Broken(Launcher):
        def __call__(self, argv, cwd, env) -> subprocess.Popen:
            raise FileNotFoundError("node")

    world = make_world(harness, launcher=Broken())
    body = world.ask("olga")

    assert_failed(body, "worker_unavailable", "ExplorerUnavailable", "explorer_failed")
    assert world.lines == [] and world.model_calls() == []


def test_a_silent_worker_is_stopped_at_the_wall_clock(make_world, harness):
    world = make_world(harness, launcher=fake_launcher("silent"), explore_timeout_seconds=1)
    body = world.ask("olga")

    assert_failed(body, "timeout", "ExplorerTimeout", "explorer_timeout")
    # the clock started at its hello, so it always got the task, and then was told to stop
    assert len(world.sent("host", "start")) == 1
    assert world.sent("host", "abort") == [{"type": "abort", "reason": "timeout"}]
    world.assert_reaped()


# ---------------------------------------------------------------- start-up, then the clock


def test_a_slow_start_does_not_spend_the_run_s_clock(make_world, harness, monkeypatch):
    # The worker takes longer to say hello than the whole run may last, grace included. Its
    # start-up has its own limit, and the run's clock starts at the hello, so the run is whole.
    monkeypatch.setattr(pi, "GRACE_SECONDS", 0.1)
    assert SLOW_START_SECONDS > 1 + pi.GRACE_SECONDS
    world = make_world(harness, launcher=fake_launcher("slow_hello"), explore_timeout_seconds=1)
    body = world.ask("olga")

    assert (body["answer"], body["model_errors"]) == ("Started late, answered in time.", [])
    facts = end_of_run(body)["facts"]
    assert facts["stop_reason"] == "answered"
    assert len(world.sent("host", "start")) == 1 and not world.sent("host", "abort")
    world.assert_reaped()


def test_a_worker_that_does_not_come_up_in_time_is_unavailable(make_world, harness):
    world = make_world(harness, launcher=fake_launcher("slow_hello"),
                       explore_startup_timeout_seconds=0.3, explore_timeout_seconds=60)
    began = time.monotonic()
    body = world.ask("olga")

    assert time.monotonic() - began < SLOW_START_SECONDS + 5  # nobody waited for the run's clock
    assert_failed(body, "worker_unavailable", "ExplorerUnavailable", "explorer_failed")
    assert end_of_run(body)["facts"]["detail"] == "startup_timeout"
    assert not world.sent("host", "start")  # it never got a task
    world.assert_reaped()


def test_a_worker_with_other_versions_says_so_in_the_trace(make_world, harness):
    drifted = {**PINS, "@earendil-works/pi-ai": "9.9.9"}
    world = make_world(harness, launcher=fake_launcher("answer", drifted))
    assert end_of_run(world.ask("olga"))["facts"]["detail"] == "version_drift"


def test_the_host_enforces_the_call_budget_when_the_worker_does_not(make_world, harness):
    world = make_world(harness, launcher=fake_launcher("flood"), explore_max_calls=2)
    body = world.ask("olga")

    step = end_of_run(body)
    assert (step["status"], step["facts"]["stop_reason"]) == ("blocked", "max_tool_calls")
    assert step["facts"]["calls"] == [{"tool": "low_stock", "outcome": "ok"}] * 2 + [
        {"tool": "low_stock", "outcome": "blocked"}]
    # the third call never ran: refused, traced and audited as a budget refusal, and the run
    # was told to stop
    assert [s["facts"] for s in body["trace"] if s["step"] == "gate" and
            s["status"] == "blocked"] == [{"stage": "budget", "target": "low_stock",
                                            "reason": "max_tool_calls", "blocked_by": "pi"}]
    refused = world.sent("host", "tool_result")[-1]
    assert (refused["id"], refused["code"], refused["terminate"]) == ("t3", "blocked", True)
    assert world.sent("host", "abort") == [{"type": "abort", "reason": "max_tool_calls"}]
    audit = world.api.get("/api/audit", "ada").json()["items"]
    assert [e["status"] for e in reversed(audit)] == ["ok", "ok", "blocked"]
    assert world.model_calls() == []
    world.assert_reaped()


def test_an_answer_the_worker_gave_up_on_is_marked_late(make_world, harness):
    world = make_world(harness, launcher=fake_launcher("late"))
    body = world.ask("olga")

    facts = end_of_run(body)["facts"]
    assert facts["stop_reason"] == "answered"
    assert facts["calls"] == [{"tool": "low_stock", "outcome": "ok", "late": True}]
    assert body["answer"] == "Nothing came back in time."


# ---------------------------------------------------------------- the start-up check


def test_the_start_up_check_reads_the_pinned_versions_from_the_hello(harness):
    assert check_worker(harness, launcher=fake_launcher("answer")) == PINS


@pytest.mark.parametrize(("mode", "versions", "problem"), [
    ("answer", {**PINS, "@earendil-works/pi-agent-core": "1.4.0"},
     "loaded versions other than the pins"),
    ("answer", {}, "loaded versions other than the pins"),
    ("no_hello", PINS, "did not report its versions"),
])
def test_the_start_up_check_fails_closed(harness, mode, versions, problem):
    launcher = fake_launcher(mode, versions)
    with pytest.raises(ExplorerUnavailable, match=problem):
        check_worker(harness, launcher=launcher)
    assert all(p.poll() is not None for p in launcher.procs)


def test_the_start_up_check_fails_closed_when_the_worker_cannot_start(harness):
    def broken(argv, cwd, env):
        raise PermissionError("node")

    with pytest.raises(ExplorerUnavailable, match="could not start"):
        check_worker(harness, launcher=broken)


def test_the_worker_runs_locked_down_in_an_empty_home_with_nothing_inherited(harness):
    seen = []

    def spy(argv, cwd, env):
        seen.append((argv, cwd, env))
        return fake_launcher("answer")(argv, cwd, env)

    check_worker(harness, launcher=spy)
    ((argv, cwd, env),) = seen
    assert argv == [harness.node, "--permission", f"--allow-fs-read={harness.root}",
                    str(harness.worker)]
    assert env == {"HOME": str(cwd), "LANG": "C.UTF-8"}
    assert not cwd.exists()  # the empty home goes with the worker


def test_a_key_the_worker_repeats_is_redacted_from_the_host_log_in_every_form(make_world,
                                                                            harness, caplog):
    """The host's own redaction, not the worker's: this worker writes its key, as it is,
    JSON-escaped and URL-quoted, to stderr and into its final line's error."""
    caplog.set_level(logging.WARNING, logger="twotrack.explore.pi")
    model = PiModel(model="some-model", base_url="http://127.0.0.1:9/v1", api_key=KEY)
    world = make_world(harness, launcher=fake_launcher("leaky"), model=model)
    body = world.ask("olga")

    assert end_of_run(body)["facts"]["stop_reason"] == "model_error"
    leaked = [raw.decode() for side, raw in world.lines if side == "worker"]
    assert any(KEY in text or "%22q%22" in text for text in leaked)  # it really did
    (warning,) = [r.getMessage() for r in caplog.records if "run failed" in r.getMessage()]
    assert "model error: 401 Incorrect API key provided: [redacted] | [redacted]" in warning
    assert "stderr: diagnostics: [redacted]" in warning
    assert not [form for form in secret_forms(KEY) if form in caplog.text]
    world.assert_reaped()

