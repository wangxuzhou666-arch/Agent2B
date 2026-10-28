"""What exploration may offer a model (``core.explore.exposure``), in both runtimes.

The explorer is given the routing candidates narrowed to ``explorable=True`` and then to the
deployment's allowlist ``TWOTRACK_EXPLORE_TOOLS`` (unset: every explorable target except an
external one). An external call is offered only when the allowlist names it, and even then each
call waits for a person's approval. An allowlist naming something that cannot be exposed stops
the server at start-up. The step that starts exploration records what was exposed.
"""

import json

import pytest

from tests.explore.boundary import (
    BOUNDARY,
    EXTERNAL_RUNS,
    ask,
    call,
    end_step,
    engine,
    scripted,
    start_step,
)
from tests.explore.world import fake_harness, fake_launcher
from tests.fakes import REGISTRY, FakePipeline, make_ctx
from twotrack.core.explore.exposure import check_allowlist, exposed
from twotrack.core.explore.pi import PiExplorer
from twotrack.providers import MockProvider
from twotrack.providers.base import AgentStep
from twotrack.runtime import build_runtime
from twotrack.settings import EXPLORE_TOOLS_ENV, Settings, SettingsError


@pytest.fixture(autouse=True)
def _no_external_runs():
    EXTERNAL_RUNS.clear()
    yield
    EXTERNAL_RUNS.clear()


def names(candidates) -> list[str]:
    return sorted(candidates.names())


# ---------------------------------------------------------------- the rule


def test_only_explorable_targets_are_exposed_and_external_ones_only_by_name():
    operator = BOUNDARY.candidates(make_ctx("operator"))
    assert names(operator) == ["ext_lookup", "read_hidden", "read_ok", "write_po"]

    assert names(exposed(operator, None)) == ["read_ok", "write_po"]
    assert names(exposed(operator, ("ext_lookup",))) == ["ext_lookup"]
    assert names(exposed(operator, ("ext_lookup", "read_ok"))) == ["ext_lookup", "read_ok"]
    # the allowlist narrows what roles and preconditions allow, never widens it
    assert names(exposed(BOUNDARY.candidates(make_ctx("viewer")), ("ext_lookup", "read_ok"))) == [
        "read_ok"]


def test_workflows_follow_the_same_rule():
    candidates = REGISTRY.candidates(make_ctx("operator"))
    assert candidates.workflows  # the fixture's workflows say explorable=False
    assert not exposed(candidates, None).workflows
    assert names(exposed(candidates, None)) == ["stock_level"]


# ---------------------------------------------------------------- the setting


def test_the_allowlist_comes_from_the_environment():
    assert Settings.from_env({}).explore_tools is None
    assert Settings.from_env({EXPLORE_TOOLS_ENV: "  "}).explore_tools is None
    assert Settings.from_env({EXPLORE_TOOLS_ENV: " read_ok, ext_lookup ,read_ok"}).explore_tools \
        == ("read_ok", "ext_lookup")
    with pytest.raises(SettingsError, match=f"{EXPLORE_TOOLS_ENV} must name at least one tool"):
        Settings.from_env({EXPLORE_TOOLS_ENV: " , "})


@pytest.mark.parametrize(("allowlist", "problem"), [
    (("read_ok", "nope"), "names nope, which no plugin provides"),
    (("read_hidden",), "names read_hidden, which its plugin declares explorable=False"),
])
def test_an_allowlist_that_cannot_be_honoured_stops_start_up_before_anything_opens(
        tmp_path, allowlist, problem):
    with pytest.raises(SettingsError, match=f"{EXPLORE_TOOLS_ENV} {problem}"):
        check_allowlist(BOUNDARY, allowlist)
    settings = Settings(data_dir=tmp_path / "data", explore_tools=allowlist)
    with pytest.raises(SettingsError, match=EXPLORE_TOOLS_ENV):
        build_runtime(settings, registry=BOUNDARY, provider=MockProvider())
    assert not (tmp_path / "data").exists()


def test_a_valid_allowlist_reaches_the_engine(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", explore_tools=("ext_lookup",))
    runtime = build_runtime(settings, registry=BOUNDARY, provider=MockProvider())
    try:
        assert runtime.engine.settings.explore_tools == ("ext_lookup",)
    finally:
        runtime.close()


# ---------------------------------------------------------------- the built-in loop


def test_the_built_in_loop_is_offered_only_the_exposed_tools_and_the_start_names_them():
    provider = scripted(AgentStep(text="Nothing to look up."))
    response = ask(engine(provider))

    assert response.decision.kind.value == "open_task"
    assert start_step(response).facts == {"loop": "start", "runtime": "builtin",
                                          "exposed": ["read_ok", "write_po"]}
    (offered,) = [p for m, p in provider.received if m == "agent_step"]
    assert '["read_ok", "write_po"]' in offered
    assert "read_hidden" not in offered and "ext_lookup" not in offered


def test_an_external_tool_the_allowlist_does_not_name_is_refused_as_unknown():
    pipeline = FakePipeline(BOUNDARY)
    provider = scripted(call("ext_lookup"), AgentStep(text="I cannot look outside."))
    response = ask(engine(provider, pipeline=pipeline))

    assert pipeline.runs == [] and EXTERNAL_RUNS == []
    # the trace never repeats a name the model was not given
    assert end_step(response).facts["calls"] == [{"tool": "invalid", "outcome": "blocked"}]
    assert response.answer == "I cannot look outside."


def test_an_allowlisted_external_tool_still_waits_for_approval_on_every_call():
    pipeline = FakePipeline(BOUNDARY)
    provider = scripted(call("ext_lookup"), AgentStep(text="never asked"))
    response = ask(engine(provider, allowlist=("ext_lookup", "read_ok"), pipeline=pipeline))

    assert start_step(response).facts["exposed"] == ["ext_lookup", "read_ok"]
    assert pipeline.runs == [("ext_lookup", {})]  # the pipeline issued a grant, nothing more
    assert response.grant is not None and response.grant.token == FakePipeline.GRANT
    assert EXTERNAL_RUNS == []
    assert end_step(response).facts["stop_reason"] == "needs_confirmation"


# ---------------------------------------------------------------- the Pi runtime


@pytest.mark.parametrize(("allowlist", "declared"), [
    (None, ["read_ok", "write_po"]),
    (("ext_lookup", "read_ok"), ["ext_lookup", "read_ok"]),
])
def test_the_pi_worker_is_declared_only_the_exposed_tools(tmp_path, allowlist, declared):
    lines: list[tuple[str, bytes]] = []
    explorer = PiExplorer(fake_harness(tmp_path), None, launcher=fake_launcher("answer"),
                          on_line=lambda side, raw: lines.append((side, raw)))
    response = ask(engine(scripted(), allowlist=allowlist, explorer=explorer))

    (start,) = [json.loads(raw) for side, raw in lines
                if side == "host" and json.loads(raw)["type"] == "start"]
    assert [t["name"] for t in start["tools"]] == declared
    assert start_step(response).facts == {"loop": "start", "runtime": "pi", "exposed": declared}
    assert response.answer == "All quiet."
