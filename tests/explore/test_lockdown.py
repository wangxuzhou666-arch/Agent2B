"""The Pi worker's lockdown, observed: what a process started the way the host starts the worker
can and cannot do.

``test_selection`` and ``test_fake_worker`` pin the argument list and the environment the host
builds; neither shows that Node honours them. Here Node is started with those same flags
(``PiHarness.argv`` with the worker script left off) in that same environment (``worker_env``),
through the host's own ``launch``, and each probe tries one thing the worker must not be able
to do. The permission model has to refuse it. A probe is passed with ``-e`` because the lockdown
also stops Node from reading a script outside the harness directory.

Every test runs with a parent environment that would lift the lockdown if it leaked into the
worker: ``NODE_OPTIONS`` accepts the permission flags, so one inherited variable undoes all of it.

Skipped unless the worker is installed (see conftest); ``TWOTRACK_REQUIRE_PI=1`` fails instead.
"""

import json
import os
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from twotrack.core.explore.pi import PiHarness, launch, worker_env

pytestmark = pytest.mark.pi

PROBE_TIMEOUT_SECONDS = 30
SENTINEL = "twotrack-lockdown-sentinel"
FAKE_KEY = "not-a-real-key"
# macOS adds this variable to every process it starts, whatever environment the parent passes
OS_INJECTED_ENV = frozenset({"__CF_USER_TEXT_ENCODING"})
# What a parent could set to undo the lockdown (Node reads these flags from NODE_OPTIONS too)
LIFT_THE_LOCKDOWN = ("--allow-fs-read=* --allow-fs-write=* --allow-child-process --allow-worker "
                     "--allow-addons")

# Each probe prints one JSON line: {"ok": true, ...} when the operation went through, otherwise
# {"ok": false, "code": <the error's code>}. Arguments arrive in process.argv.slice(1).
PRELUDE = r"""
const fs = require("node:fs");
let reported = false;
const report = (result) => {
  if (reported) return;
  reported = true;
  process.stdout.write(JSON.stringify(result) + "\n");
};
const refused = (error) => ({ ok: false, code: error.code });
"""

READ = """
try { report({ ok: true, bytes: fs.readFileSync(process.argv[1]).length }); }
catch (error) { report(refused(error)); }
"""

WRITE_CWD = """
try { fs.writeFileSync("dropped.txt", "x"); report({ ok: true }); }
catch (error) { report(refused(error)); }
"""

# the temporary directory as the worker sees it: its environment names no TMPDIR
WRITE_TMP = """
const target = require("node:path").join(require("node:os").tmpdir(), process.argv[1]);
try { fs.writeFileSync(target, "x"); report({ ok: true, target }); }
catch (error) { report({ ...refused(error), target }); }
"""

SPAWN = """
try {
  const child = require("node:child_process").spawn(process.execPath, ["-e", "0"]);
  child.on("error", (error) => report(refused(error)));
  child.on("exit", () => report({ ok: true }));
} catch (error) { report(refused(error)); }
"""

SPAWN_SYNC = """
try {
  const done = require("node:child_process").spawnSync(process.execPath, ["-e", "0"]);
  report(done.error ? refused(done.error) : { ok: true });
} catch (error) { report(refused(error)); }
"""

WORKER_THREAD = """
try {
  const thread = new (require("node:worker_threads").Worker)("0", { eval: true });
  thread.on("error", (error) => report(refused(error)));
  thread.on("exit", () => report({ ok: true }));
} catch (error) { report(refused(error)); }
"""

# Node answers a native addon with its own code, not ERR_ACCESS_DENIED. Without the lockdown the
# path below fails differently (ERR_DLOPEN_FAILED: there is no such file), which is not a pass.
ADDON = """
try { process.dlopen({ exports: {} }, "/nonexistent/twotrack-probe.node"); report({ ok: true }); }
catch (error) { report(refused(error)); }
"""

ENV_NAMES = """
report({ ok: true, names: Object.keys(process.env).sort() });
"""


class Probe(NamedTuple):
    result: dict[str, Any]
    stdout: str
    stderr: str


