"""The request context carries the store, so preconditions that read it can pass.

A precondition that raises counts as failed (the registry fails closed), which would hide the
tools behind it without any visible error. These tests make that failure loud.
"""

from tests.server.conftest import Api
from twotrack.core import Principal, ToolContext
from twotrack.core.store import ReadOnlyStore

AUDIT = {"query": "run an item audit"}
WRITE = {"query": "set it", "explicit_target": "set_item_qty",
         "explicit_params": {"sku": "A-1", "qty": 12}}


def candidates_step(body):
    return next(s for s in body["trace"] if s["step"] == "candidates")


def test_a_store_reading_precondition_gates_a_tool_through_the_query_endpoint(make_runtime):
    runtime = make_runtime(seed=False)
    api = Api(runtime)
    before = api.post("/api/query?stream=false", "vera", json=AUDIT).json()
    assert "item_audit" not in candidates_step(before)["facts"]["names"]
    assert before["decision"]["target"] != "item_audit"
    blocked = api.post("/api/query?stream=false", "vera",
                       json={**AUDIT, "explicit_target": "item_audit"}).json()
    assert "No items on record yet." in blocked["answer"]  # told why, not silently absent

    runtime.store.upsert_many("item", [{"sku": "Z-1", "name": "First item"}])
    after = api.post("/api/query?stream=false", "vera", json=AUDIT).json()
    assert "item_audit" in candidates_step(after)["facts"]["names"]
    assert after["decision"]["target"] == "item_audit" and after["decision"]["source"] == "rule"
    assert after["aggregates"] == {"audited": 1} and after["error"] is None


def test_no_precondition_failed_by_an_exception_on_either_path(api):
    for url in ("/api/query?stream=false", "/api/query"):
        text = api.post(url, "vera", json=AUDIT).text
        assert '"status":"error"' not in text.replace(" ", ""), url


def test_a_context_without_a_store_would_hide_the_tool(runtime):
    """The failure the server avoids, shown on purpose: with no store the precondition raises,
    the registry fails closed, and the tool silently leaves the candidate set."""
    steps = []
    bare = ToolContext(principal=Principal(user_id="vera", roles=frozenset({"viewer"})),
                       store=None, trace=steps.append)
    assert "item_audit" not in runtime.registry.candidates(bare)
    assert [s.status.value for s in steps] == ["error"]
    carrying = ToolContext(principal=bare.principal, store=ReadOnlyStore(runtime.store))
    assert "item_audit" in runtime.registry.candidates(carrying)


def test_the_query_and_the_approval_rerun_both_hand_the_pipeline_a_store(api, monkeypatch):
    seen = []
    real_run = api.runtime.pipeline.run

    def spy(ctx, target, params, **kwargs):
        seen.append((kwargs.get("grant_token") is not None, ctx.store))
        return real_run(ctx, target, params, **kwargs)

    monkeypatch.setattr(api.runtime.pipeline, "run", spy)
    grant = api.post("/api/query?stream=false", "olga", json=WRITE).json()["approval"]["grant_id"]
    api.post(f"/api/approvals/{grant}/approve", "olga")
    assert [with_grant for with_grant, _ in seen] == [False, True]  # the query, then the replay
    for _, store in seen:
        assert isinstance(store, ReadOnlyStore)
        assert store.count("item") == 6  # reads work; the context is not an empty shell
