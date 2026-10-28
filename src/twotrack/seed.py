"""Synthetic demo data for the built-in supply-chain plugins.

Everything here is invented: item names are generic catalog words, suppliers are "Supplier
<greek letter> <suffix>" with ``example.com`` addresses. Output is a pure function of
(``rng_seed``, ``scale``, ``today``), so tests can pin all three.

At scale 1.0: 60 spare parts + 60 packaging SKUs, one inventory row per SKU and warehouse,
12 suppliers, 300 purchase orders ordered over the last 120 days. Two things are guaranteed
so a demo always has something to show:

* low stock: about 8% of SKUs (at least one class-A item per category) end with
  ``available + open_po_qty < reorder_point``;
* late orders: at least ``max(3, 10 x scale)`` open/shipped orders have an ETA before today.

``python -m twotrack.seed --db <path>`` loads the registry (honouring TWOTRACK_PLUGIN_PATHS),
ensures the schema and seeds an empty database.
"""

import argparse
import json
import math
import random
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from twotrack.plugins.supply_chain.entities import INCOMING_STATUSES as INCOMING
from twotrack.plugins.supply_chain.entities import LATE_STATUSES, WAREHOUSES
from twotrack.plugins.supply_chain.logic import FIRST_PO_NUMBER

REQUIRED_ENTITIES = ("sku", "spare_part", "packaging", "inventory", "supplier",
                     "purchase_order")
HISTORY_DAYS = 120
SPARE_FIRST_ID = 10001
PACKAGING_FIRST_ID = 20001

GREEK = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota",
         "Kappa", "Lambda", "Omicron", "Sigma", "Tau", "Upsilon", "Omega")
SUPPLIER_SUFFIXES = ("Ltd", "Co", "Inc", "Trading", "Industrial", "Supply")
REGIONS = ("North", "South", "East", "West", "Central", "Overseas")

PART_TYPES = (
    ("Ball Bearing", "ea"), ("Drive Belt", "ea"), ("Hydraulic Seal Kit", "kit"),
    ("Proximity Sensor", "ea"), ("Contactor", "ea"), ("Cartridge Fuse", "box"),
    ("Gear Motor", "ea"), ("Air Filter Element", "ea"), ("Flexible Coupling", "ea"),
    ("Solenoid Valve", "ea"), ("Limit Switch", "ea"), ("Rotary Encoder", "ea"),
    ("Pneumatic Cylinder", "ea"), ("Control Relay", "ea"), ("O-Ring Kit", "kit"),
    ("Thermocouple", "ea"), ("Timing Pulley", "ea"), ("Linear Guide Block", "ea"),
    ("Pressure Gauge", "ea"), ("Circuit Breaker", "ea"),
)
PART_GRADES = ("S", "M", "L", "HD")
MACHINE_PREFIXES = ("CNV", "PRS", "PKR", "MXR", "FLR", "CMP", "LBL", "WRP")
MACHINE_SIZES = (100, 120, 200, 240, 300, 450, 600)

PACK_TYPES = (
    ("Corrugated Box", "cardboard", "ea"), ("Shipping Carton", "cardboard", "ea"),
    ("Divider Insert", "cardboard", "pack"), ("Kraft Paper Roll", "paper", "roll"),
    ("Paper Void Fill", "paper", "case"), ("Paper Bag", "paper", "pack"),
    ("Stretch Film", "plastic", "roll"), ("Poly Mailer", "plastic", "pack"),
    ("Bubble Wrap Roll", "plastic", "roll"), ("Shrink Sleeve", "plastic", "roll"),
    ("Packing Tape", "plastic", "roll"), ("Plastic Tray", "plastic", "ea"),
    ("Steel Strapping", "metal", "coil"), ("Metal Tin", "metal", "ea"),
    ("Strapping Seal", "metal", "box"),
)
PACK_SIZES = ("Small", "Medium", "Large", "XL")


def utc_today() -> date:
    return datetime.now(UTC).date()


# ---------------------------------------------------------------- generation (pure)


def _log_uniform(rng: random.Random, low: float, high: float) -> float:
    return math.exp(rng.uniform(math.log(low), math.log(high)))


def _reorder_point(rng: random.Random, demand: float, lead: int) -> int:
    return max(2, math.ceil(demand * lead * rng.uniform(0.8, 1.3)))


