"""Routing sanity with the mock provider (it always abstains): the rule arm and the BM25
fallback must carry English and Chinese queries to the right target with the right params."""

import pytest
from world import OPERATOR, VIEWER

from twotrack.core import RouteKind, RouteSource
from twotrack.core.routing import decide_route
from twotrack.plugins.supply_chain import extract
from twotrack.providers.mock import MockProvider

ENGLISH = [
    ("how much stock do we have of SKU-20017", "inventory_lookup", {"sku_id": "SKU-20017"}),
    ("inventory in WH-WEST", "inventory_lookup", {"warehouse": "WH-WEST"}),
    ("show stock levels in the east warehouse", "inventory_lookup", {"warehouse": "WH-EAST"}),
    ("are we running low on anything", "low_stock", {}),
    ("which class B items are below reorder point", "low_stock", {"criticality": "B"}),
    ("what are our stockouts", "low_stock", {}),
    ("how many should I reorder of SKU-10003", "reorder_suggestion", {"sku_id": "SKU-10003"}),
    ("what quantity should we replenish", "reorder_suggestion", {}),
    ("order status of PO-20101", "po_status", {"po_id": "PO-20101"}),
    ("list draft purchase orders", "po_status", {"status": "draft"}),
    ("show me purchase orders for SKU-10042", "po_status", {"sku_id": "SKU-10042"}),
    ("which purchase orders are overdue", "late_shipments", {}),
    ("any delayed deliveries from SUP-002", "late_shipments", {"supplier_id": "SUP-002"}),
    ("deliveries past their ETA", "late_shipments", {}),
    ("rank suppliers for SKU-10010", "supplier_compare", {"sku_id": "SKU-10010"}),
    ("which vendor should we use for SKU-20005", "supplier_compare", {"sku_id": "SKU-20005"}),
    ("plan purchases for everything that is low", "reorder_plan", {}),
    ("procurement plan for class A", "reorder_plan", {"criticality": "A"}),
]

CHINESE = [
    ("SKU-20003 现有库存", "inventory_lookup", {"sku_id": "SKU-20003"}),
    ("缺货的物料有哪些", "low_stock", {}),
    ("采购单 PO-20020 的状态", "po_status", {"po_id": "PO-20020"}),
    ("哪些订单延迟了", "late_shipments", {}),
    ("比较SKU-10042的供应商", "supplier_compare", {"sku_id": "SKU-10042"}),
    ("帮我做一个补货计划", "reorder_plan", {}),
]

# Each names the write by one phrase and gives every param it requires (a SKU and a quantity):
# a complete request, so with no model it goes straight to the write, whose pipeline then holds
# it for its one-time approval; that approval is the confirmation
COMPLETE_WRITE_PHRASES = [
    ("place an order for 30 units of SKU-10019", {"sku_id": "SKU-10019", "qty": 30}),
    ("create a PO for SKU-20011, 500 units", {"sku_id": "SKU-20011", "qty": 500}),
    ("raise a PO for 1,200 pcs of SKU-10007 from SUP-003",
     {"sku_id": "SKU-10007", "qty": 1200, "supplier_id": "SUP-003"}),
    ("创建采购单 SKU-10042 数量 100", {"sku_id": "SKU-10042", "qty": 100}),
]

# The same phrases without a required param: weak evidence with no model, so routing offers
# the write as a one-option confirmation (what it did extract kept for the one click) before
# the pipeline's own grant
WRITE_PHRASES = [
    ("place an order for 30 units", {"qty": 30}),
    ("create a PO for SKU-20011", {"sku_id": "SKU-20011"}),
    ("raise a PO for SKU-10007 from SUP-003", {"sku_id": "SKU-10007", "supplier_id": "SUP-003"}),
    ("创建采购单 数量 100", {"qty": 100}),
]


def _route(world, query, principal=OPERATOR):
    return decide_route(query, world.ctx(principal), world.registry, MockProvider())


@pytest.mark.parametrize(("query", "target", "params"), ENGLISH + CHINESE)
def test_query_routes_to_expected_target(seeded, query, target, params):
    decision = _route(seeded, query)
    assert decision.target == target, decision.reason
    assert decision.source in (RouteSource.RULE, RouteSource.SEMANTIC)
    expected_kind = RouteKind.WORKFLOW if target == "reorder_plan" else RouteKind.TOOL
    assert decision.kind is expected_kind
    assert {k: decision.params.get(k) for k in params} == params


def test_a_bare_create_never_wins_the_write(seeded):
    """A bare verb is no evidence of a write: create_po declares only phrases that name it, so
    a read workflow the question names runs, and a verb with no such phrase never runs the
    write (at most BM25 offers it, as a confirmation)."""
    for query in ("create a reorder plan", "新建一个补货计划", "创建一份采购计划"):
        decision = _route(seeded, query)
        assert (decision.kind, decision.target) == (RouteKind.WORKFLOW, "reorder_plan"), query
    for query in ("create one for SKU-10042, 50 units", "raise it to 50 units",
                  "新建 SKU-10042 数量 50", "创建一个"):
        decision = _route(seeded, query)
        assert not (decision.kind is RouteKind.TOOL and decision.target == "create_po"), query
        assert decision.source is not RouteSource.RULE or "create_po" not in \
            decision.clarify_options, query


@pytest.mark.parametrize(("query", "params"), COMPLETE_WRITE_PHRASES)
def test_a_complete_write_request_goes_straight_to_the_write(seeded, query, params):
    decision = _route(seeded, query)
    assert (decision.kind, decision.target, decision.source) == (
        RouteKind.TOOL, "create_po", RouteSource.RULE)
    assert decision.params == params


@pytest.mark.parametrize(("query", "params"), WRITE_PHRASES)
def test_weak_write_match_becomes_a_confirmation(seeded, query, params):
    decision = _route(seeded, query)
    assert decision.kind is RouteKind.CLARIFY
    assert decision.clarify_options == ["create_po"]
    assert decision.params == params


def test_viewer_is_never_routed_to_the_write_tool(seeded):
    for query in ("create a PO for SKU-20011, 500 units", "创建采购单 SKU-10042 数量 100"):
        decision = _route(seeded, query, VIEWER)
        assert decision.target != "create_po"
        assert "create_po" not in decision.clarify_options


@pytest.mark.parametrize(("query", "expected"), [
    ("查询SKU-10042库存", {"sku_id": "SKU-10042"}),
    ("sku-77 in wh-central", {"sku_id": "SKU-77", "warehouse": "WH-CENTRAL"}),
    ("西仓还有多少", {"warehouse": "WH-WEST"}),
    ("PO-20015 from SUP-007", {"po_id": "PO-20015", "supplier_id": "SUP-007"}),
    ("order 2,500 units", {"qty": 2500}),
    ("下单200件", {"qty": 200}),
    ("quantity: 40", {"qty": 40}),
    ("A类物料", {"criticality": "A"}),
    ("tier c only", {"criticality": "C"}),
    ("cancelled orders", {"status": "cancelled"}),
    ("已发货的采购单", {"status": "shipped"}),
    ("what is a class of item", {}),  # an article is not a criticality
    ("SKU-10042", {"sku_id": "SKU-10042"}),  # its digits are not a quantity
])
def test_extractors(query, expected):
    names = ("sku_id", "po_id", "supplier_id", "warehouse", "qty", "criticality", "status")
    assert extract.extractor(*names)(query) == expected
