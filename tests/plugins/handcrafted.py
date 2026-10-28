"""A small handcrafted store whose every expected number is derived here.

Handcrafted world (today = 2026-10-01). available = on_hand - reserved over warehouses;
incoming = qty on draft/open/shipped POs; low = available + incoming < reorder_point;
suggested = max(0, ceil(demand x lead + rop - available - incoming)).

    SKU    cat crit demand lead rop  available     incoming    low?       suggested
    SKU-1  sp  A    2.0    10   30   10-2+5 = 13   5+3+2 = 10  23<30 yes  ceil(20+30-13-10) = 27
    SKU-2  pk  B    1.5    4    20   20            0           20=20 no   ceil(6+20-20) = 6
    SKU-3  sp  C    0.1    3    5    0 (no rows)   0           0<5 yes    ceil(5.3) = 6
    SKU-4  sp  B    0.1    30   4    1             0           1<4 yes    ceil(3+4-1) = 6
    SKU-5  pk  A    -      -    1    3-5 = -2      2           0<1 yes    ceil(0+1+2-2) = 1
    SKU-6  pk  C    1      1    10   0             10          10=10 no
    SKU-7  sp  C    1      1    -    0             0           no rop
    SKU-8  sp  C    0.1    30   0    0             0           no         3 (not 4: float noise)

Low, most urgent first: SKU-1 (A, short 7), SKU-5 (A, short 1), SKU-4 (B, 3), SKU-3 (C, 5).
"""

from world import po, sku, stock

SUPPLIERS = [
    {"supplier_id": "SUP-001", "name": "Supplier Alpha Ltd", "region": "North", "rating": 4.5,
     "avg_lead_time_days": 20, "contact_email": "orders.alpha@example.com",
     "contract_discount_pct": 5.0},
    {"supplier_id": "SUP-002", "name": "Supplier Beta Co", "region": "East", "rating": 4.5,
     "avg_lead_time_days": 10, "contact_email": "orders.beta@example.com",
     "contract_discount_pct": 7.5},
    {"supplier_id": "SUP-003", "name": "Supplier Gamma Inc", "region": "West", "rating": 5.0,
     "avg_lead_time_days": 30, "contact_email": "orders.gamma@example.com",
     "contract_discount_pct": 2.0},
    {"supplier_id": "SUP-004", "name": "Supplier Delta Trading", "region": "South",
     "rating": None, "avg_lead_time_days": 5, "contact_email": "orders.delta@example.com",
     "contract_discount_pct": 0.0},
]

SKUS = [
    sku("SKU-1", criticality="A", unit_cost=2.5, avg_daily_demand=2.0, lead_time_days=10,
        reorder_point=30),
    sku("SKU-2", entity="packaging", criticality="B", unit_cost=1.0, avg_daily_demand=1.5,
        lead_time_days=4, reorder_point=20),
    sku("SKU-3", criticality="C", avg_daily_demand=0.1, lead_time_days=3, reorder_point=5),
    sku("SKU-4", criticality="B", unit_cost=10.0, avg_daily_demand=0.1, lead_time_days=30,
        reorder_point=4),
    sku("SKU-5", entity="packaging", criticality="A", unit_cost=0.4, reorder_point=1),
    sku("SKU-6", entity="packaging", criticality="C", unit_cost=1.0, avg_daily_demand=1.0,
        lead_time_days=1, reorder_point=10),
    sku("SKU-7", criticality="C", unit_cost=1.0, avg_daily_demand=1.0, lead_time_days=1),
    sku("SKU-8", criticality="C", unit_cost=1.0, avg_daily_demand=0.1, lead_time_days=30,
        reorder_point=0),
]

INVENTORY = [
    stock("INV-1", "SKU-1", "WH-EAST", 10, 2),
    stock("INV-2", "SKU-1", "WH-WEST", 5),
    stock("INV-3", "SKU-2", "WH-CENTRAL", 20),
    stock("INV-4", "SKU-4", "WH-EAST", 1),
    stock("INV-5", "SKU-5", "WH-WEST", 3, 5),
    stock("INV-6", "SKU-6", "WH-EAST", 0),
]

ORDERS = [
    po("PO-1", "SKU-1", "SUP-001", 5, "open", eta="2026-09-21", ordered_at="2026-08-20",
       unit_price=2.4),  # 10 days late
    po("PO-2", "SKU-1", "SUP-002", 3, "shipped", eta="2026-09-30", ordered_at="2026-09-10",
       unit_price=2.6),  # 1 day late
    po("PO-3", "SKU-1", "SUP-002", 2, "draft", eta="2026-09-01", ordered_at="2026-08-15",
       unit_price=2.55),  # past ETA but a draft: not late
    po("PO-4", "SKU-1", "SUP-004", 100, "received", eta="2026-08-01", ordered_at="2026-07-10",
       unit_price=2.0),
    po("PO-5", "SKU-1", "SUP-001", 50, "cancelled", eta="2026-10-15", ordered_at="2026-09-20"),
    po("PO-6", "SKU-5", "SUP-003", 2, "open", eta="2026-10-01", ordered_at="2026-09-25",
       unit_price=0.45),  # due today: not late yet
    po("PO-7", "SKU-6", "SUP-003", 10, "shipped", eta="2026-10-05", ordered_at="2026-09-28"),
]
