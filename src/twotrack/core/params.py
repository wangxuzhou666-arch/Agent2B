"""Param metadata that a Tool or Workflow declares on its params model, read by core.

A Tool's params model is ``params_model``; a Workflow's is its first step's ``input_model``.
Annotations go in a field's ``json_schema_extra`` dict. They are for the host: the schema a model
is shown (the LLM arm, the explorers, MCP clients) keeps standard JSON Schema keywords only
(core.model_schema)::

    category: str | None = Field(default=None, json_schema_extra={"binds": "category:sku"})

* ``binds``: the constraint the param applies (core.scope). ``"category:<entity>"`` takes the
  id of any entity that extends ``<entity>`` (sub-categories included); ``"field:<entity>.
  <field>"`` takes a value of an enum field declared with ``scope_key=True``. Routing fills an
  unset bound param from the question; the pipeline rejects a value the constraint does not
  have. A target binds each constraint with at most one param.
* ``label``: ``{"en": ..., "zh": ...}``, the param's name in confirmation summaries (default:
  the label of the target entity's field with the same id, else the humanized key).
* ``unset``: ``{"en": ..., "zh": ...}``, what leaving the param empty means ("best-ranked for
  this SKU"); a summary leaves out an empty param without one.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .contract import FieldSpec, Tool, Workflow
from .i18n import as_language, localized, say
from .narration import humanize_key

BINDS = "binds"
LABEL = "label"
UNSET = "unset"


def params_model(target: Any) -> type[BaseModel] | None:
    """The model a target's params are validated against, if it declares one."""
    if isinstance(target, Tool):
        model = target.params_model
    elif isinstance(target, Workflow) and target.steps:
        model = target.steps[0].input_model
    else:
        model = None
    return model if isinstance(model, type) and issubclass(model, BaseModel) else None


def annotations(target: Any) -> dict[str, dict[str, Any]]:
    """``json_schema_extra`` of every param that declares it as a dict."""
    model = params_model(target)
    if model is None:
        return {}
    return {name: dict(info.json_schema_extra) for name, info in model.model_fields.items()
            if isinstance(info.json_schema_extra, dict)}


def binds(target: Any) -> dict[str, Any]:
    """Raw ``binds`` declarations, param -> value (the registry validates them)."""
    return {name: extra[BINDS] for name, extra in annotations(target).items() if BINDS in extra}


def required_params(target: Any) -> tuple[str, ...]:
    """The params a target cannot run without: the required fields of its params model."""
    model = params_model(target)
    if model is None:
        return ()
    return tuple(name for name, info in model.model_fields.items() if info.is_required())


def gives_required_params(target: Any, params: Mapping[str, Any]) -> bool:
    """Whether ``params`` (what a question gave, before the pipeline validates it) hold every
    param the target requires, and the target's params model takes them as they are: the
    request is complete, nothing is left to ask. A target that requires no param never
    qualifies, since such a question gave nothing beyond naming the target."""
    required = required_params(target)
    if not required or any(params.get(name) is None for name in required):
        return False
    try:
        params_model(target).model_validate(dict(params))
    except (TypeError, ValueError):  # ValidationError is a ValueError
        return False
    return True


def target_title(target: Any, language: str | None) -> str:
    """A tool's or workflow's display name: its declared ``title`` in ``language`` (else the
    English one), else the humanized name ("low_stock" -> "Low stock")."""
    return localized(getattr(target, "title", None), language) or humanize_key(target.name)


def registry_title(registry: Any, name: str, language: str | None) -> str:
    """``target_title`` of the registry target called ``name``; the humanized ``name`` when it is
    not a target (a tool that was removed since, an import)."""
    known = name in registry.tools or name in registry.workflows
    return target_title(registry.target(name), language) if known else humanize_key(name)


def field_name(spec: FieldSpec, language: str | None) -> str:
    """A field's display name in ``language`` (its English label when it has none there)."""
    return spec.label_in(language)


def param_label(target: Any, name: str, language: str | None,
                entities: Mapping[str, Any] | None = None) -> str:
    declared = localized(annotations(target).get(name, {}).get(LABEL), language)
    if declared:
        return declared
    entity = (entities or {}).get(getattr(target, "entity", None) or "")
    if entity is not None:
        for spec in entity.all_fields:
            if spec.id == name:
                return field_name(spec, language)
    return humanize_key(name)


def _value_text(value: Any, language: str | None) -> str:
    if isinstance(value, bool):
        return say("yes" if value else "no", language)
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def describe_request(target: Any, params: Mapping[str, Any], language: str | None,
                     entities: Mapping[str, Any] | None = None) -> str:
    """What a confirmation asks for, in plain words of ``language``: the target's description
    in that language, then each param by its display name ("Supplier: best-ranked for this
    SKU"), never ``key=None``."""
    extras = annotations(target)
    zh = as_language(language) == "zh"
    description = target.description_in(language)
    parts = []
    for name, value in params.items():
        if value is None:
            text = localized(extras.get(name, {}).get(UNSET), language)
            if not text:
                continue
        else:
            text = _value_text(value, language)
        label = param_label(target, name, language, entities)
        parts.append(f"{label}：{text}" if zh else f"{label}: {text}")
    if not parts:
        return description
    return (f"{description}（{'；'.join(parts)}）" if zh
            else f"{description} ({'; '.join(parts)})")
