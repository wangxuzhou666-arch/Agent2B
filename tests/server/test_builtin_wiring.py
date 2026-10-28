"""The server on the built-in supply-chain plugins: real registry, real seed data.

The other server tests use a fixture plugin so they do not move when the example changes.
These few exist because the built-in tools have preconditions that read the store
(`has_inventory_data`, `has_suppliers`): a request context without a store would make the
registry fail them closed and hide the tools, and only the real plugins prove the wiring end
to end.
"""

import pytest

from tests.server.conftest import PASSWORD, Api
from twotrack.providers import MockProvider
from twotrack.runtime import build_runtime
from twotrack.settings import Settings

LOW_STOCK = {"query": "which SKUs are low on stock"}  # one of low_stock's declared examples
GATED_BY_INVENTORY = {"inventory_lookup", "low_stock", "reorder_suggestion"}
GATED_BY_SUPPLIERS = {"supplier_compare", "create_po"}


@pytest.fixture
def empty(tmp_path):
    runtime = build_runtime(Settings(data_dir=tmp_path / "builtin"), provider=MockProvider())
    for name, role in (("olga", "operator"), ("vera", "viewer"), ("ada", "admin")):
        runtime.users.add(name, PASSWORD, [role])
    yield Api(runtime)
    runtime.close()


def seed(runtime):
    from twotrack.seed import seed_database

    return seed_database(runtime.store, runtime.registry)


def names(body):
    step = next(s for s in body["trace"] if s["step"] == "candidates")
    return set(step["facts"]["names"])


def test_gated_tools_are_hidden_until_data_exists_then_routable(empty):
    def ask():  # an operator may reach every gated tool, `create_po` included
        return empty.post("/api/query?stream=false", "olga", json=LOW_STOCK).json()

    before = ask()
    assert not (GATED_BY_INVENTORY | GATED_BY_SUPPLIERS) & names(before)
    assert before["decision"]["target"] != "low_stock"
    assert not [s for s in before["trace"] if s["status"] == "error"]  # hidden, not crashed

    assert seed(empty.runtime)  # data arrives
    after = ask()
    assert (GATED_BY_INVENTORY | GATED_BY_SUPPLIERS) <= names(after)
    assert after["decision"]["target"] == "low_stock" and after["decision"]["source"] == "rule"
    assert after["error"] is None and after["provenance"]["tool"] == "low_stock"
    assert not [s for s in after["trace"] if s["status"] == "error"]
    assert after["rows"] and "sku_id" in after["rows"][0]


def test_the_same_holds_over_the_event_stream(empty):
    from tests.server.conftest import parse_sse

    seed(empty.runtime)
    frames = parse_sse(empty.post("/api/query", "vera", json=LOW_STOCK).text)
    assert frames[-1][0] == "response" and frames[-1][1]["decision"]["target"] == "low_stock"
    assert not [d for name, d in frames if name == "trace" and d["status"] == "error"]


def test_an_explicit_call_is_told_what_is_missing_rather_than_silently_refused(empty):
    body = empty.post("/api/query?stream=false", "olga",
                      json={"query": "x", "explicit_target": "inventory_lookup"}).json()
    assert "No inventory data yet" in body["answer"]
    seed(empty.runtime)
    ok = empty.post("/api/query?stream=false", "olga",
                    json={"query": "x", "explicit_target": "inventory_lookup"}).json()
    assert ok["error"] is None and ok["rows"] and ok["provenance"]["tool"] == "inventory_lookup"


def test_a_gated_write_is_confirmed_and_replayed_with_the_context_intact(empty):
    seed(empty.runtime)
    runtime = empty.runtime
    sku = runtime.store.list("sku", sort="sku_id", limit=1).rows[0]["sku_id"]
    before = runtime.store.count("purchase_order")
    ask = {"query": "x", "explicit_target": "create_po",
           "explicit_params": {"sku_id": sku, "qty": 5}}
    waiting = empty.post("/api/query?stream=false", "olga", json=ask).json()
    assert waiting["approval"] and runtime.store.count("purchase_order") == before
    done = empty.post(f"/api/approvals/{waiting['approval']['grant_id']}/approve", "olga").json()
    assert done["status"] == "ok" and done["target"] == "create_po"
    assert runtime.store.count("purchase_order") == before + 1
    assert done["rows"] and done["rows"][0]["sku_id"] == sku


def test_admin_only_supplier_fields_stay_with_admins(empty):
    seed(empty.runtime)
    ask = {"query": "x", "explicit_target": "supplier_compare"}
    viewer = empty.post("/api/query?stream=false", "vera", json=ask).json()
    admin = empty.post("/api/query?stream=false", "ada", json=ask).json()
    restricted = {"contact_email", "contract_discount_pct"}
    assert viewer["rows"] and not restricted & set(viewer["rows"][0])
    assert not restricted & set(viewer["columns"])
    assert restricted <= set(admin["rows"][0]) and restricted <= set(admin["columns"])
    records = empty.get("/api/entities/supplier/records", "vera").json()["items"]
    assert records and not restricted & set(records[0])


def test_the_servers_own_demo_seeding_loads_the_built_in_data(tmp_path):
    runtime = build_runtime(Settings(data_dir=tmp_path / "demo", demo_mode=True),
                            provider=MockProvider())
    try:
        assert runtime.store.count("sku") > 0 and runtime.store.count("inventory") > 0
        assert runtime.store.count("supplier") > 0
        api = Api(runtime)
        token = api.client.post("/api/auth/demo", json={"role": "viewer"}).json()
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        body = api.client.post("/api/query?stream=false", headers=headers,
                               json=LOW_STOCK).json()
        assert body["decision"]["target"] == "low_stock" and body["error"] is None
    finally:
        runtime.close()
