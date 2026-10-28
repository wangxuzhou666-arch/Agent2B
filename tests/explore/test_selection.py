"""Choosing the explorer: its settings, the worker's model, and the start-up checks (no Node).

``TWOTRACK_EXPLORER`` picks the runtime (``builtin`` by default) and ``TWOTRACK_EXPLORE_*`` its
budgets; a malformed value stops start-up naming the variable. Selecting ``pi`` fails closed:
no worker, packages other than the pins, or a Node older than 22.19 stop the server before it
opens anything (``ExplorerUnavailable``), and nothing falls back to the built-in loop.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.explore.world import BUILTIN_MARKER, PI_MARKER, PINS
from tests.fakes import REGISTRY, make_ctx
from twotrack.core import load_registry
from twotrack.core.agent_loop import AGENT_SYSTEM
from twotrack.core.explore import pi
from twotrack.core.explore.explorer import ExploreBudget
from twotrack.core.explore.pi import (
    EXPLORER_SYSTEM,
    KEY_ENV,
    ExplorerUnavailable,
    PiModel,
    locate_harness,
    pi_model_from_env,
    tool_specs,
    worker_env,
)
from twotrack.core.model_schema import params_schema
from twotrack.providers import MockProvider
from twotrack.runtime import build_runtime
from twotrack.settings import Settings, SettingsError

# ---------------------------------------------------------------- settings


def test_the_built_in_loop_is_the_default_with_the_documented_budgets():
    s = Settings.from_env({})
    assert (s.explorer, s.explore_max_turns, s.explore_max_calls, s.explore_timeout_seconds,
            s.explore_tool_timeout_seconds, s.pi_harness_dir) == ("builtin", 4, 6, 120.0, 30.0,
                                                                  None)


def test_a_worker_start_up_has_its_own_limit():
    assert Settings.from_env({}).explore_startup_timeout_seconds == 20.0
    assert Settings.from_env({"TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS": " 45 "}) \
        .explore_startup_timeout_seconds == 45.0
    for bad, problem in (("0", "at least 0.1"), ("soon", "must be a number")):
        with pytest.raises(SettingsError, match=f"TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS "
                                                f".*{problem}"):
            Settings.from_env({"TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS": bad})


def test_the_explorer_settings_come_from_the_environment():
    s = Settings.from_env({
        "TWOTRACK_EXPLORER": " Pi ", "TWOTRACK_EXPLORE_MAX_TURNS": "2",
        "TWOTRACK_EXPLORE_MAX_CALLS": "9", "TWOTRACK_EXPLORE_TIMEOUT_SECONDS": "1.5",
        "TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS": "0.25", "TWOTRACK_PI_HARNESS_DIR": " /opt/pi "})
    assert (s.explorer, s.explore_max_turns, s.explore_max_calls, s.explore_timeout_seconds,
            s.explore_tool_timeout_seconds, s.pi_harness_dir) == ("pi", 2, 9, 1.5, 0.25,
                                                                  Path("/opt/pi"))
    blank = Settings.from_env({"TWOTRACK_EXPLORER": "", "TWOTRACK_PI_HARNESS_DIR": "  "})
    assert (blank.explorer, blank.pi_harness_dir) == ("builtin", None)


@pytest.mark.parametrize(("name", "value", "problem"), [
    ("TWOTRACK_EXPLORER", "node", "must be one of builtin/pi"),
    ("TWOTRACK_EXPLORE_MAX_TURNS", "0", "at least 1"),
    ("TWOTRACK_EXPLORE_MAX_TURNS", "2.5", "whole number"),
    ("TWOTRACK_EXPLORE_MAX_CALLS", "-1", "at least 1"),
    ("TWOTRACK_EXPLORE_TIMEOUT_SECONDS", "0.5", "at least 1"),
    ("TWOTRACK_EXPLORE_TIMEOUT_SECONDS", "inf", "finite"),
    ("TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS", "nan", "finite"),
    ("TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS", "0.01", "at least 0.1"),
    ("TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS", "soon", "must be a number"),
])
def test_a_malformed_explorer_setting_stops_start_up_naming_the_variable(name, value, problem):
    with pytest.raises(SettingsError, match=problem) as raised:
        Settings.from_env({name: value})
    assert name in str(raised.value)


def test_the_budgets_reach_the_engine(tmp_path):
    settings = replace(Settings(data_dir=tmp_path / "data"), explore_max_turns=2,
                       explore_max_calls=3, explore_timeout_seconds=5.0,
                       explore_tool_timeout_seconds=0.5, explore_startup_timeout_seconds=7.0)
    runtime = build_runtime(settings, registry=load_registry(extra_paths=[]),
                            provider=MockProvider())
    try:
        assert runtime.engine.settings.explore_budget == ExploreBudget(
            max_turns=2, max_tool_calls=3, timeout_seconds=5.0, tool_timeout_seconds=0.5,
            startup_timeout_seconds=7.0)
        assert runtime.engine.explorer is None  # the built-in loop
    finally:
        runtime.close()


def test_the_two_system_prompts_tell_the_runtimes_apart():
    # the tests' proof that nothing fell back: Pi's prompt carries one marker, the loop's the other
    assert PI_MARKER in EXPLORER_SYSTEM and PI_MARKER not in AGENT_SYSTEM
    assert BUILTIN_MARKER in AGENT_SYSTEM and BUILTIN_MARKER not in EXPLORER_SYSTEM


# ---------------------------------------------------------------- the worker's model


REAL = {"TWOTRACK_PROVIDER": "openai_compat", "TWOTRACK_LLM_MODEL": "qwen2.5",
        "TWOTRACK_LLM_BASE_URL": "http://127.0.0.1:11434/v1"}
SECRET = "k-not-a-real-key-0123456789"


def test_with_no_real_model_configured_the_worker_uses_the_zero_key_endpoint():
    assert pi_model_from_env({}) is None
    assert pi_model_from_env({**REAL, "TWOTRACK_PROVIDER": "mock"}) is None


def test_a_real_model_comes_from_the_provider_settings():
    model = pi_model_from_env({**REAL, "TWOTRACK_PROVIDER": " OpenAI_Compat ",
                               "TWOTRACK_LLM_MODEL": " qwen2.5 ", KEY_ENV: f" {SECRET} "})
    assert model == PiModel(model="qwen2.5", base_url=REAL["TWOTRACK_LLM_BASE_URL"],
                            api_key=SECRET)
    assert SECRET not in repr(model)
    assert pi_model_from_env({**REAL, KEY_ENV: "  "}).api_key is None  # a keyless endpoint


@pytest.mark.parametrize("missing", ["TWOTRACK_LLM_MODEL", "TWOTRACK_LLM_BASE_URL"])
def test_a_real_model_without_its_endpoint_stops_start_up(missing):
    with pytest.raises(ExplorerUnavailable, match="needs TWOTRACK_LLM_MODEL and") as raised:
        pi_model_from_env({**REAL, missing: " ", KEY_ENV: SECRET})
    assert SECRET not in str(raised.value)


def test_the_worker_environment_has_the_key_variable_only_with_a_key():
    assert worker_env("/home", None) == {"HOME": "/home", "LANG": "C.UTF-8"}
    assert worker_env("/home", SECRET) == {"HOME": "/home", "LANG": "C.UTF-8", KEY_ENV: SECRET}


def test_the_worker_is_offered_the_candidates_sorted_with_their_schemas():
    candidates = REGISTRY.candidates(make_ctx("operator"))
    specs = tool_specs(candidates)
    assert [s.name for s in specs] == sorted(candidates.names())
    stock = next(s for s in specs if s.name == "stock_level")
    assert stock.description == REGISTRY.target("stock_level").description
    assert stock.parameters == params_schema(REGISTRY.target("stock_level"))


# ---------------------------------------------------------------- the install, fail closed


def install(root: Path, versions: dict[str, str] = PINS, *, pins: dict | None = PINS,
            worker: bool = True) -> Path:
    """A harness directory: worker.mjs, package.json pinning ``pins`` and node_modules holding
    ``versions``."""
    root.mkdir(parents=True, exist_ok=True)
    if worker:
        (root / "worker.mjs").write_text("// stand-in\n", encoding="utf-8")
    package = {} if pins is None else {"overrides": pins}
    (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    for name, version in versions.items():
        found = root / "node_modules" / name
        found.mkdir(parents=True)
        (found / "package.json").write_text(json.dumps({"name": name, "version": version}),
                                            encoding="utf-8")
    return root


def fake_node(tmp_path: Path, says: str) -> str:
    """An executable that answers ``--version`` with ``says``."""
    node = tmp_path / "bin" / f"node-{len(list(tmp_path.glob('bin/*')))}"
    node.parent.mkdir(exist_ok=True)
    node.write_text(f"#!/bin/sh\necho '{says}'\n", encoding="utf-8")
    node.chmod(0o755)
    return str(node)


def test_a_pinned_install_and_node_22_19_pass(tmp_path):
    root = install(tmp_path / "pi")
    harness = locate_harness(root, node=fake_node(tmp_path, "v22.19.0"))
    assert (harness.root, harness.node_version, dict(harness.pins)) == (root.resolve(),
                                                                        "22.19.0", PINS)
    assert harness.argv()[1:3] == ["--permission", f"--allow-fs-read={root.resolve()}"]


@pytest.mark.parametrize("says", ["v22.18.0", "v20.11.1", "v18.20.4"])
def test_an_older_node_is_refused(tmp_path, says):
    with pytest.raises(ExplorerUnavailable, match=f"22.19 or newer, found {says[1:]}"):
        locate_harness(install(tmp_path / "pi"), node=fake_node(tmp_path, says))


@pytest.mark.parametrize("says", ["vnext", ""])
def test_a_node_that_does_not_say_its_version_is_refused(tmp_path, says):
    with pytest.raises(ExplorerUnavailable, match="could not run"):
        locate_harness(install(tmp_path / "pi"), node=fake_node(tmp_path, says))


def test_a_node_that_is_not_there_is_refused(tmp_path, monkeypatch):
    root = install(tmp_path / "pi")
    with pytest.raises(ExplorerUnavailable, match="could not run"):
        locate_harness(root, node=str(tmp_path / "no-such-node"))
    monkeypatch.setattr(pi.shutil, "which", lambda name: None)
    with pytest.raises(ExplorerUnavailable, match="on PATH"):
        locate_harness(root)


def test_drifted_versions_are_refused(tmp_path):
    root = install(tmp_path / "pi", {**PINS, "@earendil-works/pi-ai": "1.3.0"})
    with pytest.raises(ExplorerUnavailable,
                       match=r"pi-ai 1\.2\.0 is not installed .* \(found 1\.3\.0\)"):
        locate_harness(root, node=fake_node(tmp_path, "v24.0.0"))


def test_an_install_that_was_never_run_is_refused(tmp_path):
    with pytest.raises(ExplorerUnavailable, match=r"found nothing\); run `npm ci"):
        locate_harness(install(tmp_path / "pi", {}), node=fake_node(tmp_path, "v24.0.0"))


@pytest.mark.parametrize(("layout", "problem"), [
    ({"worker": False}, "there is no Pi worker"),
    ({"pins": None}, "pins no versions"),
])
def test_a_harness_without_its_worker_or_pins_is_refused(tmp_path, layout, problem):
    with pytest.raises(ExplorerUnavailable, match=problem):
        locate_harness(install(tmp_path / "pi", **layout), node=fake_node(tmp_path, "v24.0.0"))


def test_an_empty_directory_is_no_harness(tmp_path):
    with pytest.raises(ExplorerUnavailable, match="there is no Pi worker"):
        locate_harness(tmp_path)


def test_selecting_pi_without_a_worker_stops_start_up_before_anything_opens(tmp_path):
    assert issubclass(ExplorerUnavailable, SettingsError)  # reported like any bad setting
    settings = Settings.from_env({"TWOTRACK_EXPLORER": "pi",
                                  "TWOTRACK_PI_HARNESS_DIR": str(tmp_path / "empty"),
                                  "TWOTRACK_DATA_DIR": str(tmp_path / "data")})
    with pytest.raises(ExplorerUnavailable, match="there is no Pi worker"):
        build_runtime(settings, provider=MockProvider())
    assert not (tmp_path / "data").exists()
