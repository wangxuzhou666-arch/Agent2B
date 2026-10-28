"""seed_database: deterministic, idempotent, and always leaves something worth showing."""

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from twotrack.core import Principal, ToolContext, load_registry
from twotrack.core.database import Database
from twotrack.core.store import ReadOnlyStore, SqliteStore
from twotrack.plugins.supply_chain import logic
from twotrack.seed import generate, main, seed_database

TODAY = date(2026, 10, 1)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry():
    return load_registry(extra_paths=[])


def _store(registry, path=":memory:"):
    store = SqliteStore(Database(path))
    store.ensure_schema(registry)
    return store


def _dump(store, registry):
    roots = [e.id for e in registry.entities.values() if e.extends is None]
    return {r: logic.fetch_all(store, r) for r in roots}


def _late(orders, today):
    return [po for po in orders
            if po["status"] in ("open", "shipped") and date.fromisoformat(po["eta"]) < today]


def test_scale_one_shape(registry):
    counts = seed_database(_store(registry), registry, today=TODAY)
    assert counts == {"supplier": 12, "spare_part": 60, "packaging": 60, "inventory": 360,
                      "purchase_order": 300}


def test_same_seed_same_data_different_seed_different_data(registry):
    a, b = _store(registry), _store(registry)
    seed_database(a, registry, rng_seed=7, today=TODAY)
    seed_database(b, registry, rng_seed=7, today=TODAY)
    assert _dump(a, registry) == _dump(b, registry)
    assert generate(rng_seed=7, today=TODAY) == generate(rng_seed=7, today=TODAY)
    assert generate(rng_seed=7, today=TODAY) != generate(rng_seed=8, today=TODAY)


def test_idempotent(registry):
    store = _store(registry)
    assert seed_database(store, registry, today=TODAY)
    before = _dump(store, registry)
    assert seed_database(store, registry, today=TODAY) == {}
    assert seed_database(store, registry, rng_seed=99, today=TODAY) == {}
    assert _dump(store, registry) == before


def test_any_existing_data_blocks_seeding(registry):
    store = _store(registry)
    store.upsert_many("supplier", [{"supplier_id": "SUP-900", "name": "Supplier Own Ltd"}])
    assert seed_database(store, registry, today=TODAY) == {}
    assert store.count("purchase_order") == 0


@pytest.mark.parametrize("rng_seed", [1, 7, 42, 2026])
@pytest.mark.parametrize("scale", [0.1, 1.0, 2.0])
def test_guaranteed_low_stock_and_late_orders(registry, rng_seed, scale):
    store = _store(registry)
    seed_database(store, registry, rng_seed=rng_seed, scale=scale, today=TODAY)
    positions = logic.stock_positions(store)
    low = logic.low_stock_positions(positions)
    for category in ("spare_part", "packaging"):
        mine = [p for p in low if p.category == category]
        assert len(mine) >= 2
        assert any(p.criticality == "A" for p in mine)
    assert len(low) < len(positions) / 2  # most SKUs are healthy
    late = _late(logic.fetch_all(store, "purchase_order"), TODAY)
    assert len(late) >= max(3, round(10 * scale))


def test_dates_are_anchored_and_values_are_plausible(registry):
    data = generate(today=TODAY)
    orders = data["purchase_order"]
    assert min(date.fromisoformat(po["ordered_at"]) for po in orders) >= \
        TODAY - timedelta(days=120)
    assert max(date.fromisoformat(po["ordered_at"]) for po in orders) <= TODAY
    assert all(po["eta"] > po["ordered_at"] for po in orders)
    assert set(Counter(po["status"] for po in orders)) == {"draft", "open", "shipped",
                                                          "received", "cancelled"}
    assert [po["po_id"] for po in orders] == [f"PO-{20001 + i}" for i in range(300)]
    assert {i["as_of"] for i in data["inventory"]} == {TODAY.isoformat()}
    assert all(i["on_hand"] >= 0 and i["reserved"] >= 0 for i in data["inventory"])
    suppliers = data["supplier"]
    assert all(s["contact_email"].endswith("@example.com") for s in suppliers)
    assert all(s["name"].startswith("Supplier ") for s in suppliers)
    assert all(1.0 <= s["rating"] <= 5.0 for s in suppliers)
    known = {s["sku_id"] for s in data["spare_part"] + data["packaging"]}
    assert {po["sku_id"] for po in orders} <= known
    assert {i["sku_id"] for i in data["inventory"]} == known


def test_seeded_store_makes_the_demo_tools_non_empty(registry):
    store = _store(registry)
    seed_database(store, registry, today=TODAY)
    ctx = ToolContext(principal=Principal(user_id="v", roles=frozenset({"viewer"})),
                      store=ReadOnlyStore(store),
                      clock=lambda: __import__("datetime").datetime(2026, 10, 1, 9))
    for name in ("low_stock", "reorder_suggestion", "late_shipments", "supplier_compare"):
        tool = registry.tool(name)
        assert tool.handler(ctx, tool.parse_params({})).rows, name
    assert set(registry.candidates(ctx).names()) == {*registry.tools, *registry.workflows} - {
        "create_po"}  # everything but the write is reachable for a viewer


def test_seed_requires_the_builtin_entities(registry):
    from twotrack.core import build_registry
    from twotrack.plugins.spare_parts import PLUGIN

    partial = build_registry([PLUGIN.__class__(name="only", version="1")])
    with pytest.raises(ValueError, match="lacks the built-in entities"):
        seed_database(_store(registry), partial)


def test_cli_seeds_once(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TWOTRACK_PLUGIN_PATHS", raising=False)
    db = tmp_path / "demo.db"
    assert main(["--db", str(db), "--today", "2026-10-01", "--scale", "0.5"]) == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts["purchase_order"] == 150 and counts["supplier"] == 6
    assert main(["--db", str(db), "--rng-seed", "7"]) == 0
    assert "nothing was seeded" in capsys.readouterr().out


def test_python_dash_m_entry_point(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "TWOTRACK_PLUGIN_PATHS"}
    done = subprocess.run([sys.executable, "-m", "twotrack.seed", "--db", str(tmp_path / "x.db"),
                           "--scale", "0.1"], capture_output=True, text=True, env=env,
                          cwd=REPO, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["supplier"] == 4
