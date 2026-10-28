"""The parameter schema a model is shown for a tool: standard JSON Schema keywords only.

A target's params model is the host's contract: the pipeline validates every call against it,
with Pydantic, unchanged. The JSON Schema Pydantic writes from that model also carries keys that
are no JSON Schema keyword: the annotations core reads from a param's ``json_schema_extra``
(``binds``, ``label``, ``unset``; core.params) and anything else a plugin puts there, plus
OpenAPI's ``discriminator``. A model has no use for them, some providers refuse a schema that
holds them, and a label written for a person is not an instruction for a model. So every schema
that leaves for a model (the routing arm's and the built-in loop's tool cards, the Pi worker's
tool list, the MCP tool list) goes through ``model_facing``, which keeps only the keywords of
JSON Schema 2020-12 (and draft-07's ``definitions``, ``dependencies`` and ``additionalItems``).

The walk follows the schema's structure, so a property that happens to be called ``label`` stays:
only keys in keyword position are dropped. Values that are data (``enum``, ``const``,
``default``, ``examples``, ``required``) are kept as written.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import JsonValue

from .contract import Tool, Workflow

# subschemas by name: the keys are names, never keywords
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions",
                          "dependentSchemas"})
# one subschema (``items`` may also be draft-07's list of them)
_SCHEMA_VALUES = frozenset({"additionalProperties", "propertyNames", "items", "additionalItems",
                            "contains", "not", "if", "then", "else", "unevaluatedItems",
                            "unevaluatedProperties", "contentSchema"})
_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})

STANDARD_KEYWORDS = frozenset({
    # core
    "$schema", "$id", "$ref", "$anchor", "$dynamicRef", "$dynamicAnchor", "$vocabulary",
    "$comment", "$defs", "definitions",
    # applicators
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "dependentSchemas", "dependencies",
    "prefixItems", "items", "additionalItems", "contains", "properties", "patternProperties",
    "additionalProperties", "propertyNames", "unevaluatedItems", "unevaluatedProperties",
    # validation
    "type", "enum", "const", "multipleOf", "maximum", "exclusiveMaximum", "minimum",
    "exclusiveMinimum", "maxLength", "minLength", "pattern", "maxItems", "minItems",
    "uniqueItems", "maxContains", "minContains", "maxProperties", "minProperties", "required",
    "dependentRequired",
    # metadata, format, content
    "title", "description", "default", "deprecated", "readOnly", "writeOnly", "examples",
    "format", "contentEncoding", "contentMediaType", "contentSchema",
})


def model_facing(schema: Any) -> Any:
    """``schema`` with every key in keyword position that is not a standard keyword removed,
    at every depth. A boolean schema, or anything that is not a mapping, comes back as is."""
    if not isinstance(schema, Mapping):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in STANDARD_KEYWORDS:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, Mapping):
            out[key] = {name: model_facing(sub) for name, sub in value.items()}
        elif key in _SCHEMA_VALUES:
            out[key] = ([model_facing(v) for v in value] if isinstance(value, list)
                        else model_facing(value))
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            out[key] = [model_facing(v) for v in value]
        elif key == "dependencies" and isinstance(value, Mapping):  # a schema or a name list
            out[key] = {name: model_facing(sub) for name, sub in value.items()}
        else:  # data, or a keyword whose value is not a schema
            out[key] = value
    return out


def params_schema(target: Any) -> dict[str, JsonValue]:
    """The model-facing JSON Schema of a target's parameters: its params model's (a Tool's
    ``params_model``, a Workflow's first step's input model), standard keywords only."""
    model = None
    if isinstance(target, Tool):
        model = target.params_model
    elif isinstance(target, Workflow) and target.steps:
        model = target.steps[0].input_model
    return model_facing(model.model_json_schema()) if model is not None else {"type": "object"}
