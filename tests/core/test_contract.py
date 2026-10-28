import math
from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from twotrack.core import (
    EntitySpec,
    FieldSpec,
    FieldType,
    Plugin,
    Principal,
    Provenance,
    RegistryError,
    ResolvedEntity,
    ResourceEffect,
    RouteDecision,
    Sensitivity,
    Step,
    Tool,
    ToolOutputs,
    ToolResult,
    TraceStep,
    Workflow,
    build_registry,
    resolve_entity,
)

VIEWER = Principal(user_id="u1", roles={"viewer"})
OPERATOR = Principal(user_id="u2", roles={"operator"})
ADMIN = Principal(user_id="u3", roles={"admin"})


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int = 0


# the declarations of a read that returns nothing; a test that needs another overrides it
READ_DECLARED = {"approval": "none", "outputs": ToolOutputs(aggregates=(), columns=()),
                 "explorable": False}


def _noop(ctx, data):
    return data


def _resolved() -> ResolvedEntity:
    base = EntitySpec(
        id="widget", label="Widget", primary_key="widget_id",
        fields=(
            FieldSpec(id="widget_id", label="Widget ID"),
            FieldSpec(id="qty", label="Quantity", type=FieldType.INT, aliases=("qty on hand",)),
            FieldSpec(id="cost", label="Unit Cost", sensitivity=Sensitivity.RESTRICTED,
                      visible_roles=frozenset({"operator"})),
        ),
    )
    child = EntitySpec(id="gizmo", label="Gizmo", extends="widget",
                       attrs=(FieldSpec(id="voltage", label="Voltage", unit="V"),))
    return resolve_entity(child, resolve_entity(base, None))


def test_principal_rejects_unknown_role():
    with pytest.raises(ValidationError):
        Principal(user_id="u", roles={"superuser"})


def test_role_allow_list_admin_passes_everything():
    assert VIEWER.can_access(None)
    assert not VIEWER.can_access(("operator",))
    assert OPERATOR.can_access(("operator", "viewer"))
    assert ADMIN.can_access(("operator",))


@pytest.mark.parametrize("kwargs", [
    {"type": FieldType.ENUM},
    {"type": FieldType.STR, "enum_values": ("a",)},
    {"sensitivity": Sensitivity.RESTRICTED},
])
def test_field_spec_rejects_inconsistent_declarations(kwargs):
    with pytest.raises(ValidationError):
        FieldSpec(id="f", label="F", **kwargs)


def test_resolve_entity_merges_parent_chain():
    gizmo = _resolved()
    assert gizmo.lineage == ("widget", "gizmo")
    assert gizmo.primary_key == "widget_id" and gizmo.label_field == "widget_id"
    assert [f.id for f in gizmo.fields] == ["widget_id", "qty", "cost"]
    assert [f.id for f in gizmo.attrs] == ["voltage"]
    assert gizmo.field("voltage").unit == "V"


def test_header_matching_ignores_case_space_and_separators():
    gizmo = _resolved()
    assert gizmo.match_header("  QTY on  Hand ").id == "qty"
    assert gizmo.match_header("Widget_ID").id == "widget_id"
    assert gizmo.match_header("unit-cost").id == "cost"
    assert gizmo.match_header("colour") is None


def test_visible_fields_follow_roles():
    gizmo = _resolved()
    assert "cost" not in {f.id for f in gizmo.visible_fields(VIEWER)}
    assert "cost" in {f.id for f in gizmo.visible_fields(OPERATOR)}
    assert "cost" in {f.id for f in gizmo.visible_fields(ADMIN)}


def _result(**aggregates) -> ToolResult:
    return ToolResult(
        rows=[{"widget_id": "w-1", "qty": 41}, {"widget_id": "w-2", "qty": 9}],
        aggregates=aggregates,
        columns=["widget_id", "qty"],
        provenance=Provenance(tool="t", row_count=120, as_of="2026-01-01T00:00:00Z"),
    )