def run_probe(harness: PiHarness, home: Path, body: str, *args: str,
              key: str | None = None) -> Probe:
    """Run ``body`` in Node as the host runs the worker: its argv minus the script, its
    environment, ``home`` as cwd. Returns the one JSON line the probe printed."""
    *lockdown, script = harness.argv()
    assert script == str(harness.worker), "the last argument of the worker's argv is its script"
    proc = launch([*lockdown, "-e", PRELUDE + body, *args], home, worker_env(str(home), key))
    try:
        stdout, stderr = proc.communicate(timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)  # launch() made the probe its own process group
        proc.communicate()
        pytest.fail(f"the probe did not finish in {PROBE_TIMEOUT_SECONDS} s")
    out, err = stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    lines = out.splitlines()
    assert len(lines) == 1, f"expected one JSON line, got {out!r}; stderr: {err[-500:]!r}"
    return Probe(json.loads(lines[0]), out, err)


def assert_refused(probe: Probe, code: str) -> None:
    assert probe.result.get("ok") is False and probe.result.get("code") == code, (
        f"the lockdown should have answered {code}, the probe saw {probe.result}; "
        f"stderr: {probe.stderr[-500:]!r}")


@pytest.fixture(autouse=True)
def hostile_parent_env(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", LIFT_THE_LOCKDOWN)
    monkeypatch.setenv("TWOTRACK_LOCKDOWN_CANARY", "canary")
    monkeypatch.setenv("OPENAI_API_KEY", "canary")


@pytest.fixture
def home(tmp_path):
    """The worker's cwd and HOME: a fresh empty directory, as every run makes one."""
    path = tmp_path / "home"
    path.mkdir()
    return path


# ---------------------------------------------------------------- what it may do


def test_the_worker_can_read_its_own_install(pi_harness, home):
    """The control: the probes below are not refused because they cannot run at all, and the
    harness directory (the worker's code and packages) stays readable."""
    probe = run_probe(pi_harness, home, READ, str(pi_harness.root / "package.json"))
    assert probe.result["ok"] is True and probe.result["bytes"] > 0, probe


# ---------------------------------------------------------------- what it may not do


def test_a_file_outside_the_harness_directory_cannot_be_read(pi_harness, home, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text(SENTINEL, encoding="utf-8")

    probe = run_probe(pi_harness, home, READ, str(outside))

    assert_refused(probe, "ERR_ACCESS_DENIED")
    assert SENTINEL not in probe.stdout + probe.stderr


def test_nothing_can_be_written_in_the_working_directory(pi_harness, home):
    probe = run_probe(pi_harness, home, WRITE_CWD)

    assert_refused(probe, "ERR_ACCESS_DENIED")
    assert list(home.iterdir()) == []


def test_nothing_can_be_written_in_the_temporary_directory(pi_harness, home):
    name = f"twotrack-lockdown-{uuid.uuid4().hex}"
    try:
        probe = run_probe(pi_harness, home, WRITE_TMP, name)

        assert_refused(probe, "ERR_ACCESS_DENIED")
        assert not Path(probe.result["target"]).exists()
    finally:
        # only a worker without the lockdown leaves something behind
        for directory in {Path("/tmp"), Path(tempfile.gettempdir())}:
            (directory / name).unlink(missing_ok=True)


@pytest.mark.parametrize("body", [SPAWN, SPAWN_SYNC], ids=["spawn", "spawn_sync"])
def test_no_child_process_can_be_started(pi_harness, home, body):
    assert_refused(run_probe(pi_harness, home, body), "ERR_ACCESS_DENIED")


def test_no_worker_thread_can_be_started(pi_harness, home):
    assert_refused(run_probe(pi_harness, home, WORKER_THREAD), "ERR_ACCESS_DENIED")


def test_no_native_addon_can_be_loaded(pi_harness, home):
    assert_refused(run_probe(pi_harness, home, ADDON), "ERR_DLOPEN_DISABLED")


# ---------------------------------------------------------------- what it is told


def test_the_worker_sees_only_the_allowlisted_environment(pi_harness, home):
    probe = run_probe(pi_harness, home, ENV_NAMES, key=FAKE_KEY)

    seen = set(probe.result["names"]) - OS_INJECTED_ENV
    allowed = set(worker_env(str(home), FAKE_KEY))
    assert seen == allowed, (f"not allowlisted: {sorted(seen - allowed)}; "
                             f"allowlisted but missing: {sorted(allowed - seen)}")
