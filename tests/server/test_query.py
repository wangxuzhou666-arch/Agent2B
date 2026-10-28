"""POST /api/query: JSON and SSE, projection of rows, pending writes."""

import pytest

from tests.fakes import ScriptedProvider
from tests.server.conftest import Api, parse_sse

COUNT = {"query": "how many items do we have"}
WRITE = {"query": "set it", "explicit_target": "set_item_qty",
         "explicit_params": {"sku": "A-1", "qty": 12}}


def test_json_answer_comes_from_code_and_names_its_route(api):
    response = api.post("/api/query?stream=false", "vera", json=COUNT)
    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["source"] == "rule" and body["decision"]["target"] == "item_count"
    assert body["aggregates"] == {"item_count": 6}
    assert "6" in body["answer"] and body["error"] is None and body["approval"] is None
    assert [s["step"] for s in body["trace"]][:2] == ["candidates", "understand"]
    assert body["conversation_id"]


def test_conversation_id_is_echoed(api):
    body = api.post("/api/query?stream=false", "vera",
                    json={**COUNT, "conversation_id": "conv-7"}).json()
    assert body["conversation_id"] == "conv-7"


def test_stream_is_default_and_sends_trace_frames_then_one_response(api):
    with api.client.stream("POST", "/api/query", headers=api.headers("vera"), json=COUNT) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-store"
        text = "".join(r.iter_text())
    frames = parse_sse(text)
    events = [name for name, _ in frames]
    assert events[-1] == "response" and events.count("response") == 1
    assert events[:-1] and set(events[:-1]) == {"trace"}
    steps = [data["step"] for name, data in frames if name == "trace"]
    assert steps[0] == "candidates" and "execute" in steps and steps[-1] == "respond"
    final = frames[-1][1]
    assert final["aggregates"] == {"item_count": 6}
    assert [s["step"] for s in final["trace"]] == steps  # the response carries the same trace


def test_stream_false_and_stream_true_agree(api):
    streamed = parse_sse(api.post("/api/query", "vera", json=COUNT).text)[-1][1]
    plain = api.post("/api/query?stream=false", "vera", json=COUNT).json()
    for key in ("answer", "aggregates", "decision", "columns", "rows"):
        assert streamed[key] == plain[key]


def test_heartbeat_comments_keep_a_slow_stream_alive(make_runtime):
    slow = Api(make_runtime(sse_heartbeat_seconds=0.05))
    text = slow.post("/api/query", "vera", json={"query": "slow report"}).text
    assert text.count(": heartbeat") >= 2
    assert parse_sse(text)[-1][0] == "response"  # comments do not disturb the frames


def test_rows_are_projected_for_the_role(api):
    ask = {"query": "show all items"}
    operator = api.post("/api/query?stream=false", "olga", json=ask).json()
    viewer = api.post("/api/query?stream=false", "vera", json=ask).json()
    assert {"unit_cost", "supplier"} <= set(operator["rows"][0])
    assert "unit_cost" in operator["columns"]
    assert set(viewer["rows"][0]) >= {"sku", "name", "qty", "reorder_flag"}
    assert not {"unit_cost", "supplier"} & set(viewer["rows"][0])
    assert not {"unit_cost", "supplier"} & set(viewer["columns"])
    assert "Supplier" not in str(viewer["rows"]) and "0.25" not in str(viewer["rows"])
    assert "reorder_flag" in viewer["columns"]  # computed columns survive projection


def test_rows_reach_the_table_but_never_the_model(make_runtime):
    spy = ScriptedProvider()
    api = Api(make_runtime(provider=spy))
    body = api.post("/api/query?stream=false", "olga", json={"query": "show all items"}).json()
    assert body["rows"] and body["decision"]["target"] == "item_list"
    sent = spy.payloads()
    assert "Hex bolt" not in sent and "Supplier Beta" not in sent  # rows
    assert "Supplier Alpha" not in sent  # restricted values are also scrubbed from queries


def test_restricted_values_in_the_question_are_redacted_before_the_model(make_runtime):
    spy = ScriptedProvider()
    api = Api(make_runtime(provider=spy))
    api.post("/api/query?stream=false", "olga",
             json={"query": "how many items from Supplier Alpha"})
    assert "Supplier Alpha" not in spy.payloads() and "[REDACTED]" in spy.payloads()