def test_allowed_numbers_come_from_aggregates_and_counts():
    numbers = _result(total_qty=50, mean_qty=25.0).allowed_numbers()
    assert numbers == {50, 25.0, 120, 2}
    assert 41 not in numbers  # row values are never citable


def test_narrator_view_never_contains_rows():
    view = _result(total_qty=50).narrator_view()
    assert "rows" not in view and "w-1" not in str(view)
    assert view["aggregates"] == {"total_qty": 50} and view["row_count"] == 120


@pytest.mark.parametrize("bad", [True, math.nan, "50"])
def test_aggregates_must_be_finite_numbers(bad):
    with pytest.raises(ValidationError):
        _result(total_qty=bad)


@pytest.mark.parametrize("kwargs", [
    {"kind": "tool"},
    {"kind": "tool", "target": "t", "confidence": 1.5},
    {"kind": "tool", "target": "t", "clarify_options": ["a", "b"]},
])
def test_route_decision_validation(kwargs):
    with pytest.raises(ValidationError):
        RouteDecision(**{"confidence": 0.9, "source": "rule", **kwargs})


def test_route_decision_clarify_and_trace_are_json_safe():
    d = RouteDecision(kind="clarify", confidence=0.4, source="llm", clarify_options=["a", "b"])
    assert RouteDecision.model_validate_json(d.model_dump_json()) == d
    with pytest.raises(ValidationError):
        TraceStep(step="gate", status="ok", facts={"x": object()})


def test_serialized_types_emit_json_schema():
    for model in (EntitySpec, ResolvedEntity, RouteDecision, TraceStep, ToolResult, Principal):
        assert model.model_json_schema()["type"] == "object"


def test_tool_requires_explicit_effect_and_normalizes_sequences():
    with pytest.raises(TypeError):
        Tool(name="t", description="d", params_model=Out, handler=_noop, roles=("viewer",))
    tool = Tool(name="t", description="d", params_model=Out, handler=_noop,
                roles=["viewer"], effect="write", **{**READ_DECLARED, "approval": "required"})
    assert tool.roles == ("viewer",) and tool.effect is ResourceEffect.WRITE
    assert tool.parse_params({"value": "3"}).value == 3


def test_a_title_is_optional_per_language_and_falls_back_to_the_name():
    from twotrack.core.params import target_title

    bare = Tool(name="late_po_count", description="d", params_model=Out, handler=_noop,
                roles=("viewer",), effect="read", **READ_DECLARED)
    assert bare.problems() == [] and target_title(bare, "zh") == "Late PO count"
    titled = Tool(name="t", description="d", params_model=Out, handler=_noop, roles=("viewer",),
                  effect="read", title={"en": "Stock", "zh": "库存"}, **READ_DECLARED)
    assert (target_title(titled, "en"), target_title(titled, "zh")) == ("Stock", "库存")
    for bad in ({"fr": "Stock"}, {"en": " "}, {"en": 3}, "Stock"):
        broken = Tool(name="t", description="d", params_model=Out, handler=_noop,
                      roles=("viewer",), effect="read", title=bad, **READ_DECLARED)
        assert any("title" in p for p in broken.problems()), bad


def _tool(**kwargs) -> Tool:
    return Tool(**{"name": "t", "params_model": Out, "handler": _noop, "roles": ("viewer",),
                   "effect": "read", **READ_DECLARED, **kwargs})


def test_a_plain_description_is_the_text_of_every_language():
    tool = _tool(description="Count parts.")
    assert tool.description == "Count parts." and tool.problems() == []
    assert tool.descriptions == {"en": "Count parts.", "zh": "Count parts."}
    assert [tool.description_in(lang) for lang in ("en", "zh", None, "fr")] == \
        ["Count parts."] * 4


