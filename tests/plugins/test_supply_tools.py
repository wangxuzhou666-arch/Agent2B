"""Tool math on the handcrafted store (expected numbers are derived in conftest.py)."""

import math

import pytest
from world import ADMIN, OPERATOR, TODAY, VIEWER, build_world

from twotrack.plugins.supply_chain import logic


def _ids(result):
    return [r["sku_id"] for r in result.rows]


# ---------------------------------------------------------------- formula (pure)


@pytest.mark.parametrize(("fields", "expected"), [
    ({"avg_daily_demand": 2.0, "lead_time_days": 10, "reorder_point": 30, "on_hand": 15,
      "reserved": 2, "open_po_qty": 10}, 27),
    ({"avg_daily_demand": 0.1, "lead_time_days": 30, "reorder_point": 0}, 3),  # not 4
    ({"avg_daily_demand": 1.1, "lead_time_days": 3, "reorder_point": 0}, 4),  # 3.3 -> 4
    ({"avg_daily_demand": 1.0, "lead_time_days": 5, "reorder_point": 10, "on_hand": 40}, 0),
    ({"reorder_point": 1, "on_hand": 3, "reserved": 5, "open_po_qty": 2}, 1),
    ({"avg_daily_demand": 3.0, "lead_time_days": 7}, 21),  # no reorder point counts as 0
])
def test_suggested_qty_formula(fields, expected):
    position = logic.StockPosition(sku_id="SKU-X", **fields)
    raw = (position.avg_daily_demand * position.lead_time_days
           + (position.reorder_point or 0) - position.available - position.open_po_qty)
    assert logic.suggested_qty(position) == expected == max(0, math.ceil(round(raw, 6)))


def test_positions_count_only_incoming_statuses_and_sum_warehouses(small):
    positions = {p.sku_id: p for p in logic.stock_positions(small.store)}
    one = positions["SKU-1"]
    assert (one.on_hand, one.reserved, one.available, one.open_po_qty) == (15, 2, 13, 10)
    assert positions["SKU-3"].available == 0  # no inventory rows at all
    assert positions["SKU-5"].available == -2  # reserved above on hand is not clamped
    assert positions["SKU-1"].category == "spare_part"
    assert positions["SKU-2"].category == "packaging"


# ---------------------------------------------------------------- reads


def test_inventory_lookup_by_sku_and_by_warehouse(small):
    result = small.call("inventory_lookup", {"sku_id": "SKU-1"})
    assert [(r["warehouse"], r["available"]) for r in result.rows] == [("WH-EAST", 8),
                                                                        ("WH-WEST", 5)]
    assert result.aggregates == {"total_on_hand": 15, "total_reserved": 2,
                                 "total_available": 13, "sku_count": 1}
    east = small.call("inventory_lookup", {"warehouse": "wh-east"})
    assert _ids(east) == ["SKU-1", "SKU-4", "SKU-6"]
    assert east.aggregates["total_on_hand"] == 11 and east.provenance.scope == \
        "Warehouse: WH-EAST"
    assert small.call("inventory_lookup", {"sku_id": "SKU-404"}).rows == []


def test_low_stock_edge_cases(small):
    result = small.call("low_stock")
    # SKU-2 and SKU-6 sit exactly at the reorder point; SKU-7 has none; SKU-8's is 0
    assert _ids(result) == ["SKU-1", "SKU-5", "SKU-4", "SKU-3"]
    assert result.aggregates == {"low_stock_count": 4, "class_a_count": 2, "total_shortfall": 16}
    assert [r["shortfall"] for r in result.rows] == [7, 1, 3, 5]
    assert _ids(small.call("low_stock", {"criticality": "a"})) == ["SKU-1", "SKU-5"]


def test_low_stock_with_nothing_low_says_so():
    empty = build_world()
    result = empty.call("low_stock")
    assert result.rows == [] and result.aggregates["low_stock_count"] == 0
    assert result.message


def test_reorder_suggestion_for_all_low_skus(small):
    result = small.call("reorder_suggestion")
    assert {r["sku_id"]: r["suggested_qty"] for r in result.rows} == \
        {"SKU-1": 27, "SKU-5": 1, "SKU-4": 6, "SKU-3": 6}
    by_id = {r["sku_id"]: r for r in result.rows}
    assert by_id["SKU-3"]["estimated_cost"] is None  # no unit cost on file
    assert result.aggregates == {"sku_count": 4, "skus_to_order": 4, "total_suggested_qty": 40,
                                 "estimated_cost": 127.9}  # 67.5 + 0.4 + 60


