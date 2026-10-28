"""The parameter schemas a model is shown keep standard JSON Schema keywords only.

The host's params models are unchanged and keep validating every call with Pydantic; only what
leaves for a model (tool cards for the LLM arm and the built-in loop, the Pi worker's tools, the
MCP tool list) is cut down to JSON Schema keywords, at every depth, by structure.
"""

from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from tests.fakes import REGISTRY
from twotrack.core import load_registry
from twotrack.core.explore.pi import tool_specs
from twotrack.core.model_schema import STANDARD_KEYWORDS, model_facing, params_schema
from twotrack.core.registry import Candidates
from twotrack.core.routing.llm_arm import tool_card, tool_cards

_NAMED = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
_DATA = ("enum", "const", "default", "examples", "required", "dependentRequired")


def keywords(schema: Any) -> set[str]:
    """Every key in keyword position, at every depth (property names and data excluded)."""
    if not isinstance(schema, dict):
        return set()
    found = set(schema)
    for key, value in schema.items():
        if key in _NAMED and isinstance(value, dict):
            for sub in value.values():
                found |= keywords(sub)
        elif isinstance(value, list) and key not in _DATA:
            for sub in value:
                found |= keywords(sub)
        elif isinstance(value, dict) and key not in _DATA:
            found |= keywords(value)
    return found


class Inner(BaseModel):
    code: str = Field(json_schema_extra={"binds": "field:sku.code", "x-ui": "hidden"})


class Annotated(BaseModel):
    label: str = Field(description="a param that happens to be called label",
                       json_schema_extra={"label": {"en": "Label", "zh": "标签"}})
    supplier_id: str | None = Field(default=None, json_schema_extra={
        "label": {"en": "Supplier"}, "unset": {"en": "best-ranked"}})
    inner: Inner
    items: list[Inner] = []
    kind: Literal["a", "b"] = Field(default="a", json_schema_extra={"unset": {"en": "a"}})
    qty: int = Field(gt=0, json_schema_extra={"examples": [3]})


def test_custom_keys_go_at_every_depth_and_names_and_data_stay():
    raw = Annotated.model_json_schema()
    assert {"label", "unset", "binds", "x-ui"} <= keywords(raw)  # the raw schema has them

    shown = model_facing(raw)
    assert keywords(shown) <= STANDARD_KEYWORDS
    # a property called "label" is a name, not a keyword: it stays, with its description
    assert shown["properties"]["label"] == {
        "description": "a param that happens to be called label", "title": "Label",
        "type": "string"}
    # nested models, list items and unions are walked too
    assert shown["$defs"]["Inner"]["properties"]["code"] == {"title": "Code", "type": "string"}
    assert shown["properties"]["supplier_id"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
        "title": "Supplier Id"}
    # data keywords keep their values as written
    assert shown["properties"]["kind"]["enum"] == ["a", "b"]
    assert shown["properties"]["qty"] == {"exclusiveMinimum": 0, "examples": [3],
                                          "title": "Qty", "type": "integer"}
    assert shown["required"] == raw["required"]
    # and nothing else was touched: the raw schema minus the custom keys
    assert model_facing(shown) == shown


def test_host_side_validation_is_unchanged():
    model_facing(Annotated.model_json_schema())
    assert "label" in Annotated.model_json_schema()["properties"]["supplier_id"]
    with pytest.raises(ValidationError):
        Annotated.model_validate({"label": "x", "inner": {"code": "c"}, "qty": 0})
    assert Annotated.model_validate({"label": "x", "inner": {"code": "c"}, "qty": 1}).qty == 1


def test_a_boolean_or_empty_schema_passes_through():
    assert model_facing(True) is True
    assert model_facing({}) == {}
    assert model_facing({"type": "object", "additionalProperties": False, "label": 1}) == {
        "type": "object", "additionalProperties": False}


def test_every_built_in_target_reaches_a_model_with_standard_keywords_only():
    registry = load_registry(extra_paths=[])
    targets = [*registry.tools.values(), *registry.workflows.values()]
    raw = [t.params_model.model_json_schema() for t in registry.tools.values()]
    assert any(keywords(s) - STANDARD_KEYWORDS for s in raw)  # e.g. create_po's label/unset

    everything = Candidates(tools=registry.tools, workflows=registry.workflows)
    cards = {c.name: c.params_schema for c in tool_cards(everything)}  # LLM arm, built-in loop
    specs = {s.name: s.parameters for s in tool_specs(everything)}  # the Pi worker
    assert cards == specs and set(cards) == {t.name for t in targets}
    for target in targets:
        shown = tool_card(target).params_schema
        assert keywords(shown) <= STANDARD_KEYWORDS, target.name
        assert shown == params_schema(target)
    supplier = cards["create_po"]["properties"]["supplier_id"]
    assert "label" not in supplier and "unset" not in supplier


def test_a_fixture_schema_without_custom_keys_is_shown_as_written():
    target = REGISTRY.target("stock_level")
    assert params_schema(target) == target.params_model.model_json_schema()