def _suppliers(rng: random.Random, count: int) -> list[dict[str, Any]]:
    out = []
    for i in range(count):
        letter = GREEK[i % len(GREEK)]
        batch = i // len(GREEK)
        tag = f"{letter} {batch + 1}" if batch else letter
        handle = tag.lower().replace(" ", "")
        out.append({
            "supplier_id": f"SUP-{i + 1:03d}",
            "name": f"Supplier {tag} {SUPPLIER_SUFFIXES[i % len(SUPPLIER_SUFFIXES)]}",
            "region": rng.choice(REGIONS),
            "rating": round(rng.uniform(2.5, 5.0), 1),
            "avg_lead_time_days": rng.randint(3, 35),
            "contact_email": f"orders.{handle}@example.com",
            "contract_discount_pct": round(rng.uniform(0.0, 12.0), 1),
        })
    return out


def _spare_parts(rng: random.Random, count: int) -> list[dict[str, Any]]:
    out = []
    for i in range(count):
        kind, unit = PART_TYPES[i % len(PART_TYPES)]
        demand = round(rng.uniform(0.2, 6.0), 2)
        lead = rng.randint(7, 42)
        out.append({
            "sku_id": f"SKU-{SPARE_FIRST_ID + i}",
            "name": f"{kind} {rng.choice(PART_GRADES)}-{rng.randint(10, 99)}",
            "unit": unit,
            "unit_cost": round(_log_uniform(rng, 4.0, 650.0), 2),
            "avg_daily_demand": demand,
            "reorder_point": _reorder_point(rng, demand, lead),
            "lead_time_days": lead,
            "criticality": rng.choices(("A", "B", "C"), weights=(2, 3, 5))[0],
            "machine_model": f"{rng.choice(MACHINE_PREFIXES)}-{rng.choice(MACHINE_SIZES)}",
            "interchangeable_with": None,
        })
    for part in out:  # a quarter have a substitute among the other spare parts
        if len(out) > 1 and rng.random() < 0.25:
            other = rng.choice([p for p in out if p is not part])
            part["interchangeable_with"] = other["sku_id"]
    return out


def _dimensions(rng: random.Random) -> str:
    return "x".join(str(rng.randrange(50, 650, 10)) for _ in range(3))


def _packaging(rng: random.Random, count: int) -> list[dict[str, Any]]:
    out = []
    for i in range(count):
        kind, material, unit = PACK_TYPES[i % len(PACK_TYPES)]
        demand = round(rng.uniform(15.0, 400.0), 1)
        lead = rng.randint(3, 21)
        recyclable = rng.random() < (0.35 if material == "plastic" else 0.95)
        out.append({
            "sku_id": f"SKU-{PACKAGING_FIRST_ID + i}",
            "name": f"{kind} {rng.choice(PACK_SIZES)}",
            "unit": unit,
            "unit_cost": round(_log_uniform(rng, 0.05, 25.0), 2),
            "avg_daily_demand": demand,
            "reorder_point": _reorder_point(rng, demand, lead),
            "lead_time_days": lead,
            "criticality": rng.choices(("A", "B", "C"), weights=(1, 3, 6))[0],
            "material": material,
            "dimensions_mm": _dimensions(rng),
            "recyclable": recyclable,
        })
    return out


def _pick_low(rng: random.Random, items: list[dict[str, Any]], count: int) -> list[str]:
    """``count`` SKU ids, starting with a class-A item (one is promoted if none exists)."""
    class_a = [s["sku_id"] for s in items if s["criticality"] == "A"]
    if not class_a:
        promoted = rng.choice(items)
        promoted["criticality"] = "A"
        class_a = [promoted["sku_id"]]
    chosen = [rng.choice(class_a)]
    rest = [s["sku_id"] for s in items if s["sku_id"] not in chosen]
    chosen += rng.sample(rest, min(len(rest), max(0, count - len(chosen))))
    return chosen


def _sources(rng: random.Random, items: list[dict[str, Any]],
             pool: list[str]) -> dict[str, list[str]]:
    return {s["sku_id"]: rng.sample(pool, min(len(pool), rng.randint(1, 3))) for s in items}


def _order_qty(rng: random.Random, sku: dict[str, Any], packaging: bool) -> int:
    raw = sku["avg_daily_demand"] * rng.uniform(5.0, 20.0 if packaging else 40.0)
    return max(50, round(raw / 50) * 50) if packaging else max(1, round(raw))