def test_reorder_suggestion_for_one_sku_even_when_not_low(small):
    result = small.call("reorder_suggestion", {"sku_id": "SKU-2"})
    assert result.rows[0]["suggested_qty"] == 6  # ceil(1.5 x 4 + 20 - 20 - 0)
    assert result.aggregates["total_suggested_qty"] == 6
    assert small.call("reorder_suggestion", {"sku_id": "SKU-8"}).rows[0]["suggested_qty"] == 3
    missing = small.call("reorder_suggestion", {"sku_id": "SKU-404"})
    assert missing.rows == [] and "SKU-404" in missing.message


def test_po_status_counts_by_status(small):
    result = small.call("po_status")
    assert result.aggregates == {"po_count": 7, "total_qty": 172, "draft_count": 1,
                                 "open_count": 2, "shipped_count": 2, "received_count": 1,
                                 "cancelled_count": 1}
    by_sku = small.call("po_status", {"sku_id": "SKU-1", "status": "OPEN"})
    assert [r["po_id"] for r in by_sku.rows] == ["PO-1"]
    assert small.call("po_status", {"po_id": "PO-6"}).rows[0]["status"] == "open"
    assert small.call("po_status", {"supplier_id": "SUP-003"}).aggregates["po_count"] == 2


def test_late_shipments_use_the_clock(small):
    result = small.call("late_shipments")
    # PO-3 is a draft, PO-6 is due today, PO-4 was received: none of them is late
    assert [(r["po_id"], r["days_late"]) for r in result.rows] == [("PO-1", 10), ("PO-2", 1)]
    assert result.aggregates == {"late_count": 2, "oldest_days_late": 10, "late_qty": 8,
                                 "supplier_count": 2}
    assert result.rows[0]["supplier_name"] == "Supplier Alpha Ltd"
    assert small.call("late_shipments", {"supplier_id": "SUP-002"}).aggregates == {
        "late_count": 1, "oldest_days_late": 1, "late_qty": 3, "supplier_count": 1}


def test_supplier_compare_ranks_by_rating_then_lead_time(small):
    result = small.call("supplier_compare", {"sku_id": "SKU-1"})
    # SUP-003 never supplied SKU-1; SUP-004 has no rating so it ranks last
    assert [r["supplier_id"] for r in result.rows] == ["SUP-002", "SUP-001", "SUP-004"]
    assert [r["po_count"] for r in result.rows] == [2, 2, 1]
    assert result.rows[0]["last_unit_price"] == 2.6  # PO-2 is SUP-002's latest priced order
    assert result.rows[1]["last_unit_price"] == 2.4  # PO-5 has no price
    assert result.aggregates == {"supplier_count": 3, "top_rating": 4.5,
                                 "top_lead_time_days": 10}
    overall = small.call("supplier_compare")
    assert [r["supplier_id"] for r in overall.rows] == ["SUP-003", "SUP-002", "SUP-001",
                                                         "SUP-004"]
    fallback = small.call("supplier_compare", {"sku_id": "SKU-2"})
    assert len(fallback.rows) == 4 and "SKU-2" in fallback.message


@pytest.mark.parametrize("principal", [VIEWER, OPERATOR])
def test_restricted_supplier_fields_never_reach_non_admin_rows(small, principal):
    for params in ({}, {"sku_id": "SKU-1"}):
        result = small.call("supplier_compare", params, principal=principal)
        assert result.rows
        for row in result.rows:
            assert "contact_email" not in row and "contract_discount_pct" not in row
        assert "contact_email" not in result.columns
        assert "@" not in repr(result.model_dump())


def test_admin_sees_restricted_supplier_fields(small):
    result = small.call("supplier_compare", {"sku_id": "SKU-1"}, principal=ADMIN)
    assert result.rows[0]["contact_email"] == "orders.beta@example.com"
    assert result.rows[0]["contract_discount_pct"] == 7.5
    assert {"contact_email", "contract_discount_pct"} <= set(result.columns)
    # restricted values never become narratable numbers
    assert 7.5 not in result.aggregates.values()


def test_read_tool_runs_through_the_pipeline_for_a_viewer(small):
    outcome = small.run(VIEWER, "low_stock")
    assert outcome.status == "ok" and outcome.result.aggregates["low_stock_count"] == 4


def test_preconditions_block_tools_on_an_empty_store():
    empty = build_world()
    outcome = empty.run(OPERATOR, "inventory_lookup")
    assert outcome.status == "blocked" and "inventory" in outcome.problem.lower()
    names = set(empty.registry.candidates(empty.ctx(OPERATOR)).names())
    assert names == {"po_status", "late_shipments"}


