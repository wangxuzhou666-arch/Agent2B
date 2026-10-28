"""The built-in plugins load cleanly and declare the contract the console relies on."""

import pytest
from world import ADMIN, OPERATOR, VIEWER, build_world, builtin_registry, load, sku

from twotrack.core import ResourceEffect, Sensitivity, StepKind
from twotrack.core.store import table_name

READ_TOOLS = {"inventory_lookup", "low_stock", "reorder_suggestion", "po_status",
              "late_shipments", "supplier_compare"}


def test_builtin_registry_loads_with_no_problems():
    registry = builtin_registry()  # raises RegistryError listing every problem otherwise
    assert [p.name for p in registry.plugins] == ["packaging", "spare_parts", "supply_chain"]
    assert set(registry.entities) == {"sku", "spare_part", "packaging", "inventory",
                                      "supplier", "purchase_order"}
    assert set(registry.tools) == READ_TOOLS | {"create_po"}
    assert list(registry.workflows) == ["reorder_plan"]
    assert set(registry.preconditions) == {"has_inventory_data", "has_suppliers"}


def test_reads_are_open_to_every_role_and_the_write_is_not():
    registry = builtin_registry()
    for name in READ_TOOLS:
        tool = registry.tool(name)
        assert tool.effect is ResourceEffect.READ
        assert set(tool.roles) == {"admin", "operator", "viewer"}
    create = registry.tool("create_po")
    assert create.effect is ResourceEffect.WRITE
    assert set(create.roles) == {"admin", "operator"}
    assert create.requires == ("has_suppliers",)
    assert not create.allows(VIEWER) and create.allows(OPERATOR) and create.allows(ADMIN)


def test_every_target_routes_in_both_languages():
    registry = builtin_registry()
    for target in [*registry.tools.values(), *registry.workflows.values()]:
        words = [*target.keywords, *target.examples]
        assert any(w.isascii() for w in words), target.name
        assert any(not w.isascii() for w in words), target.name
        assert target.param_extractor is not None, target.name


def test_reorder_plan_is_deterministic_steps_then_one_final_narration():
    plan = builtin_registry().workflow("reorder_plan")
    assert [s.kind for s in plan.steps] == [StepKind.DETERMINISTIC] * 3 + [StepKind.LLM]
    assert plan.effect is ResourceEffect.READ
    assert all(s.output_model is not None for s in plan.steps[:3])


def test_categories_extend_sku_with_attrs_only():
    registry = builtin_registry()
    for category, attrs in (("spare_part", ["machine_model", "interchangeable_with"]),
                            ("packaging", ["material", "dimensions_mm", "recyclable"])):
        entity = registry.entity(category)
        assert entity.lineage == ("sku", category)
        assert entity.fields == registry.entity("sku").fields
        assert [f.id for f in entity.attrs] == attrs


@pytest.mark.parametrize(("entity", "header", "field"), [
    ("sku", "Item Code", "sku_id"),
    ("sku", "物料编码", "sku_id"),
    ("sku", "ROP", "reorder_point"),
    ("sku", "ABC Class", "criticality"),
    ("spare_part", "适用机型", "machine_model"),
    ("packaging", "Dimensions", "dimensions_mm"),
    ("inventory", "Qty On Hand", "on_hand"),
    ("inventory", "仓库", "warehouse"),
    ("purchase_order", "Qty", "qty"),
    ("purchase_order", "Quantity", "qty"),
    ("purchase_order", "数量", "qty"),
    ("purchase_order", "ETA", "eta"),
    ("purchase_order", "预计到货", "eta"),
    ("purchase_order", "SKU", "sku_id"),
    ("supplier", "Vendor Name", "name"),
])
def test_spreadsheet_headers_map_through_aliases(entity, header, field):
    assert builtin_registry().entity(entity).match_header(header).id == field


def test_supplier_contact_and_terms_are_restricted_to_admins():
    supplier = builtin_registry().entity("supplier")
    restricted = {f.id for f in supplier.all_fields if f.sensitivity is Sensitivity.RESTRICTED}
    assert restricted == {"contact_email", "contract_discount_pct"}
    assert {f.id for f in supplier.visible_fields(ADMIN)} >= restricted
    for principal in (OPERATOR, VIEWER):
        assert not restricted & {f.id for f in supplier.visible_fields(principal)}


def test_category_rows_share_the_sku_table_with_no_extra_columns():
    world = build_world()
    load(world, skus=[sku("SKU-1", criticality="A"),
                      sku("SKU-2", entity="packaging", recyclable="yes", material="paper")])
    with world.db.read() as conn:
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table_name('sku')})")]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert columns == [f.id for f in world.registry.entity("sku").fields] + ["_entity", "_attrs"]
    assert not {"e_spare_part", "e_packaging"} & tables
    assert world.store.get("sku", "SKU-2")["_entity"] == "packaging"
    assert world.store.get("packaging", "SKU-2")["recyclable"] is True
    assert world.store.count("sku") == 2 and world.store.count("spare_part") == 1