def _purchase_orders(rng: random.Random, count: int, skus: list[dict[str, Any]],
                     packaging_ids: set[str], sources: dict[str, list[str]],
                     suppliers: dict[str, dict[str, Any]], today: date) -> list[dict[str, Any]]:
    drafts = []
    for _ in range(count):
        sku = rng.choice(skus)
        supplier = suppliers[rng.choice(sources[sku["sku_id"]])]
        ordered = today - timedelta(days=rng.randint(0, HISTORY_DAYS))
        eta = ordered + timedelta(days=max(1, supplier["avg_lead_time_days"]
                                           + rng.randint(-2, 4)))
        roll = rng.random()
        if eta < today:
            # the longer ago the ETA, the likelier the order has arrived
            past = (today - eta).days
            late = 0.3 if past <= 14 else 0.1 if past <= 45 else 0.02
            status = ("cancelled" if roll < 0.05 else "shipped" if roll < 0.05 + late * 0.6
                      else "open" if roll < 0.05 + late else "received")
        else:
            status = ("draft" if roll < 0.12 else "open" if roll < 0.55
                      else "shipped" if roll < 0.93 else "cancelled")
        drafts.append({
            "sku_id": sku["sku_id"], "supplier_id": supplier["supplier_id"],
            "qty": _order_qty(rng, sku, sku["sku_id"] in packaging_ids),
            "unit_price": round(sku["unit_cost"] * rng.uniform(0.92, 1.12), 2),
            "status": status, "ordered_at": ordered.isoformat(), "eta": eta.isoformat(),
        })
    drafts.sort(key=lambda po: po["ordered_at"])  # stable: ties keep generation order
    return [{"po_id": f"PO-{FIRST_PO_NUMBER + i}", **po} for i, po in enumerate(drafts)]


def _is_late(po: dict[str, Any], today: date) -> bool:
    return po["status"] in LATE_STATUSES and date.fromisoformat(po["eta"]) < today


def _cap_incoming(pos: list[dict[str, Any]], sku: dict[str, Any], today: date) -> int:
    """Close the largest incoming POs of ``sku`` until incoming <= reorder_point // 2."""
    cap = sku["reorder_point"] // 2
    mine = [po for po in pos if po["sku_id"] == sku["sku_id"] and po["status"] in INCOMING]
    mine.sort(key=lambda po: (-po["qty"], po["po_id"]))
    incoming = sum(po["qty"] for po in mine)
    for po in mine:
        if incoming <= cap:
            break
        past = date.fromisoformat(po["eta"]) < today
        po["status"] = "received" if past else "cancelled"
        incoming -= po["qty"]
    return incoming


def _ensure_late(rng: random.Random, pos: list[dict[str, Any]], low: set[str], today: date,
                 minimum: int) -> None:
    missing = minimum - sum(1 for po in pos if _is_late(po, today))
    if missing <= 0:
        return
    candidates = [po for po in pos if po["status"] == "received" and po["sku_id"] not in low
                  and date.fromisoformat(po["eta"]) < today]
    for po in rng.sample(candidates, min(missing, len(candidates))):
        po["status"] = rng.choice(("open", "shipped"))