# ---------------------------------------------------------------- write


def test_create_po_needs_a_grant_then_inserts_a_draft(small):
    first = small.run(OPERATOR, "create_po", {"sku_id": "SKU-1", "qty": "7"})
    assert first.status == "needs_confirmation" and first.grant is not None
    assert small.store.get("purchase_order", "PO-8") is None  # nothing written yet

    done = small.run(OPERATOR, "create_po", {"sku_id": "SKU-1", "qty": 7},
                     grant_token=first.grant.token)
    assert done.status == "ok", done.problem
    created = small.store.get("purchase_order", "PO-8")
    assert created == {"po_id": "PO-8", "sku_id": "SKU-1", "supplier_id": "SUP-002", "qty": 7,
                       "unit_price": 2.6, "status": "draft",
                       "ordered_at": TODAY.isoformat(), "eta": "2026-10-11",
                       "_entity": "purchase_order"}
    assert done.result.aggregates == {"qty": 7, "lead_time_days": 10, "order_value": 18.2}

    # the draft counts as incoming: SKU-1 is no longer low (13 + 10 + 7 = 30)
    assert "SKU-1" not in _ids(small.call("low_stock"))
    replay = small.run(OPERATOR, "create_po", {"sku_id": "SKU-1", "qty": 7},
                       grant_token=first.grant.token)
    assert replay.status == "blocked"
    assert small.store.count("purchase_order") == 8


def test_create_po_without_history_uses_best_supplier_and_list_cost(small):
    grant = small.run(ADMIN, "create_po", {"sku_id": "SKU-2", "qty": 40}).grant
    done = small.run(ADMIN, "create_po", {"sku_id": "SKU-2", "qty": 40},
                     grant_token=grant.token)
    row = done.result.rows[0]
    assert (row["supplier_id"], row["unit_price"], row["eta"]) == ("SUP-003", 1.0, "2026-10-31")


def test_create_po_with_explicit_supplier(small):
    params = {"sku_id": "SKU-1", "qty": 5, "supplier_id": "SUP-001"}
    grant = small.run(OPERATOR, "create_po", params).grant
    done = small.run(OPERATOR, "create_po", params, grant_token=grant.token)
    assert done.result.rows[0]["supplier_id"] == "SUP-001"
    assert done.result.rows[0]["unit_price"] == 2.4


def test_viewer_cannot_create_po(small):
    outcome = small.run(VIEWER, "create_po", {"sku_id": "SKU-1", "qty": 5})
    assert outcome.status == "blocked" and outcome.grant is None
    assert "create_po" not in small.registry.candidates(small.ctx(VIEWER))
    assert small.store.count("purchase_order") == 7


@pytest.mark.parametrize(("params", "status"), [
    ({"sku_id": "SKU-1", "qty": 0}, "invalid"),
    ({"sku_id": "SKU-1"}, "invalid"),
    ({"sku_id": "SKU-1", "qty": 5, "warehouse": "WH-EAST"}, "invalid"),  # unknown key
])
def test_create_po_rejects_bad_params_before_any_grant(small, params, status):
    outcome = small.run(OPERATOR, "create_po", params)
    assert outcome.status == status and outcome.grant is None


@pytest.mark.parametrize(("params", "needle"), [
    ({"sku_id": "SKU-404", "qty": 5}, "SKU-404"),
    ({"sku_id": "SKU-1", "qty": 5, "supplier_id": "SUP-999"}, "SUP-999"),
])
def test_create_po_unknown_references_are_user_fixable(small, params, needle):
    grant = small.run(OPERATOR, "create_po", params).grant
    outcome = small.run(OPERATOR, "create_po", params, grant_token=grant.token)
    assert outcome.status == "invalid" and needle in outcome.problem
    assert small.store.count("purchase_order") == 7


def test_rows_match_columns_and_survive_entity_projection(seeded):
    """Rows of an entity-bound tool use that entity's fields or undeclared computed keys, so a
    projection keyed on ``Tool.entity`` cannot silently drop a column."""
    registry = seeded.registry
    declared = {f.id for e in registry.entities.values() for f in e.all_fields}
    for name, tool in registry.tools.items():
        if name == "create_po":
            continue
        result = seeded.call(name, principal=ADMIN)
        assert result.rows, name
        keys = {k for row in result.rows for k in row}
        assert keys == set(result.columns), name
        own = {f.id for f in registry.entity(tool.entity).all_fields}
        assert not {k for k in keys if k in declared and k not in own}, name
