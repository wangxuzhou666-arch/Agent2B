"""The audit log replays every query's whole decision ladder, including queries that never
reach the pipeline, and links an approved write back to the request that asked for it."""

import logging

from tests.fakes import ScriptedProvider
from tests.server.conftest import Api
from twotrack.core.audit import AuditQuery
from twotrack.providers.base import AgentStep, LLMRoute, ToolCall

COUNT = {"query": "how many items do we have"}
CHAT = {"query": "write a short poem about the sea"}
WRITE = {"query": "set it", "explicit_target": "set_item_qty",
         "explicit_params": {"sku": "A-1", "qty": 12}}


def ask(api, body, user="olga"):
    response = api.post("/api/query?stream=false", user, json=body)
    assert response.status_code == 200
    return response.json()


def detail(api, entry_id, user="ada"):
    return api.get(f"/api/audit/{entry_id}", user).json()


def steps(entry):
    return [s["step"] for s in entry["trace"]]


def test_a_deterministic_query_replays_its_whole_ladder(api):
    body = ask(api, COUNT, "vera")
    assert body["audit_ids"]
    entry = detail(api, body["audit_ids"][-1])
    assert steps(entry) == ["candidates", "understand", "gate", "execute", "narrate", "respond"]
    understand = entry["trace"][1]
    assert understand["source"] == "rule" and understand["facts"]["kind"] == "tool"
    assert entry["trace"][4]["facts"]["source"] in ("model", "template")
    assert entry["status"] == "ok" and entry["target"] == "item_count"
    # the live answer and the record carry the same steps
    assert steps(entry) == [s["step"] for s in body["trace"]]


def test_the_stream_leaves_the_same_record(api):
    from tests.server.conftest import parse_sse

    frames = parse_sse(api.post("/api/query", "vera", json=COUNT).text)
    final = frames[-1][1]
    assert frames[-1][0] == "response" and final["audit_ids"]
    assert steps(detail(api, final["audit_ids"][-1]))[-2:] == ["narrate", "respond"]


def test_a_chat_fallback_leaves_a_record_with_no_target(api):
    body = ask(api, CHAT)
    assert body["decision"]["kind"] == "chat" and body["audit_ids"]
    entry = detail(api, body["audit_ids"][-1])
    assert entry["status"] == "chat" and entry["target"] is None
    assert entry["route_source"] == "fallback" and entry["request_id"]
    assert steps(entry) == ["candidates", "understand", "narrate", "respond"]


def test_a_clarification_leaves_a_record_with_the_options(make_runtime):
    provider = ScriptedProvider(route=LLMRoute(target="item_count", confidence=0.85))
    api = Api(make_runtime(provider=provider))
    body = ask(api, {"query": "anything new from the stockroom"})
    assert body["decision"]["kind"] == "clarify"
    entry = detail(api, body["audit_ids"][-1])
    assert entry["status"] == "clarify" and entry["route_source"] == "llm_confirm"
    assert entry["target"] == "item_count" and entry["effect"] == "read"
    understand = next(s for s in entry["trace"] if s["step"] == "understand")
    assert understand["facts"]["options"] == ["item_count"]


def test_an_explicit_target_outside_the_candidates_is_recorded_as_blocked(api):
    body = ask(api, WRITE, "vera")
    entry = detail(api, body["audit_ids"][-1])
    assert entry["status"] == "blocked" and entry["target"] == "set_item_qty"
    assert "not available" in entry["problem"]
    assert steps(entry) == ["candidates", "gate", "understand", "respond"]


def test_stats_cover_every_routing_decision(api):
    ask(api, COUNT, "vera")
    ask(api, CHAT)
    ask(api, CHAT, "vera")
    stats = api.get("/api/audit/stats", "ada").json()
    assert stats["by_status"] == {"ok": 1, "chat": 2}
    # a chat fallback is its own source: it must not inflate the BM25 ("semantic") share
    assert stats["by_route_source"] == {"rule": 1, "fallback": 2}
    assert {"route_source": "fallback", "status": "chat", "count": 2} in stats["buckets"]
    assert "semantic" not in stats["by_route_source"]