def _inventory(rng: random.Random, skus: list[dict[str, Any]], low: dict[str, int],
               today: date) -> list[dict[str, Any]]:
    """One row per SKU and warehouse. SKUs in ``low`` (sku_id -> incoming qty) get stock that
    keeps ``available + incoming`` below the reorder point."""
    rows: list[dict[str, Any]] = []
    for sku in skus:
        weights = [rng.random() if rng.random() > 0.2 else 0.0 for _ in WAREHOUSES]
        if not any(weights):
            weights[rng.randrange(len(WAREHOUSES))] = 1.0
        total = sum(weights)
        rop = sku["reorder_point"]
        if sku["sku_id"] in low:
            target = rng.randint(0, max(0, rop - low[sku["sku_id"]] - 1))
        else:
            target = round(rop * rng.uniform(0.85, 3.2))
        shares = [math.floor(target * w / total) for w in weights]
        shares[max(range(len(shares)), key=lambda i: weights[i])] += target - sum(shares)
        for warehouse, share in zip(WAREHOUSES, shares, strict=True):
            reserved = rng.randint(0, share // 4) if sku["sku_id"] in low \
                else round(share * rng.uniform(0.0, 0.15))
            on_hand = share + reserved if sku["sku_id"] in low else share
            rows.append({"id": f"INV-{len(rows) + 1:05d}", "sku_id": sku["sku_id"],
                         "warehouse": warehouse, "on_hand": on_hand, "reserved": reserved,
                         "as_of": today.isoformat()})
    return rows


def generate(*, rng_seed: int = 42, scale: float = 1.0,
             today: date | None = None) -> dict[str, list[dict[str, Any]]]:
    """All demo rows keyed by entity id, in insert order. Pure and deterministic."""
    if not scale > 0:
        raise ValueError("scale must be positive")
    today = today or utc_today()
    rng = random.Random(rng_seed)
    n_items = max(4, round(60 * scale))
    suppliers = _suppliers(rng, max(4, round(12 * scale)))
    spare = _spare_parts(rng, n_items)
    packaging = _packaging(rng, n_items)
    skus = spare + packaging

    supplier_ids = [s["supplier_id"] for s in suppliers]
    half = len(supplier_ids) // 2
    # spare-part and packaging suppliers overlap by one generalist
    sources = {**_sources(rng, spare, supplier_ids[:half + 1]),
               **_sources(rng, packaging, supplier_ids[half:])}
    per_category = max(2, round(0.04 * len(skus)))
    low_ids = _pick_low(rng, spare, per_category) + _pick_low(rng, packaging, per_category)

    pos = _purchase_orders(rng, max(20, round(300 * scale)), skus,
                           {s["sku_id"] for s in packaging}, sources,
                           {s["supplier_id"]: s for s in suppliers}, today)
    by_id = {s["sku_id"]: s for s in skus}
    incoming = {sid: _cap_incoming(pos, by_id[sid], today) for sid in low_ids}
    _ensure_late(rng, pos, set(low_ids), today, max(3, round(10 * scale)))

    return {
        "supplier": suppliers,
        "spare_part": spare,
        "packaging": packaging,
        "inventory": _inventory(rng, skus, incoming, today),
        "purchase_order": pos,
    }


# ---------------------------------------------------------------- store


def has_data(store: Any, registry: Any) -> bool:
    roots = [e.id for e in registry.entities.values() if e.extends is None]
    return any(store.count(entity_id) > 0 for entity_id in roots)


def seed_database(store: Any, registry: Any, *, rng_seed: int = 42, scale: float = 1.0,
                  today: date | None = None) -> dict[str, int]:
    """Seed an empty store; returns rows written per entity id.

    Idempotent: if any root entity already has rows, nothing is written and ``{}`` is
    returned. The store's schema must already be ensured. All rows go in one transaction.
    """
    missing = [e for e in REQUIRED_ENTITIES if e not in registry.entities]
    if missing:
        raise ValueError(f"the registry lacks the built-in entities {missing}")
    if has_data(store, registry):
        return {}
    data = generate(rng_seed=rng_seed, scale=scale, today=today)
    with store.transaction():
        if has_data(store, registry):  # another seeder won the race
            return {}
        return {entity_id: store.upsert_many(entity_id, rows) for entity_id, rows in data.items()}


# ---------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    from twotrack.core import load_registry
    from twotrack.core.database import Database
    from twotrack.core.errors import RegistryError
    from twotrack.core.store import SqliteStore, StoreError

    parser = argparse.ArgumentParser(prog="python -m twotrack.seed",
                                     description="Seed an empty database with demo data.")
    parser.add_argument("--db", required=True, help="SQLite database file")
    parser.add_argument("--seed", "--rng-seed", dest="seed", type=int, default=42,
                        help="random seed (default 42)")
    parser.add_argument("--scale", type=float, default=1.0, help="size multiplier (default 1)")
    parser.add_argument("--today", type=date.fromisoformat, default=None,
                        help="anchor date YYYY-MM-DD (default: today, UTC)")
    args = parser.parse_args(argv)
    try:
        registry = load_registry()
        db = Database(args.db)
        try:
            store = SqliteStore(db)
            store.ensure_schema(registry)
            counts = seed_database(store, registry, rng_seed=args.seed, scale=args.scale,
                                   today=args.today)
        finally:
            db.close()
    except (RegistryError, StoreError, ValueError) as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1
    if not counts:
        print("The database already has data; nothing was seeded.")
    else:
        print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