def test_a_description_per_language_keeps_english_as_the_plain_text():
    tool = _tool(description={"en": "Count parts.", "zh": "统计零件数量。"})
    assert tool.problems() == []
    assert tool.description == "Count parts."  # code that does not care about the language
    assert tool.description_in("zh") == "统计零件数量。"
    assert tool.description_in("en") == "Count parts."
    assert tool.description_in("fr") == "Count parts."
    workflow = Workflow(name="w", description={"en": "Plan.", "zh": "计划。"}, roles=("viewer",),
                        effect="read", **READ_DECLARED,
                        steps=[Step(id="s", kind="deterministic", description="d", fn=_noop)])
    assert (workflow.description, workflow.description_in("zh")) == ("Plan.", "计划。")


def test_a_copy_keeps_every_language_and_a_new_description_replaces_them():
    tool = _tool(description={"en": "Count parts.", "zh": "统计零件数量。"})
    titled = replace(tool, title={"en": "Parts"})
    assert titled.description_in("zh") == "统计零件数量。" and titled.problems() == []
    redescribed = replace(tool, description={"en": "Count.", "zh": "计数。"})
    assert redescribed.description_in("zh") == "计数。" and redescribed.problems() == []
    plain = replace(tool, description="Count.")  # a plain string: every language, no stale zh
    assert plain.descriptions == {"en": "Count.", "zh": "Count."} and plain.problems() == []
    assert replace(plain, title={"en": "Parts"}).descriptions == plain.descriptions


def test_a_table_declared_beside_a_plain_description_must_agree_with_it():
    assert _tool(description="Count.", descriptions={"en": "Count.", "zh": "计数。"}
                 ).description_in("zh") == "计数。"
    disagree = _tool(description="Count.", descriptions={"en": "Tally.", "zh": "计数。"})
    assert any("differ" in p for p in disagree.problems())
    no_english = _tool(description="Count.", descriptions={"zh": "计数。"})
    assert any("description needs a 'en' text" in p for p in no_english.problems())


@pytest.mark.parametrize(("description", "problem"), [
    ("", "description is empty"),
    ("  ", "description is empty"),
    ({}, "description is empty"),
    ({"zh": "统计零件数量。"}, "description needs a 'en' text"),
    ({"en": "Count parts.", "fr": "Compter."}, "language 'fr' is not supported"),
    ({"en": "Count parts.", "zh": " "}, "description for 'zh' is empty"),
    ({"en": 3}, "description must be a string or map a language"),
    (None, "description must be a string or map a language"),
])
def test_a_missing_or_broken_description_is_a_problem(description, problem):
    problems = _tool(description=description).problems()
    assert any(problem in p and "tool 't'" in p for p in problems), problems


def test_a_broken_description_stops_the_registry_load():
    plugin = Plugin(name="p", version="0.1.0", tools=(_tool(description={"zh": "统计。"}),))
    with pytest.raises(RegistryError, match="description needs a 'en' text"):
        build_registry([plugin])


def _workflow(kinds: list[str], first_output=None) -> Workflow:
    steps = [Step(id=f"s{i}", kind=k, description="d", fn=_noop,
                  output_model=first_output if i == 0 else None) for i, k in enumerate(kinds)]
    return Workflow(name="w", description="d", roles=("operator",), effect="read",
                    **READ_DECLARED, steps=steps)


@pytest.mark.parametrize("kinds, first_output, expected", [
    (["deterministic"], None, None),
    (["deterministic", "llm"], None, None),
    (["llm", "deterministic", "llm"], Out, None),
    (["llm"], None, "at least one deterministic step"),
    (["llm", "llm"], Out, "at least one deterministic step"),
    (["deterministic", "llm", "deterministic"], None, "llm step 's1' is a middle step"),
    (["llm", "deterministic"], None, "first llm step 's0' must declare output_model"),
    (["deterministic", "magic"], None, "unknown kind 'magic'"),
])
def test_workflow_step_kind_rule(kinds, first_output, expected):
    problems = _workflow(kinds, first_output).problems()
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems
