"""The category filter on the built-in reads, and human confirmation summaries.

Handcrafted store (handcrafted.py): spare parts SKU-1, 3, 4, 7, 8; packaging SKU-2, 5, 6.
"""

import pytest
from world import OPERATOR, build_world, builtin_registry, load, po, sku, stock

from twotrack.core import EntitySpec, Plugin, build_registry, load_registry
from twotrack.core.errors import InputProblem


def _ids(result):
    return [r["sku_id"] for r in result.rows]


def test_inventory_lookup_by_category(small):
    result = small.call("inventory_lookup", {"category": "packaging"})
    assert _ids(result) == ["SKU-2", "SKU-5", "SKU-6"]
    assert result.aggregates == {"total_on_hand": 23, "total_reserved": 5,
                                 "total_available": 18, "sku_count": 3}
    assert result.provenance.scope == "Category: Packaging"
    east = small.call("inventory_lookup", {"category": "packaging", "warehouse": "WH-EAST"})
    assert _ids(east) == ["SKU-6"]
    # a SKU of another category is outside the scope: no rows rather than its rows
    assert small.call("inventory_lookup", {"category": "packaging", "sku_id": "SKU-1"}).rows == []


def test_low_stock_and_reorder_suggestion_by_category(small):
    spare = small.call("low_stock", {"category": "spare_part"})
    assert _ids(spare) == ["SKU-1", "SKU-4", "SKU-3"]
    assert spare.aggregates == {"low_stock_count": 3, "class_a_count": 1, "total_shortfall": 15}
    assert _ids(small.call("low_stock", {"category": "packaging"})) == ["SKU-5"]
    assert _ids(small.call("low_stock", {"category": "spare_part", "criticality": "C"})) == [
        "SKU-3"]
    suggestion = small.call("reorder_suggestion", {"category": "packaging"})
    assert {r["sku_id"]: r["suggested_qty"] for r in suggestion.rows} == {"SKU-5": 1}
    assert suggestion.provenance.scope == "SKUs below their reorder point, Category: Packaging"
    other = small.call("reorder_suggestion", {"category": "spare_part", "sku_id": "SKU-2"})
    assert other.rows == [] and "category spare_part" in other.message


def test_purchase_order_reports_by_category(small):
    orders = small.call("po_status", {"category": "packaging"})
    assert [r["po_id"] for r in orders.rows] == ["PO-7", "PO-6"]
    assert orders.aggregates["po_count"] == 2
    late = small.call("late_shipments", {"category": "spare_part"})
    assert [r["po_id"] for r in late.rows] == ["PO-1", "PO-2"]
    assert small.call("late_shipments", {"category": "packaging"}).aggregates["late_count"] == 0


def test_reorder_plan_by_category(small):
    outcome = small.run(OPERATOR, "reorder_plan", {"category": "Packaging"})
    assert outcome.status == "ok", outcome.problem
    assert [r["sku_id"] for r in outcome.result.rows] == ["SKU-5"]
    assert outcome.result.provenance.scope == "Category: Packaging"


def test_sub_categories_are_included():
    """A category that extends spare_part is still a spare part for the filter."""
    consumable = EntitySpec(id="consumable_spare", label="Consumable Spare",
                            extends="spare_part")
    plugins = [p for p in builtin_registry().plugins]
    registry = build_registry([*plugins, Plugin(name="consumables", version="1",
                                                 entities=(consumable,))])
    world = build_world(registry)
    load(world, skus=[sku("SKU-1", reorder_point=5, machine_model="M-1"),
                      ("consumable_spare", {"sku_id": "SKU-9", "name": "Filter",
                                            "machine_model": "M-1", "reorder_point": 5}),
                      sku("SKU-2", entity="packaging", reorder_point=5)],
         inventory=[stock("INV-1", "SKU-1", "WH-EAST", 1)],
         orders=[po("PO-1", "SKU-9", "SUP-001", 3, "open", eta="2026-09-01")])
    assert _ids(world.call("low_stock", {"category": "spare_part"})) == ["SKU-1", "SKU-9"]
    assert _ids(world.call("low_stock", {"category": "consumable_spare"})) == ["SKU-9"]
    assert [r["po_id"] for r in world.call("late_shipments", {"category": "spare_part"}).rows] \
        == ["PO-1"]


def test_a_drop_in_category_filters_like_a_builtin_one(monkeypatch):
    from pathlib import Path

    from twotrack.core import PLUGIN_PATHS_ENV

    monkeypatch.setenv(PLUGIN_PATHS_ENV, str(Path(__file__).resolve().parents[2] / "examples"
                                             / "plugins"))
    world = build_world(load_registry())
    load(world, skus=[sku("SKU-30001", entity="cold_chain_item", shelf_life_days=30,
                          reorder_point=50),
                      sku("SKU-1", reorder_point=50)],
         inventory=[stock("INV-1", "SKU-30001", "WH-EAST", 12)])
    assert _ids(world.call("low_stock", {"category": "cold_chain_item"})) == ["SKU-30001"]
    outcome = world.run(OPERATOR, "inventory_lookup", {"category": "cold_chain_item"})
    assert outcome.status == "ok" and _ids(outcome.result) == ["SKU-30001"]


# ---------------------------------------------------------------- unknown categories


@pytest.mark.parametrize("category", ["widgets", "sku", "supplier"])
def test_an_unknown_category_is_a_fixable_input_problem(small, category):
    outcome = small.run(OPERATOR, "low_stock", {"category": category})
    assert outcome.status == "invalid" and outcome.result is None
    assert f"'{category}' is not a known category" in outcome.problem
    assert "packaging (Packaging)" in outcome.problem and "spare_part (Spare Part)" in \
        outcome.problem


def test_the_unknown_category_message_follows_the_request_language(small):
    outcome = small.pipeline.run(small.ctx(OPERATOR, language="zh"), "late_shipments",
                                 {"category": "widgets"})
    assert outcome.status == "invalid"
    assert outcome.problem.startswith("“widgets”不是已知的类别。")
    assert "spare_part (备件)" in outcome.problem


def test_a_direct_handler_call_with_an_unknown_category_is_an_input_problem(small):
    tool = small.registry.tool("po_status")
    with pytest.raises(InputProblem):
        tool.handler(small.ctx(OPERATOR), tool.parse_params({"category": "widgets"}))


# ---------------------------------------------------------------- confirmation summaries


def test_a_confirmation_summary_reads_as_plain_words(small):
    grant = small.run(OPERATOR, "create_po", {"sku_id": "SKU-1", "qty": 7}).grant
    assert grant.summary == (
        "Create a draft purchase order for a SKU. Without a supplier it uses the best-ranked "
        "supplier of that SKU. (SKU: SKU-1; Quantity: 7; Supplier: best-ranked for this SKU)")
    assert "None" not in grant.summary and "_id" not in grant.summary


def test_a_confirmation_summary_in_chinese(small):
    """The tool's Chinese description, then its params: no English left in the sentence."""
    outcome = small.pipeline.run(small.ctx(OPERATOR, language="zh"), "create_po",
                                 {"sku_id": "SKU-1", "qty": 7, "supplier_id": "SUP-003"})
    assert outcome.grant.summary == (
        "为某个物料新建一张草稿采购单。未指定供应商时，选用该物料排名最高的供应商。"
        "（物料编码：SKU-1；数量：7；供应商：SUP-003）")
