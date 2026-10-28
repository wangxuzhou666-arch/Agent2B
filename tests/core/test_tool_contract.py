"""The tool contract the core owns: what a plugin must declare, and what the registry refuses.

Declarations have no defaults (approval, outputs, explorable beside roles and effect), the
role/effect table is core policy, and installing a plugin never widens who may run what.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from twotrack.core import (
    ROLE_EFFECTS,
    Approval,
    Plugin,
    Principal,
    RegistryError,
    ResourceEffect,
    Role,
    Step,
    Tool,
    ToolOutputs,
    Workflow,
    build_registry,
    load_registry,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"
VIEWER = Principal(user_id="v", roles={"viewer"})
OPERATOR = Principal(user_id="o", roles={"operator"})
ADMIN = Principal(user_id="a", roles={"admin"})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = 0


def _run(ctx, params):
    return None


DECLARED = {"approval": "none", "explorable": False,
            "outputs": ToolOutputs(aggregates=("n",), columns=("a",))}


def tool(**kw) -> Tool:
    base = {"name": "t", "description": "d", "params_model": Strict, "handler": _run,
            "roles": ("viewer", "operator"), "effect": "read", **DECLARED}
    return Tool(**{**base, **kw})


def problems_of(*targets) -> str:
    tools = tuple(t for t in targets if isinstance(t, Tool))
    workflows = tuple(t for t in targets if isinstance(t, Workflow))
    with pytest.raises(RegistryError) as caught:
        build_registry([Plugin(name="p", version="1", tools=tools, workflows=workflows)])
    return "\n".join(caught.value.problems)


# ---------------------------------------------------------------- nothing is left to a default


@pytest.mark.parametrize("missing", ["roles", "effect", "approval", "outputs", "explorable"])
def test_a_tool_cannot_omit_a_declaration(missing):
    base = {"name": "t", "description": "d", "params_model": Strict, "handler": _run,
            "roles": ("viewer",), "effect": "read", **DECLARED}
    del base[missing]
    with pytest.raises(TypeError, match=missing):
        Tool(**base)


def test_a_plugin_file_without_them_stops_the_load(tmp_path):
    (tmp_path / "lazy.py").write_text(
        "from pydantic import BaseModel, ConfigDict\n"
        "from twotrack.core import Plugin, Tool\n"
        "class P(BaseModel):\n    model_config = ConfigDict(extra='forbid')\n"
        "PLUGIN = Plugin(name='lazy', version='1', tools=(Tool(name='lazy_tool', description='d',"
        " params_model=P, handler=lambda c, p: None, roles=('viewer',), effect='read'),))\n")
    with pytest.raises(RegistryError, match="approval"):
        load_registry(module_names=[], extra_paths=[tmp_path])


def test_a_complete_declaration_loads_and_is_normalised():
    registry = build_registry([Plugin(name="p", version="1", tools=(
        tool(approval="none", outputs=ToolOutputs(aggregates=["n"], columns=["a"])),))])
    declared = registry.tool("t")
    assert declared.approval is Approval.NONE and declared.explorable is False
    assert declared.outputs == ToolOutputs(aggregates=("n",), columns=("a",))


# ---------------------------------------------------------------- approval follows the effect


@pytest.mark.parametrize(("effect", "approval", "needle"), [
    ("write", "none", "a write target must declare approval='required'"),
    ("read", "required", "a read target must declare approval='none'"),
    ("read", "maybe", "unknown approval 'maybe'"),
    ("write", None, "unknown approval None"),
])
def test_the_registry_ties_approval_to_the_effect(effect, approval, needle):
    assert needle in problems_of(tool(effect=effect, approval=approval, roles=("operator",)))


@pytest.mark.parametrize("approval", ["required", "none"])
def test_an_external_target_chooses_its_approval(approval):
    registry = build_registry([Plugin(name="p", version="1", tools=(
        tool(effect="external", approval=approval, roles=("operator",)),))])
    assert registry.tool("t").approval.value == approval


def test_a_write_with_approval_loads():
    registry = build_registry([Plugin(name="p", version="1", tools=(
        tool(effect="write", approval="required", roles=("operator", "admin")),))])
    assert registry.tool("t").approval is Approval.REQUIRED


# ---------------------------------------------------------------- the role/effect table is core's


def test_the_policy_table_is_fixed_in_core():
    assert ROLE_EFFECTS[Role.VIEWER] == {ResourceEffect.READ}
    every = {ResourceEffect.READ, ResourceEffect.WRITE, ResourceEffect.EXTERNAL}
    assert ROLE_EFFECTS[Role.OPERATOR] == every and ROLE_EFFECTS[Role.ADMIN] == every
    with pytest.raises(TypeError):
        ROLE_EFFECTS[Role.VIEWER] = every  # a plugin cannot edit it


@pytest.mark.parametrize(("effect", "approval"), [("write", "required"), ("external", "none")])
def test_a_viewer_cannot_be_given_a_write_or_external_target(effect, approval):
    text = problems_of(tool(effect=effect, approval=approval, roles=("viewer", "operator")))
    assert f"role 'viewer' may not run a {effect} target" in text


def test_operator_and_admin_may_run_every_effect():
    for effect, approval in (("read", "none"), ("write", "required"), ("external", "none")):
        build_registry([Plugin(name="p", version="1", tools=(
            tool(effect=effect, approval=approval, roles=("operator", "admin")),))])


def test_a_plugin_cannot_invent_a_role():
    assert "unknown role(s) ['auditor']" in problems_of(tool(roles=("auditor",)))


def test_the_table_is_checked_again_on_every_call():
    """Even a target that skipped the registry cannot be run by a role the table denies."""
    unvalidated = tool(effect="write", approval="required", roles=("viewer", "operator"))
    assert not unvalidated.allows(VIEWER)
    assert unvalidated.allows(OPERATOR) and unvalidated.allows(ADMIN)
    assert tool().allows(VIEWER) and not tool(roles=("operator",)).allows(VIEWER)


# ---------------------------------------------------------------- inputs and outputs


def test_a_params_model_that_accepts_unknown_keys_is_refused():
    class Ignores(BaseModel):
        x: int = 0

    class Keeps(BaseModel):
        model_config = ConfigDict(extra="allow")

    for model in (Ignores, Keeps):
        assert f"params_model {model.__name__} accepts unknown keys" in problems_of(
            tool(params_model=model))


def test_a_workflow_input_model_must_forbid_unknown_keys_too():
    class Loose(BaseModel):
        x: int = 0

    step = Step(id="a", kind="deterministic", description="d", fn=_run, input_model=Loose)
    workflow = Workflow(name="w", description="d", roles=("operator",), effect="read",
                        steps=(step,), **DECLARED)
    assert "input_model of the first step Loose accepts unknown keys" in problems_of(workflow)
    fixed = replace(workflow, steps=(replace(step, input_model=Strict),))
    build_registry([Plugin(name="p", version="1", workflows=(fixed,))])


@pytest.mark.parametrize(("outputs", "needle"), [
    (None, "outputs must be ToolOutputs"),
    ({"aggregates": ("n",)}, "outputs must be ToolOutputs"),
    (ToolOutputs(aggregates="total", columns=()), "outputs.aggregates must be a tuple"),
    (ToolOutputs(aggregates=(), columns=("a", " ")), "outputs.columns must be a tuple"),
])
def test_outputs_must_be_a_real_declaration(outputs, needle):
    assert needle in problems_of(tool(outputs=outputs))


def test_a_label_for_an_aggregate_the_tool_cannot_emit_is_refused():
    labels = {"en": {"n": "Count", "ghost": "Ghost"}}
    assert "aggregate_labels names 'ghost', which outputs.aggregates does not declare" in \
        problems_of(tool(aggregate_labels=labels))
    build_registry([Plugin(name="p", version="1", tools=(
        tool(aggregate_labels={"en": {"n": "Count"}}),))])


# ---------------------------------------------------------------- exploration opt-in


def test_explorable_must_be_a_real_boolean():
    assert "explorable must be True or False" in problems_of(tool(explorable="yes"))


def test_an_explorable_external_target_must_wait_for_approval():
    external = {"effect": "external", "roles": ("operator",), "explorable": True}
    assert "an explorable external target must declare approval='required'" in problems_of(
        tool(approval="none", **external))
    build_registry([Plugin(name="p", version="1", tools=(tool(approval="required", **external),))])


# ---------------------------------------------------------------- what ships


def test_every_shipped_plugin_loads_under_the_contract():
    registry = load_registry(extra_paths=[EXAMPLES])
    targets = {**registry.tools, **registry.workflows}
    assert {"create_po", "supplier_scorecard", "expiring_soon", "reorder_plan"} <= set(targets)
    for name, target in targets.items():
        writes = target.effect is ResourceEffect.WRITE
        assert (target.approval is Approval.REQUIRED) == writes, name
        assert target.outputs.aggregates, name  # every shipped target reports figures
        assert not (target.explorable and target.effect is ResourceEffect.EXTERNAL), name
    write_names = {n for n, t in targets.items() if t.effect is ResourceEffect.WRITE}
    assert write_names == {"create_po"} and registry.tool("create_po").explorable
    assert all(t.explorable for n, t in targets.items() if n != "create_po")


def test_the_shipped_viewer_sees_no_write_tool():
    registry = load_registry(extra_paths=[EXAMPLES])
    names = {n for n, t in registry.tools.items() if t.allows(VIEWER)}
    assert "create_po" not in names and "inventory_lookup" in names
    assert "supplier_scorecard" not in names  # lists admin and operator only
