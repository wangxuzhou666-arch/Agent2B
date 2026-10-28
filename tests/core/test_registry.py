import os
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from twotrack.core import (
    PLUGIN_PATHS_ENV,
    EntitySpec,
    FieldSpec,
    Plugin,
    Precondition,
    Principal,
    RegistryError,
    Step,
    Tool,
    ToolContext,
    ToolOutputs,
    TraceStatus,
    Workflow,
    build_registry,
    load_registry,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "plugins"
CORE, EXTRA = FIXTURES / "core", FIXTURES / "extra"


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


NO_OUTPUT = ToolOutputs(aggregates=(), columns=())


def _noop(ctx, data):
    return data


def tool(**kw) -> Tool:
    base = dict(name="t_one", description="d", params_model=NoParams, handler=_noop,
                roles=("viewer",), effect="read", approval="none", outputs=NO_OUTPUT,
                explorable=False)
    return Tool(**{**base, **kw})


def workflow(**kw) -> Workflow:
    steps = (Step(id="a", kind="deterministic", description="d", fn=_noop),)
    base = dict(name="w_one", description="d", roles=("operator",), effect="read",
                approval="none", outputs=NO_OUTPUT, explorable=False, steps=steps)
    return Workflow(**{**base, **kw})


def entity(**kw) -> EntitySpec:
    base = dict(id="thing", label="Thing", primary_key="thing_id",
                fields=(FieldSpec(id="thing_id", label="Thing ID"),))
    return EntitySpec(**{**base, **kw})


def child(eid: str, extends: str, **kw) -> EntitySpec:
    return EntitySpec(id=eid, label=eid.title(), extends=extends, **kw)


def pre(name: str = "ready", check=lambda ctx: True) -> Precondition:
    return Precondition(name=name, description="d", check=check)


def plugin(name: str = "alpha", **kw) -> Plugin:
    return Plugin(name=name, version="0.1.0", **kw)


def ctx_for(*roles: str, **facts) -> ToolContext:
    return ToolContext(principal=Principal(user_id="u", roles=set(roles)), facts=facts)


def problems_of(*plugins: Plugin) -> list[str]:
    with pytest.raises(RegistryError) as info:
        build_registry(plugins)
    return info.value.problems


# ---------------------------------------------------------------- loading


def test_load_happy_path_from_directory():
    reg = load_registry(module_names=[], extra_paths=[CORE])
    assert [p.name for p in reg.plugins] == ["widgets"]
    assert list(reg.tools) == ["widget_count", "widget_import"]
    assert list(reg.workflows) == ["widget_restock"]
    assert list(reg.preconditions) == ["has_upload"]
    assert reg.entity("widget").label_field == "name"
    assert reg.target("widget_restock") is reg.workflow("widget_restock")
    with pytest.raises(KeyError, match="unknown tool 'nope'"):
        reg.tool("nope")


def test_registry_with_no_sources_is_empty():
    # the built-in package now ships plugins; tests/plugins covers loading it
    assert dict(load_registry(module_names=[], extra_paths=[]).tools) == {}


def test_drop_in_plugin_dir_adds_entity_without_core_change(monkeypatch):
    monkeypatch.setenv(PLUGIN_PATHS_ENV, str(CORE))
    assert "gizmo" not in load_registry().entities
    monkeypatch.setenv(PLUGIN_PATHS_ENV, f"{CORE}{os.pathsep}{EXTRA}")
    gizmo = load_registry().entity("gizmo")
    assert gizmo.lineage == ("widget", "gizmo")
    assert [f.id for f in gizmo.fields] == ["widget_id", "name", "qty", "cost"]
    assert gizmo.match_header("Volts").id == "voltage"


@pytest.mark.parametrize("source, expected", [
    (FIXTURES / "broken_import", "failed to import: ModuleNotFoundError"),
    (FIXTURES / "no_export", "does not export PLUGIN"),
    (FIXTURES / "wrong_type", "exports PLUGIN of type dict, expected Plugin"),
    (FIXTURES / "missing_dir", "is not a directory"),
])
def test_load_failures_are_fail_closed(source, expected):
    with pytest.raises(RegistryError) as info:
        load_registry(module_names=[], extra_paths=[source])
    assert any(expected in p for p in info.value.problems), info.value.problems


def test_unknown_module_name_is_reported():
    with pytest.raises(RegistryError, match="'twotrack_no_such_plugin' failed to import"):
        load_registry(module_names=["twotrack_no_such_plugin"], extra_paths=[])


def test_load_and_validation_problems_are_reported_together():
    with pytest.raises(RegistryError) as info:
        load_registry(module_names=[], extra_paths=[FIXTURES / "broken_import", CORE, CORE])
    text = str(info.value)
    assert "failed to import" in text and "duplicate plugin name 'widgets'" in text


# ---------------------------------------------------------------- validation rules

CYCLE = (child("a", "b"), child("b", "a"))

RULES = {
    "duplicate_plugin": ([plugin("a"), plugin("a")], "duplicate plugin name 'a'"),
    "duplicate_tool": (
        [plugin("a", tools=[tool()]), plugin("b", tools=[tool()])],
        "duplicate tool/workflow name 't_one': declared by plugin 'a' and plugin 'b'"),
    "tool_workflow_share_names": ([plugin(tools=[tool(name="x")], workflows=[workflow(name="x")])],
                                  "duplicate tool/workflow name 'x'"),
    "duplicate_workflow": (
        [plugin("a", workflows=[workflow()]), plugin("b", workflows=[workflow()])],
        "duplicate tool/workflow name 'w_one'"),
    "duplicate_entity": ([plugin("a", entities=[entity()]), plugin("b", entities=[entity()])],
                         "duplicate entity name 'thing'"),
    "duplicate_precondition": ([plugin("a", preconditions=[pre()]),
                                plugin("b", preconditions=[pre()])],
                               "duplicate precondition name 'ready'"),
    "empty_roles": ([plugin(tools=[tool(roles=())])], "tool 't_one': roles is empty"),
    "unknown_role": ([plugin(workflows=[workflow(roles=("root",))])], "unknown role(s) ['root']"),
    "unknown_effect": ([plugin(tools=[tool(effect="delete")])], "unknown effect 'delete'"),
    "tool_requires_unknown": ([plugin(tools=[tool(requires=("nope",))])],
                              "tool 't_one': requires unknown precondition 'nope'"),
    "workflow_requires_unknown": ([plugin(workflows=[workflow(requires=("nope",))])],
                                  "workflow 'w_one': requires unknown precondition 'nope'"),
    "tool_unknown_entity": ([plugin(tools=[tool(entity="ghost")])],
                            "entity 'ghost' is not declared by any plugin"),
    "extends_unknown": ([plugin(entities=[child("orphan", "ghost")])],
                        "entity 'orphan' extends unknown entity 'ghost'"),
    "extends_cycle": ([plugin(entities=CYCLE)], "entity extends cycle: a -> b -> a"),
    "duplicate_field_after_merge": (
        [plugin(entities=[entity(), child("sub", "thing",
                                          attrs=(FieldSpec(id="thing_id", label="Other"),))])],
        "entity 'sub': duplicate field id 'thing_id'"),
    "alias_collision": (
        [plugin(entities=[entity(), child("sub", "thing", attrs=(
            FieldSpec(id="size", label="Size", aliases=("Thing ID",)),))])],
        "header alias 'Thing ID' of field 'size' collides with field 'thing_id'"),
    "child_declares_core_fields": (
        [plugin(entities=[entity(),
                          child("sub", "thing", fields=(FieldSpec(id="x", label="X"),))])],
        "entity 'sub': extends 'thing' so it may only add attrs"),
    "primary_key_not_a_field": ([plugin(entities=[entity(primary_key="nope")])],
                                "primary_key 'nope' is not one of its core fields"),
    "workflow_step_kind": (
        [plugin(workflows=[workflow(steps=(
            Step(id="a", kind="deterministic", description="d", fn=_noop),
            Step(id="b", kind="llm", description="d", fn=_noop),
            Step(id="c", kind="deterministic", description="d", fn=_noop)))])],
        "llm step 'b' is a middle step"),
}


@pytest.mark.parametrize("plugins, expected", RULES.values(), ids=RULES.keys())
def test_fail_closed_rule(plugins, expected):
    problems = problems_of(*plugins)
    assert any(expected in p for p in problems), problems


def test_all_problems_are_reported_not_just_the_first():
    problems = problems_of(plugin(tools=[tool(roles=()), tool(name="t_two", requires=("nope",))]))
    assert len(problems) == 2
    assert "roles is empty" in problems[0] and "unknown precondition 'nope'" in problems[1]


# ---------------------------------------------------------------- candidate-set gate


def test_candidates_filter_by_role():
    reg = load_registry(module_names=[], extra_paths=[CORE])
    assert reg.candidates(ctx_for("viewer")).names() == ["widget_count"]
    assert reg.candidates(ctx_for("operator")).names() == ["widget_count", "widget_restock"]
    assert reg.candidates(ctx_for()).names() == []


def test_candidates_filter_by_precondition():
    reg = load_registry(module_names=[], extra_paths=[CORE])
    no_upload, with_upload = ctx_for("admin"), ctx_for("admin", upload=True)
    assert "widget_import" not in reg.candidates(no_upload)
    assert "widget_import" in reg.candidates(with_upload)
    assert reg.unmet(reg.tool("widget_import"), no_upload) == ["has_upload"]
    assert reg.unmet(reg.tool("widget_import"), with_upload) == []


def test_raising_precondition_fails_closed_and_is_traced():
    def boom(ctx):
        raise RuntimeError("store offline")

    reg = build_registry([plugin(preconditions=[pre(check=boom)],
                                 tools=[tool(requires=("ready",)), tool(name="t_two")])])
    seen = []
    ctx = ToolContext(principal=Principal(user_id="u", roles={"viewer"}), trace=seen.append)
    assert reg.candidates(ctx).names() == ["t_two"]
    assert seen[0].status is TraceStatus.ERROR and seen[0].facts["precondition"] == "ready"
