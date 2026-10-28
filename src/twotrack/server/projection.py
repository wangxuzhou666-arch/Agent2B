"""What a signed-in user may see of rows and fields.

Two callers need it, with different rules.

* Entity endpoints return stored rows. ``project_rows`` projects them strictly: only the
  entity's declared, visible fields survive.
* Query results come from tools, whose rows mix entity fields with computed columns
  (``shortfall``, ``reorder_flag``) and with fields of other entities (a stock row that also
  shows the supplier's public name). Dropping everything the bound entity does not declare
  would silently delete useful columns, so a result key is removed only when it is *hidden
  from this user*, never because it is merely unknown to the tool's entity.

Tools are role-gated, so a computed column is the tool author's responsibility.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from twotrack.core import FieldSpec, Principal, ResolvedEntity
from twotrack.core.registry import Registry
from twotrack.core.store import ENTITY_KEY

from .schemas import FieldOut


def field_out(spec: FieldSpec, language: str | None = None) -> FieldOut:
    """A field as the entity endpoints describe it, its label and description in ``language``."""
    return FieldOut(id=spec.id, label=spec.label_in(language),
                    description=spec.description_in(language), type=spec.type,
                    required=spec.required, aliases=list(spec.aliases),
                    enum_values=list(spec.enum_values), unit=spec.unit,
                    sensitivity=spec.sensitivity)


def target_entity(registry: Registry, target: str | None) -> ResolvedEntity | None:
    if target is None or target not in registry.tools:
        return None
    entity_id = registry.tools[target].entity
    return registry.entities.get(entity_id) if entity_id else None


def hidden_keys(principal: Principal, registry: Registry,
                entity: ResolvedEntity | None) -> frozenset[str]:
    """Result keys that must go for ``principal`` when the rows belong to ``entity``.

    * a field ``entity`` declares: hidden when ``entity`` hides it from the user;
    * a field only other entities declare: hidden when any of them hides it, so an id that two
      entities reuse with different rules gets the cautious reading;
    * a key no entity declares is a computed column and stays.
    """
    own = {f.id: f.visible_to(principal) for f in entity.all_fields} if entity else {}
    elsewhere = {f.id for other in registry.entities.values() for f in other.all_fields
                 if not f.visible_to(principal)}
    return frozenset({fid for fid, visible in own.items() if not visible}
                     | (elsewhere - own.keys()))


def _row_entity(registry: Registry, row: Mapping[str, Any],
                bound: ResolvedEntity | None) -> ResolvedEntity | None:
    """A row that names its category (``_entity``) is governed by that category's fields."""
    name = row.get(ENTITY_KEY)
    return (registry.entities.get(name) if isinstance(name, str) else None) or bound


def project_result(principal: Principal, registry: Registry, target: str | None,
                   columns: Iterable[str], rows: Iterable[Mapping[str, Any]]
                   ) -> tuple[list[str], list[dict[str, Any]]]:
    """Columns and rows of a query result, minus what is hidden from ``principal``."""
    bound = target_entity(registry, target)
    cache: dict[str | None, frozenset[str]] = {}

    def hidden_for(entity: ResolvedEntity | None) -> frozenset[str]:
        key = entity.id if entity else None
        if key not in cache:
            cache[key] = hidden_keys(principal, registry, entity)
        return cache[key]

    projected = []
    for row in rows:
        hidden = hidden_for(_row_entity(registry, row, bound))
        projected.append({k: v for k, v in row.items() if k not in hidden})
    hidden_columns = hidden_for(bound).union(*cache.values())  # any row's entity may hide it
    return [c for c in columns if c not in hidden_columns], projected
