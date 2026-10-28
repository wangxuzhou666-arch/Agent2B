"""The drop-in example adds a third SKU category through TWOTRACK_PLUGIN_PATHS alone."""

from pathlib import Path

import pytest
from world import OPERATOR, VIEWER, build_world, builtin_registry, load, sku, stock

from twotrack.core import PLUGIN_PATHS_ENV, load_registry
from twotrack.core.store import table_name

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setenv(PLUGIN_PATHS_ENV, str(EXAMPLES))
    return load_registry()


def _sku_columns(world):
    with world.db.read() as conn:
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({table_name('sku')})")]


def test_not_loaded_without_the_plugin_path(monkeypatch):
    monkeypatch.delenv(PLUGIN_PATHS_ENV, raising=False)
    registry = load_registry()
    assert "cold_chain_item" not in registry.entities
    assert "expiring_soon" not in registry.tools


def test_plugin_path_adds_the_category_without_core_change(registry):
    assert [p.name for p in registry.plugins] == ["packaging", "spare_parts", "supply_chain",
                                                  "cold_chain", "supplier_scorecard"]
    item = registry.entity("cold_chain_item")
    assert item.lineage == ("sku", "cold_chain_item")
    assert item.fields == registry.entity("sku").fields
    assert [f.id for f in item.attrs] == ["storage_temp_c", "shelf_life_days"]
    assert item.match_header("保质期").id == "shelf_life_days"
    assert "expiring_soon" in registry.tools


def test_rows_live_in_the_sku_table_with_no_new_columns(registry):
    world = build_world(registry)
    assert _sku_columns(world) == _sku_columns(build_world(builtin_registry()))
    load(world, skus=[sku("SKU-30001", entity="cold_chain_item", criticality="A",
                          storage_temp_c=-18.0, shelf_life_days=30, reorder_point=50,
                          avg_daily_demand=4.0, lead_time_days=5)],
         inventory=[stock("INV-1", "SKU-30001", "WH-EAST", 12)])
    row = world.store.get("sku", "SKU-30001")
    assert row["_entity"] == "cold_chain_item" and row["storage_temp_c"] == -18.0
    assert world.store.count("sku") == world.store.count("cold_chain_item") == 1
    with world.db.read() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "e_cold_chain_item" not in tables

    # built-in tools treat the new category like any SKU
    low = world.call("low_stock")
    assert [(r["sku_id"], r["category"]) for r in low.rows] == [("SKU-30001", "cold_chain_item")]
    suggestion = world.call("reorder_suggestion", {"sku_id": "SKU-30001"})
    assert suggestion.rows[0]["suggested_qty"] == 58  # ceil(4 x 5 + 50 - 12 - 0)


def test_expiring_soon(registry):
    world = build_world(registry)
    load(world,
         skus=[sku("SKU-30001", entity="cold_chain_item", shelf_life_days=35),
               sku("SKU-30002", entity="cold_chain_item", shelf_life_days=365),
               sku("SKU-10001")],
         inventory=[
             {**stock("INV-1", "SKU-30001", "WH-EAST", 12), "as_of": "2026-09-01"},  # 5 days
             {**stock("INV-2", "SKU-30001", "WH-WEST", 0), "as_of": "2026-09-01"},  # empty
             stock("INV-3", "SKU-30002", "WH-EAST", 40),  # a year away
             {**stock("INV-4", "SKU-10001", "WH-EAST", 9), "as_of": "2020-01-01"},  # not cold
         ])
    outcome = world.run(VIEWER, "expiring_soon", {})
    assert outcome.status == "ok"
    assert [(r["sku_id"], r["warehouse"], r["days_left"]) for r in outcome.result.rows] == [
        ("SKU-30001", "WH-EAST", 5)]
    assert outcome.result.aggregates == {"item_count": 1, "expiring_units": 12,
                                         "soonest_days_left": 5}
    assert world.run(OPERATOR, "expiring_soon", {"within_days": 2}).result.rows == []
    assert registry.tool("expiring_soon").param_extractor("expiring in 30 days") == {
        "within_days": 30}
