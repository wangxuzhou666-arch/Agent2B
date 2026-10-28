"""The built-in plugins and the drop-in example label and describe every entity, category and
field in English and Chinese, with the terms the tools and the scope guard already use."""

import re
from pathlib import Path

import pytest

from twotrack.core import load_registry
from twotrack.core.i18n import say
from twotrack.core.scope import category_key
from twotrack.core.text import has_cjk

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"


@pytest.fixture(scope="module")
def registry():
    return load_registry(extra_paths=[EXAMPLES])


def everything(registry):
    """(where, label table, description table) of every entity, category and field."""
    for entity in registry.entities.values():
        yield f"entity {entity.id}", entity.labels, entity.descriptions
        inherited = {f.id for f in (registry.entities[entity.extends].all_fields
                                    if entity.extends else ())}
        for spec in entity.all_fields:
            if spec.id not in inherited:  # a category shares its parent's fields: check them once
                yield f"{entity.id}.{spec.id}", spec.labels, spec.descriptions


def test_there_is_something_to_check(registry):
    places = [where for where, *_ in everything(registry)]
    assert len(places) > 40
    assert {"entity packaging", "entity spare_part", "entity cold_chain_item",
            "purchase_order.eta", "packaging.material", "cold_chain_item.shelf_life_days"
            } <= set(places)


def test_every_label_and_description_is_declared_in_both_languages(registry):
    for where, label, description in everything(registry):
        assert set(label) == {"en", "zh"}, where
        assert set(description) == {"en", "zh"}, where
        assert not has_cjk(label["en"]) and has_cjk(label["zh"]), (where, label)
        assert not has_cjk(description["en"]) and has_cjk(description["zh"]), (where, description)
        assert label["zh"] != label["en"], where


def test_a_chinese_unit_is_left_to_the_console(registry):
    """A field that declares a unit has it appended to the label by the console, so a Chinese
    label that carried its own would show the unit twice."""
    for entity in registry.entities.values():
        for spec in entity.all_fields:
            if spec.unit:
                assert not re.search(r"[（(]", spec.label_in("zh")), (entity.id, spec.id)


def test_a_categorys_chinese_label_is_the_name_the_scope_guard_uses(registry):
    """The scope guard names a category by its label in the reply language (core.scope), so
    a scope clarification and the console call it the same."""
    categories = [e for e in registry.entities.values() if e.extends]
    assert {e.id for e in categories} == {"packaging", "spare_part", "cold_chain_item"}
    for entity in categories:
        assert registry.vocabulary.label(category_key("sku"), entity.id, "zh") == \
            entity.label_in("zh"), entity.id
    assert registry.entity("packaging").label_in("zh") == "包装材料"
    assert say("scope_kind_category", "zh") == "类别"


def test_the_terms_are_the_ones_the_tools_already_use(registry):
    sku, inventory = registry.entity("sku"), registry.entity("inventory")
    supplier, order = registry.entity("supplier"), registry.entity("purchase_order")
    for entity in (sku, inventory, order):  # one word for a SKU's code everywhere
        assert entity.field("sku_id").label_in("zh") == "物料编码"
    assert supplier.label_in("zh") == "供应商" and order.label_in("zh") == "采购单"
    assert order.field("supplier_id").label_in("zh") == "供应商编码"
    assert order.field("qty").label_in("zh") == "数量"
    assert order.field("status").label_in("zh") == "状态"
    assert inventory.field("warehouse").label_in("zh") == "仓库"
    assert sku.field("criticality").label_in("zh") == "重要等级"
    assert registry.entity("cold_chain_item").field("shelf_life_days").label_in("zh") == "保质期"


def test_a_chinese_sheet_imports_by_the_chinese_labels_alone(registry):
    inventory = registry.entity("inventory")
    headers = {"记录编号": "id", "物料编码": "sku_id", "仓库": "warehouse", "在库数量": "on_hand",
               "预留数量": "reserved", "盘点日期": "as_of"}
    assert {h: inventory.match_header(h).id for h in headers} == headers
    assert registry.entity("supplier").match_header("联系邮箱").id == "contact_email"
    assert registry.entity("purchase_order").match_header("预计到货日期").id == "eta"
    assert registry.entity("packaging").match_header("尺寸（mm）").id == "dimensions_mm"


def test_every_tool_and_workflow_is_described_in_both_languages(registry):
    targets = [*registry.tools.values(), *registry.workflows.values()]
    assert {"create_po", "reorder_plan", "expiring_soon"} <= {t.name for t in targets}
    for target in targets:
        table = target.descriptions
        assert set(table) == {"en", "zh"}, target.name
        assert not has_cjk(table["en"]) and has_cjk(table["zh"]), (target.name, table)
        assert target.description == table["en"], target.name  # what English readers saw
        assert "SKU" not in table["zh"], target.name  # an item is 物料, as the entity says


# (entity, field or None for the entity itself) whose Chinese label each description uses
TERMS = {
    "inventory_lookup": [("inventory", "warehouse"), ("inventory", "on_hand"),
                         ("inventory", "reserved")],
    "low_stock": [("sku", "reorder_point"), ("inventory", "on_hand"), ("inventory", "reserved"),
                  ("purchase_order", None)],
    "reorder_suggestion": [("sku", "avg_daily_demand"), ("sku", "lead_time_days"),
                           ("sku", "reorder_point")],
    "po_status": [("purchase_order", None), ("purchase_order", "po_id"), ("supplier", None),
                  ("purchase_order", "status")],
    "late_shipments": [("purchase_order", None), ("purchase_order", "eta")],
    "supplier_compare": [("supplier", None), ("supplier", "rating"),
                         ("supplier", "avg_lead_time_days")],
    "create_po": [("purchase_order", None), ("supplier", None)],
    "reorder_plan": [("sku", "criticality"), ("supplier", None)],
    "expiring_soon": [("cold_chain_item", None), ("inventory", "as_of"),
                      ("cold_chain_item", "shelf_life_days")],
    "supplier_scorecard": [("supplier", None), ("purchase_order", None),
                           ("purchase_order", "eta")],
}


def test_chinese_descriptions_use_the_chinese_labels(registry):
    assert set(TERMS) == {*registry.tools, *registry.workflows}
    for name, terms in TERMS.items():
        text = registry.target(name).description_in("zh")
        for entity_id, field_id in terms:
            entity = registry.entity(entity_id)
            label = (entity.field(field_id) if field_id else entity).label_in("zh")
            assert label in text, (name, label)
    # PO statuses by their Chinese names (the first Chinese synonym of each value)
    order_status = registry.vocabulary.kinds["field:purchase_order.status"]
    late = registry.target("late_shipments").description_in("zh")
    assert all(order_status.label(v, "zh") in late for v in ("open", "shipped"))
    assert order_status.label("draft", "zh") in registry.target("create_po").description_in("zh")


def test_english_texts_are_unchanged_by_the_translation(registry):
    """What English readers saw before is still there, label for label."""
    sku, order = registry.entity("sku"), registry.entity("purchase_order")
    assert [f.label_in("en") for f in sku.fields] == [
        "SKU ID", "Name", "Unit", "Unit Cost", "Avg Daily Demand", "Reorder Point",
        "Lead Time (days)", "Criticality"]
    assert [f.label_in("en") for f in order.fields] == [
        "PO ID", "SKU", "Supplier ID", "Quantity", "Unit Price", "Status", "Ordered At", "ETA"]
    assert registry.entity("packaging").label == "Packaging"
    assert registry.entity("spare_part").description == "A maintenance spare for a machine model."
