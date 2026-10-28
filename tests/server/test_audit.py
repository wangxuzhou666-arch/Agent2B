"""Audit endpoints: role gate, filters, pagination, replayable detail and the stats chart."""

from datetime import UTC, datetime, timedelta

import pytest

COUNT = {"query": "how many items do we have"}


def populate(api):
    api.post("/api/query?stream=false", "vera", json=COUNT)
    api.post("/api/query?stream=false", "olga", json=COUNT)
    api.post("/api/query?stream=false", "olga",
             json={"query": "x", "explicit_target": "item_list"})
    write = {"query": "x", "explicit_target": "set_item_qty",
             "explicit_params": {"sku": "A-1", "qty": 9}}
    grant = api.post("/api/query?stream=false", "olga", json=write).json()["approval"]["grant_id"]
    api.post(f"/api/approvals/{grant}/approve", "olga")


@pytest.mark.parametrize("path", ["/api/audit", "/api/audit/stats", "/api/audit/1"])
def test_viewers_cannot_read_the_audit_log(api, path):
    response = api.get(path, "vera")
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("user", ["olga", "ada"])
def test_operators_and_admins_can_read_it(api, user):
    populate(api)
    page = api.get("/api/audit", user).json()
    assert page["total"] == 6 and len(page["items"]) == 6 and page["page"] == 1
    assert "trace" not in page["items"][0]  # the list stays light
    assert page["items"][0]["id"] > page["items"][-1]["id"]  # newest first


def test_filters(api):
    populate(api)
    by = lambda **q: api.get("/api/audit", "ada", params=q).json()  # noqa: E731
    assert by(user="vera")["total"] == 1
    assert by(target="set_item_qty")["total"] == 3
    assert by(status="needs_confirmation")["total"] == 1
    assert by(route_source="rule")["total"] == 2
    assert by(route_source="explicit", status="ok")["total"] == 2
    assert by(since=(datetime.now(UTC) + timedelta(hours=1)).isoformat())["total"] == 0
    assert by(until=(datetime.now(UTC) - timedelta(hours=1)).isoformat())["total"] == 0
    assert by(since=(datetime.now(UTC) - timedelta(hours=1)).isoformat())["total"] == 6
    assert api.get("/api/audit", "ada", params={"status": "bogus"}).status_code == 422


def test_naive_timestamps_are_read_as_utc(api):
    populate(api)
    naive = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    assert api.get("/api/audit", "ada", params={"since": naive}).json()["total"] == 6


def test_pagination(api):
    populate(api)
    first = api.get("/api/audit", "ada", params={"page_size": 4}).json()
    second = api.get("/api/audit", "ada", params={"page_size": 4, "page": 2}).json()
    assert (len(first["items"]), len(second["items"]), first["total"]) == (4, 2, 6)
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert api.get("/api/audit", "ada", params={"page_size": 500}).status_code == 422
    assert api.get("/api/audit", "ada", params={"page": 0}).status_code == 422


def test_detail_carries_the_trace_for_replay(api):
    populate(api)
    entry = api.get("/api/audit", "ada", params={"user": "vera"}).json()["items"][0]
    detail = api.get(f"/api/audit/{entry['id']}", "olga").json()
    assert detail["target"] == "item_count" and detail["route_source"] == "rule"
    # the whole decision ladder: routing, the pipeline's gate and execute, then narration
    assert [s["step"] for s in detail["trace"]] == [
        "candidates", "understand", "gate", "execute", "narrate", "respond"]
    understand = detail["trace"][1]
    assert understand["source"] == "rule" and understand["facts"]["target"] == "item_count"
    assert detail["trace"][4]["facts"]["source"] in ("model", "template")
    assert detail["params_digest"] and detail["problem"] is None and detail["request_id"]


def test_unknown_entry_is_404_and_stats_is_not_shadowed_by_the_id_route(api):
    assert api.get("/api/audit/9999", "ada").status_code == 404
    assert api.get("/api/audit/stats", "ada").status_code == 200


def test_stats_count_runs_by_route_source_and_outcome(api):
    populate(api)
    stats = api.get("/api/audit/stats", "ada").json()
    assert stats["by_route_source"] == {"rule": 2, "explicit": 3}
    assert stats["by_status"] == {"ok": 4, "needs_confirmation": 1}  # `started` not counted
    assert stats["total"] == 5
    assert {"route_source": "rule", "status": "ok", "count": 2} in stats["buckets"]
    assert {"route_source": "explicit", "status": "needs_confirmation", "count": 1} in \
        stats["buckets"]


def test_stats_agree_with_the_listing(api):
    populate(api)
    listed = api.get("/api/audit", "ada", params={"page_size": 200}).json()["items"]
    runs = [e for e in listed if e["status"] != "started"]
    stats = api.get("/api/audit/stats", "ada").json()
    assert stats["total"] == len(runs)


def test_stats_window(api):
    populate(api)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    empty = api.get("/api/audit/stats", "ada", params={"since": future}).json()
    assert empty["total"] == 0 and empty["by_route_source"] == {} and empty["buckets"] == []
    past = api.get("/api/audit/stats", "ada",
                   params={"since": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
                           "until": (datetime.now(UTC) + timedelta(days=1)).isoformat()}).json()
    assert past["total"] == 5


def test_unrouted_runs_are_keyed_none(api):
    from twotrack.core import Principal, ToolContext

    ctx = ToolContext(principal=Principal(user_id="olga", roles=frozenset({"operator"})),
                      store=None)
    api.runtime.pipeline.run(ctx, "item_count", {})  # no route_source: a direct pipeline call
    stats = api.get("/api/audit/stats", "ada").json()
    assert stats["by_route_source"] == {"none": 1}
    assert [(b["route_source"], b["count"]) for b in stats["buckets"]] == [("none", 1)]


def test_the_none_bucket_and_the_none_filter_agree(api):
    """A write refused at the approval step has no route source: the stats bar's 'none'
    bucket and the list it links to (?route_source=none) count the same records."""
    write = {"query": "x", "explicit_target": "set_item_qty",
             "explicit_params": {"sku": "A-1", "qty": 9}}
    grant = api.post("/api/query?stream=false", "olga", json=write).json()["approval"]["grant_id"]
    assert api.post(f"/api/approvals/{grant}/approve", "omar").status_code == 403
    api.post("/api/query?stream=false", "olga", json=COUNT)  # routed: never in the bucket
    stats = api.get("/api/audit/stats", "ada").json()
    listed = api.get("/api/audit", "ada", params={"route_source": "none"}).json()
    assert stats["by_route_source"]["none"] == listed["total"] == 1
    assert sum(b["count"] for b in stats["buckets"] if b["route_source"] == "none") == 1
    (refusal,) = listed["items"]
    assert (refusal["route_source"], refusal["status"], refusal["problem"]) == (
        None, "blocked", "not_requester")
    assert refusal["request_id"] and refusal["duration_ms"] == 0.0  # one gate step, untimed
    routed = api.get("/api/audit", "ada", params={"route_source": "rule"}).json()["total"]
    assert routed == stats["by_route_source"]["rule"]


def test_duration_is_the_sum_of_the_trace_steps(api):
    populate(api)
    page = api.get("/api/audit", "ada").json()
    for item in page["items"]:
        detail = api.get(f"/api/audit/{item['id']}", "ada").json()
        steps = [s["duration_ms"] for s in detail["trace"]]
        expected = round(sum(steps), 3) if steps else None
        assert item["duration_ms"] == detail["duration_ms"] == expected
    assert any(item["duration_ms"] for item in page["items"])