def test_a_write_waits_for_confirmation_and_never_exposes_the_token(api):
    response = api.post("/api/query?stream=false", "olga", json=WRITE)
    body = response.json()
    assert body["approval"]["grant_id"] and body["answer"].startswith("Confirmation needed")
    assert api.runtime.store.get("item", "A-1")["qty"] == 40  # nothing ran yet
    with api.runtime.database.read() as conn:
        token = conn.execute("SELECT token FROM pending_request").fetchone()[0]
    assert token and token not in response.text
    assert "token" not in body["approval"]


def test_a_write_over_the_stream_carries_the_approval_in_the_response_frame(api):
    text = api.post("/api/query", "olga", json=WRITE).text
    frames = parse_sse(text)
    final = frames[-1][1]
    assert frames[-1][0] == "response" and final["approval"]["grant_id"]
    with api.runtime.database.read() as conn:
        token = conn.execute("SELECT token FROM pending_request").fetchone()[0]
    assert token not in text and all("token" not in str(data) for _, data in frames)


def test_viewer_cannot_reach_a_write_tool(api):
    body = api.post("/api/query?stream=false", "vera", json=WRITE).json()
    assert body["approval"] is None and "not available" in body["answer"]
    assert api.runtime.grants.pending() == []


def test_empty_and_oversize_questions(make_runtime):
    api = Api(make_runtime(max_body_bytes=64 * 1024))
    empty = api.post("/api/query?stream=false", "vera", json={"query": "  "}).json()
    assert empty["error"] == "The query is empty."
    huge = api.post("/api/query?stream=false", "vera", json={"query": "x" * 10_001})
    assert huge.status_code == 422 and huge.json()["error"]["code"] == "validation_error"


def test_validation_error_does_not_echo_the_input(api):
    response = api.post("/api/query?stream=false", "vera",
                        json={"query": "q", "explicit_params": "secret-value-123"})
    assert response.status_code == 422 and "secret-value-123" not in response.text


def test_failure_mid_stream_becomes_an_error_frame(api, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom with secret-row-value")
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr("twotrack.server.routes.query.stream_query", broken)
    text = api.post("/api/query", "vera", json=COUNT).text
    frames = parse_sse(text)
    assert [n for n, _ in frames] == ["error"]
    assert frames[0][1]["error"]["code"] == "query_failed"
    assert "secret-row-value" not in text


def test_failure_without_streaming_is_a_500_envelope(runtime, monkeypatch):
    from fastapi.testclient import TestClient

    from twotrack.server.app import create_app

    def broken(*args, **kwargs):
        raise RuntimeError("boom with secret-row-value")

    monkeypatch.setattr("twotrack.server.routes.query.run_query", broken)
    client = TestClient(create_app(runtime), raise_server_exceptions=False)
    token, _ = runtime.tokens.issue("vera")
    response = client.post("/api/query?stream=false", json=COUNT,
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error",
                                         "message": "The server could not complete the request."}}
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("stream", ["true", "false"])
def test_query_is_audited_once(api, stream):
    api.post(f"/api/query?stream={stream}", "vera", json=COUNT)
    entries = api.runtime.audit.list().items
    assert [(e.user_id, e.target, e.status.value, e.route_source) for e in entries] == [
        ("vera", "item_count", "ok", "rule")]


async def test_a_client_that_leaves_stops_the_worker():
    import asyncio
    import threading

    from twotrack.server.sse import relay

    closed = threading.Event()

    def slow_items():
        try:
            for i in range(100):
                yield i
                threading.Event().wait(0.02)
        finally:
            closed.set()

    stream = relay(slow_items, lambda i: f"{i}".encode(), heartbeat_seconds=5)
    assert await anext(stream) == b"0"
    await stream.aclose()  # what Starlette does when the client disconnects
    assert await asyncio.to_thread(closed.wait, 2)


async def test_a_frame_that_cannot_be_rendered_becomes_an_error_frame():
    from twotrack.server.sse import relay

    def render(item):
        raise ValueError("projection broke on secret-row-value")

    frames = [chunk async for chunk in relay(lambda: iter([1]), render, heartbeat_seconds=5)]
    assert len(frames) == 1 and frames[0].startswith(b"event: error")
    assert b"secret-row-value" not in frames[0]
