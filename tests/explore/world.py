"""A server on the built-in plugins whose open tasks run in the Pi explorer, for these tests.

``build_world`` assembles it the way ``twotrack serve`` does with ``TWOTRACK_EXPLORER=pi`` and
``TWOTRACK_ALLOW_OPEN_TASK=1``: the built-in plugin registry, seed data, an operator (olga), a
viewer (vera) and an admin (ada, who reads the audit log). The model is a ``ScriptedProvider``
behind the zero-key endpoint unless a test passes a ``PiModel``. Every protocol line is kept, and
so is every worker process, so a test can show what crossed the wire and that no worker outlived
its run.

The two system prompts tell the runtimes apart: every model call Pi plans carries
``PI_MARKER``; one carrying ``BUILTIN_MARKER`` would mean the built-in loop ran instead.
"""

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tests.fakes import ScriptedProvider
from tests.server.conftest import PASSWORD, Api
from twotrack.core import load_registry
from twotrack.core.explore.pi import DEFAULT_HARNESS_DIR, PiExplorer, PiHarness, PiModel, launch
from twotrack.providers.base import AgentStep, ToolCall
from twotrack.runtime import Runtime, build_runtime
from twotrack.seed import seed_database
from twotrack.settings import Settings

QUERY = "give me a quick overview of where things stand"  # no fixed flow covers it
PI_MARKER = "You explore a business user"  # explore.pi.EXPLORER_SYSTEM
BUILTIN_MARKER = "You complete a business user"  # agent_loop.AGENT_SYSTEM
USERS = {"olga": "operator", "vera": "viewer", "ada": "admin"}
# the committed pins (package.json "overrides"): a fake worker reports them as its versions
PINS: dict[str, str] = json.loads((DEFAULT_HARNESS_DIR / "package.json").read_text(
    encoding="utf-8"))["overrides"]
FAKE_WORKER = Path(__file__).with_name("fake_worker.py")


def call(name: str, **args: Any) -> AgentStep:
    """A model turn that calls one tool."""
    return AgentStep(tool_calls=[ToolCall(name=name, arguments=args)])


def calls(*named: tuple[str, dict[str, Any]]) -> AgentStep:
    """A model turn that calls several tools at once."""
    return AgentStep(tool_calls=[ToolCall(name=n, arguments=a) for n, a in named])


def answer(text: str) -> AgentStep:
    return AgentStep(text=text)


def end_of_run(body: dict) -> dict:
    """The trace step that ends the exploration run: the one carrying its stop reason."""
    (step,) = [s for s in body["trace"]
               if s["step"] == "execute" and "stop_reason" in s["facts"]]
    return step


class Launcher:
    """``explore.pi.launch`` that keeps every process it starts; ``argv`` replaces the worker's
    command line (a fake worker)."""

    def __init__(self, argv: list[str] | None = None) -> None:
        self.argv = argv
        self.procs: list[subprocess.Popen] = []

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
        proc = launch(self.argv or argv, cwd, env)
        self.procs.append(proc)
        return proc

    def kill(self) -> None:
        """SIGKILL the latest worker's process group, the way a crash ends it."""
        os.killpg(self.procs[-1].pid, signal.SIGKILL)


def fake_launcher(mode: str, versions: dict[str, str] = PINS) -> Launcher:
    return Launcher([sys.executable, "-I", str(FAKE_WORKER), mode, json.dumps(versions)])


def fake_harness(root: Path) -> PiHarness:
    """A harness record for a fake worker: the pins, no install (no Node is needed)."""
    return PiHarness(root=root, node=sys.executable, node_version="24.0.0",
                     pins=MappingProxyType(dict(PINS)))


class PiWorld:
    def __init__(self, provider: ScriptedProvider, launcher: Launcher) -> None:
        self.provider = provider
        self.launcher = launcher
        self.lines: list[tuple[str, bytes]] = []
        self.tap: Callable[[str, bytes], None] | None = None  # sees each line as it crosses
        self.api: Api | None = None

    def script(self, *steps: AgentStep) -> None:
        """The model's replies from now on, in order (then it answers "Done.")."""
        self.provider.steps = list(steps)

    def on_line(self, direction: str, raw: bytes) -> None:
        self.lines.append((direction, raw))
        if self.tap is not None:
            self.tap(direction, raw)

    @property
    def runtime(self) -> Runtime:
        return self.api.runtime

    def ask(self, user: str, query: str = QUERY) -> dict:
        response = self.api.post("/api/query?stream=false", user, json={"query": query})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["decision"]["kind"] == "open_task", "the query no longer reaches open_task"
        return body

    def sent(self, direction: str, kind: str | None = None) -> list[dict]:
        """The protocol lines one side wrote (``host`` or ``worker``), parsed, in order."""
        parsed = [json.loads(raw) for d, raw in self.lines if d == direction]
        return [m for m in parsed if kind in (None, m["type"])]

    def model_calls(self) -> list[str]:
        return [payload for method, payload in self.provider.received if method == "agent_step"]

    def assert_planned_by_pi(self) -> None:
        """Every model call of the run came from Pi's loop; none from the built-in one."""
        payloads = self.model_calls()
        assert payloads, "the model was never asked"
        assert all(PI_MARKER in p for p in payloads)
        assert not any(BUILTIN_MARKER in p for p in payloads)

    def assert_reaped(self) -> None:
        """Every worker this world started has exited."""
        assert self.launcher.procs
        assert all(p.poll() is not None for p in self.launcher.procs)

    def purchase_orders(self) -> int:
        return self.runtime.store.count("purchase_order")

    def first_sku(self) -> str:
        return self.runtime.store.list("sku", sort="sku_id", limit=1).rows[0]["sku_id"]


def build_world(data_dir: Path, harness: PiHarness, steps: Any = (), *,
                model: PiModel | None = None, launcher: Launcher | None = None,
                provider: ScriptedProvider | None = None, **settings: Any) -> PiWorld:
    """``steps`` script the model (a list of ``AgentStep``s, or a callable of the messages and
    tool cards); ``settings`` override ``Settings`` fields (budgets)."""
    world = PiWorld(provider or ScriptedProvider(steps=steps), launcher or Launcher())
    explorer = PiExplorer(harness, model, launcher=world.launcher, on_line=world.on_line)
    config = replace(Settings(data_dir=data_dir, allow_open_task=True, explorer="pi"),
                     **settings)
    runtime = build_runtime(config, registry=load_registry(extra_paths=[]),
                            provider=world.provider, explorer=explorer)
    try:
        seed_database(runtime.store, runtime.registry)
        for name, role in USERS.items():
            runtime.users.add(name, PASSWORD, [role])
    except BaseException:
        runtime.close()
        raise
    world.api = Api(runtime)
    return world
