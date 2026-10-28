"""Business objects as the plugins declare them, and their stored records.

Everything is projected for the signed-in role: a field the role may not see is absent from
the entity description, from record rows, and from filters and sort keys (filtering or
sorting by a hidden field would reveal its values by inference).
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from twotrack.core import ResolvedEntity
from twotrack.core.i18n import Language
from twotrack.core.registry import Registry
from twotrack.core.store import project_rows
from twotrack.runtime import Runtime

from ..deps import Session, authenticated, get_runtime, reply_language
from ..errors import ApiError, errors, not_found
from ..projection import field_out
from ..schemas import CategoryOut, EntityList, EntityOut, RecordsPage

router = APIRouter(tags=["entities"])
RESERVED_PARAMS = frozenset({"sort", "page", "page_size"})


def entity_or_404(registry: Registry, entity_id: str) -> ResolvedEntity:
    entity = registry.entities.get(entity_id)
    if entity is None:
        raise not_found(f"Entity '{entity_id}'")
    return entity


def _entity_out(session: Session, registry: Registry, root: ResolvedEntity,
                language: Language) -> EntityOut:
    principal = session.principal
    categories = [
        CategoryOut(id=c.id, label=c.label_in(language), description=c.description_in(language),
                    extends=c.extends,
                    attrs=[field_out(f, language) for f in c.attrs if f.visible_to(principal)])
        for c in registry.entities.values() if c.lineage[0] == root.id and c.id != root.id]
    return EntityOut(
        id=root.id, label=root.label_in(language), description=root.description_in(language),
        primary_key=root.primary_key, label_field=root.label_field,
        fields=[field_out(f, language) for f in root.fields if f.visible_to(principal)],
        attrs=[field_out(f, language) for f in root.attrs if f.visible_to(principal)],
        categories=categories,
        hidden_fields=sum(1 for f in root.all_fields if not f.visible_to(principal)))


@router.get("/entities", response_model=EntityList, responses=errors(401))
def list_entities(session: Session = Depends(authenticated),
                  runtime: Runtime = Depends(get_runtime),
                  language: Language = Depends(reply_language)) -> EntityList:
    """Root entities with their category children, fields limited to what the role may see.

    Every `label` and `description` (entity, category, field) is in the language
    `Accept-Language` asks for (`zh`, `zh-CN` ...: Chinese; anything else, or no header:
    English). A text a plugin did not translate is its English one.
    """
    registry = runtime.registry
    return EntityList(items=[_entity_out(session, registry, e, language)
                             for e in registry.entities.values() if e.extends is None])


def _filters(request: Request, visible: set[str], entity_id: str) -> dict[str, Any]:
    filters: dict = {}
    for key in dict.fromkeys(request.query_params.keys()):
        if key in RESERVED_PARAMS:
            continue
        if key not in visible:
            raise ApiError(400, "unknown_field", f"'{key}' is not a field of '{entity_id}'")
        values = [None if v == "" else v for v in request.query_params.getlist(key)]
        filters[key] = values[0] if len(values) == 1 else values
    return filters


def _sort(sort: str | None, visible: set[str], entity_id: str) -> list[str]:
    keys = [k.strip() for k in (sort or "").split(",") if k.strip()]
    for key in keys:
        if key.lstrip("-") not in visible:
            raise ApiError(400, "unknown_field",
                           f"cannot sort by '{key.lstrip('-')}' on '{entity_id}'")
    return keys


@router.get("/entities/{entity_id}/records", response_model=RecordsPage,
            responses=errors(400, 401, 404, 422))
def list_records(
    entity_id: str,
    request: Request,
    sort: str | None = Query(default=None, description="Comma-separated field ids; '-' = desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(authenticated),
    runtime: Runtime = Depends(get_runtime),
) -> RecordsPage:
    """A page of records. Every other query parameter is an equality filter on a field id
    (`?status=active`); repeat it for several values (`?status=a&status=b` matches either).
    `sort`, `page` and `page_size` are reserved names."""
    entity = entity_or_404(runtime.registry, entity_id)
    visible = {f.id for f in entity.visible_fields(session.principal)}
    stored = runtime.store.list(
        entity.id, filters=_filters(request, visible, entity.id),
        sort=_sort(sort, visible, entity.id), limit=page_size, offset=(page - 1) * page_size)
    return RecordsPage(
        entity=entity.id, total=stored.total, page=page, page_size=page_size,
        items=project_rows(session.principal, runtime.registry, stored.rows, entity.id))


@router.get("/entities/{entity_id}/records/{pk}", response_model=dict[str, Any],
            responses=errors(400, 401, 404, 422))
def get_record(entity_id: str, pk: str, session: Session = Depends(authenticated),
               runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    """One record by primary key, projected for the role."""
    entity = entity_or_404(runtime.registry, entity_id)
    row = runtime.store.get(entity.id, pk)
    if row is None:
        raise not_found(f"Record '{pk}' of '{entity.id}'")
    return project_rows(session.principal, runtime.registry, [row], entity.id)[0]