def test_an_approval_links_execution_back_to_its_request(api):
    asked = ask(api, WRITE)
    grant_id = asked["approval"]["grant_id"]
    request = detail(api, asked["audit_ids"][-1])
    assert request["status"] == "needs_confirmation" and request["grant_id"] == grant_id
    assert steps(request) == ["candidates", "understand", "gate", "respond"]

    done = api.post(f"/api/approvals/{grant_id}/approve", "olga").json()
    assert done["status"] == "ok"
    execution = detail(api, done["audit_ids"][-1])
    assert execution["parent_id"] == request["id"]
    assert execution["request_id"] == request["request_id"]
    assert steps(execution) == ["gate", "execute", "narrate", "respond"]

    chain = api.get("/api/audit", "ada", params={"request_id": request["request_id"]}).json()
    assert [(e["status"], e["parent_id"]) for e in reversed(chain["items"])] == [
        ("needs_confirmation", None), ("started", request["id"]), ("ok", request["id"])]
    by_grant = api.get("/api/audit", "ada", params={"grant_id": grant_id}).json()
    assert by_grant["total"] == 3


def test_an_import_commit_links_back_to_its_preview(api):
    from tests.server.test_import import commit, upload

    preview = upload(api, "olga").json()
    done = commit(api, "olga", preview, grant=True).json()
    assert done["status"] == "ok"
    records = list(reversed(api.runtime.audit.list(AuditQuery(target="import:item")).items))
    asked, *ran = records
    assert asked.status.value == "needs_confirmation"
    assert [(e.status.value, e.parent_id) for e in ran] == [("started", asked.id),
                                                           ("ok", asked.id)]


def test_a_rejection_links_back_too(api):
    asked = ask(api, WRITE)
    grant_id = asked["approval"]["grant_id"]
    api.post(f"/api/approvals/{grant_id}/reject", "olga")
    rejection = api.runtime.audit.list(AuditQuery(status="blocked")).items[0]
    assert rejection.parent_id == asked["audit_ids"][-1] and rejection.problem == "rejected"


def test_an_open_task_run_ends_with_the_whole_ladder(make_runtime):
    calls = [AgentStep(tool_calls=[ToolCall(id="c1", name="item_count")]),
             AgentStep(text="There are 6 items.")]
    api = Api(make_runtime(provider=ScriptedProvider(steps=calls), allow_open_task=True))
    body = ask(api, {"query": "zzz qqq unmatched gibberish"})
    assert body["decision"]["kind"] == "open_task" and len(body["audit_ids"]) == 1
    entry = detail(api, body["audit_ids"][-1])
    assert entry["route_source"] == "agent" and entry["target"] == "item_count"
    names = steps(entry)
    assert names[:2] == ["candidates", "understand"] and names[-2:] == ["narrate", "respond"]
    assert names.count("gate") == 1


def test_a_failed_append_keeps_the_answer_and_says_so(api, monkeypatch, caplog):
    def broken(entry_id, steps):
        raise OSError("disk full")

    monkeypatch.setattr(api.runtime.audit, "append_trace", broken)
    caplog.set_level(logging.WARNING, logger="twotrack.graph")
    body = ask(api, COUNT, "vera")
    assert body["error"] is None and "6" in body["answer"]
    assert "could not be attached" in caplog.text
    # the run itself was audited before the answer existed
    assert steps(detail(api, body["audit_ids"][-1])) == [
        "candidates", "understand", "gate", "execute"]


def test_a_failed_decision_record_keeps_the_answer_and_says_so(api, monkeypatch, caplog):
    def broken(entry):
        raise OSError("disk full")

    monkeypatch.setattr(api.runtime.audit, "record", broken)
    caplog.set_level(logging.WARNING, logger="twotrack.graph")
    body = ask(api, CHAT)
    assert body["answer"] and body["audit_ids"] == []
    assert "could not be recorded" in caplog.text
