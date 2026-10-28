"""Approvals: a write waits for its requester's confirmation, runs once, and is audited."""

from datetime import timedelta

from tests.fakes import ScriptedProvider
from tests.server.conftest import Api
from twotrack.core.audit import AuditQuery
from twotrack.providers.base import AgentStep, ToolCall
from twotrack.server.app import create_app

WRITE = {"query": "set it", "explicit_target": "set_item_qty",
         "explicit_params": {"sku": "A-1", "qty": 12}}


def ask_for_write(api, user="olga", **params):
    body = {**WRITE, "explicit_params": {"sku": "A-1", "qty": 12, **params}}
    return api.post("/api/query?stream=false", user, json=body).json()["approval"]["grant_id"]


def qty(api, sku="A-1"):
    return api.runtime.store.get("item", sku)["qty"]


def test_requester_approves_and_the_write_runs_once(api):
    grant_id = ask_for_write(api)
    done = api.post(f"/api/approvals/{grant_id}/approve", "olga")
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "ok" and body["kind"] == "query" and body["target"] == "set_item_qty"
    assert body["aggregates"] == {"new_qty": 12} and "12" in body["answer"]
    assert body["audit_ids"] and qty(api) == 12
    again = api.post(f"/api/approvals/{grant_id}/approve", "olga")
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"


def test_approval_appears_in_audit_with_the_original_route_source(api):
    grant_id = ask_for_write(api)
    api.post(f"/api/approvals/{grant_id}/approve", "olga")
    entries = api.runtime.audit.list(AuditQuery(target="set_item_qty")).items
    assert [e.status.value for e in reversed(entries)] == ["needs_confirmation", "started", "ok"]
    assert {e.route_source for e in entries} == {"explicit"}
    assert {e.grant_id for e in entries} == {grant_id}


def test_another_user_cannot_approve_and_nothing_runs(api):
    grant_id = ask_for_write(api, "olga")
    for intruder in ("omar", "ada", "vera"):
        response = api.post(f"/api/approvals/{grant_id}/approve", intruder)
        assert response.status_code == 403 and response.json()["error"]["code"] == "not_requester"
    assert qty(api) == 40
    refusals = api.runtime.audit.list(AuditQuery(status="blocked")).items
    assert {e.user_id for e in refusals} == {"omar", "ada", "vera"}
    assert {e.problem for e in refusals} == {"not_requester"}
    assert api.post(f"/api/approvals/{grant_id}/approve", "olga").json()["status"] == "ok"


def test_listing_shows_mine_and_admin_sees_all(api):
    mine = ask_for_write(api, "olga")
    theirs = ask_for_write(api, "omar", qty=3)
    as_olga = api.get("/api/approvals", "olga").json()["items"]
    assert [i["grant_id"] for i in as_olga] == [mine] and as_olga[0]["mine"] is True
    assert as_olga[0]["kind"] == "query" and as_olga[0]["target"] == "set_item_qty"
    assert as_olga[0]["route_source"] == "explicit" and as_olga[0]["expires_at"]
    as_admin = api.get("/api/approvals", "ada").json()["items"]
    assert {i["grant_id"] for i in as_admin} == {mine, theirs}
    assert {i["mine"] for i in as_admin} == {False}
    assert api.get("/api/approvals", "vera").json()["items"] == []
    assert "params" not in as_olga[0] and "token" not in as_olga[0]


def test_requester_can_reject_and_it_cannot_be_approved_afterwards(api):
    grant_id = ask_for_write(api)
    rejected = api.post(f"/api/approvals/{grant_id}/reject", "olga")
    assert rejected.json() == {"grant_id": grant_id, "status": "rejected"}
    assert api.get("/api/approvals", "olga").json()["items"] == []
    assert api.post(f"/api/approvals/{grant_id}/approve", "olga").status_code == 404
    assert qty(api) == 40
    assert api.runtime.grants.pending() == []  # the core grant is closed too
    entry = api.runtime.audit.list(AuditQuery(status="blocked")).items[0]
    assert (entry.user_id, entry.problem, entry.grant_id) == ("olga", "rejected", grant_id)


def test_admin_can_reject_anyones_but_another_operator_cannot(api):
    grant_id = ask_for_write(api, "olga")
    assert api.post(f"/api/approvals/{grant_id}/reject", "omar").status_code == 403
    assert api.post(f"/api/approvals/{grant_id}/reject", "vera").status_code == 403
    assert api.post(f"/api/approvals/{grant_id}/reject", "ada").status_code == 200
    entry = api.runtime.audit.list(AuditQuery(status="blocked")).items[0]
    assert entry.user_id == "ada" and entry.problem == "rejected_for:olga"


def test_expired_approval_is_gone_and_does_not_run(make_runtime):
    from tests.fakes import NOW

    class Clock:
        now = NOW

        def __call__(self):
            return self.now

    clock = Clock()
    runtime = make_runtime(clock=clock)
    api = Api(runtime)
    grant_id = ask_for_write(api)
    clock.now = NOW + timedelta(minutes=16)
    response = api.post(f"/api/approvals/{grant_id}/approve", "olga")
    assert response.status_code == 410 and response.json()["error"]["code"] == "approval_expired"
    assert qty(api) == 40 and api.get("/api/approvals", "olga").json()["items"] == []


def test_params_that_fail_validation_on_replay_do_not_write(api):
    grant_id = ask_for_write(api, qty=5)
    with api.runtime.database.transaction() as conn:
        conn.execute("UPDATE pending_request SET params = ?", ('{"sku": "A-1", "qty": -1}',))
    body = api.post(f"/api/approvals/{grant_id}/approve", "olga").json()
    assert body["status"] == "invalid" and "qty" in body["problem"]
    assert qty(api) == 40 and api.runtime.grants.pending() == []


def test_approve_with_a_role_that_lost_access_is_blocked_by_the_pipeline(api):
    grant_id = ask_for_write(api, "olga")
    with api.runtime.database.transaction() as conn:
        conn.execute("UPDATE app_user SET roles = 'viewer' WHERE user_id = 'olga'")
    body = api.post(f"/api/approvals/{grant_id}/approve", "olga").json()
    assert body["status"] == "blocked" and qty(api) == 40


def test_malformed_grant_id_is_a_validation_error(api):
    assert api.post("/api/approvals/not-a-grant/approve", "olga").status_code == 422
    assert api.post(f"/api/approvals/{'0' * 16}/approve", "olga").status_code == 404


def test_approvals_need_a_token(api):
    assert api.post(f"/api/approvals/{'0' * 16}/approve").status_code == 401
    assert api.post(f"/api/approvals/{'0' * 16}/reject").status_code == 401


def test_a_failed_recording_cancels_the_grant(api, monkeypatch):
    def broken(*args, **kwargs):
        import sqlite3

        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(api.runtime.approvals, "add_query", broken)
    body = api.post("/api/query?stream=false", "olga", json=WRITE).json()
    assert body["approval"] is None and "could not be recorded" in body["error"]
    assert api.runtime.grants.pending() == []


def test_two_simultaneous_approvals_run_the_write_once(api):
    from concurrent.futures import ThreadPoolExecutor

    grant_id = ask_for_write(api)
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: api.post(f"/api/approvals/{grant_id}/approve", "olga"),
                                range(2)))
    # whichever thread lost, and whatever the winner had got to, the other one is refused as a
    # late duplicate is, and on record (test_approval_refusals.py forces each interleaving)
    assert sorted(r.status_code for r in replies) == [200, 409]
    loser = next(r for r in replies if r.status_code != 200).json()["error"]["code"]
    assert loser == "grant_used"
    entries = api.runtime.audit.list(AuditQuery(target="set_item_qty")).items
    assert len([e for e in entries if e.status.value == "ok"]) == 1 and qty(api) == 12
    assert [(e.status.value, e.problem) for e in entries if e.status.value == "blocked"] == [
        ("blocked", "grant_used")]


def agent_proposal(make_runtime):
    """An API whose open-task agent proposes one write, and the answer to the question that
    reached it (no fixed flow covers it, so no target is routed)."""
    call = ToolCall(id="c1", name="set_item_qty", arguments={"sku": "A-1", "qty": 3})
    spy = ScriptedProvider(steps=[AgentStep(tool_calls=[call])])
    api = Api(make_runtime(provider=spy, allow_open_task=True))
    body = api.post("/api/query?stream=false", "olga",
                    json={"query": "zzz qqq unmatched gibberish"}).json()
    return api, body


def test_the_approval_of_a_routed_write_names_the_target_it_was_routed_to(api):
    body = api.post("/api/query?stream=false", "olga", json=WRITE).json()
    assert body["decision"]["target"] == "set_item_qty"
    assert body["approval"]["target"] == "set_item_qty"


def test_the_contract_makes_every_approval_name_its_target():
    schemas = create_app(None).openapi()["components"]["schemas"]
    for name in ("ApprovalRef", "ApprovalItem"):
        assert "target" in schemas[name]["required"], name


def test_the_approval_of_a_write_the_agent_planned_names_its_target(make_runtime):
    """An open task routes to no target (``decision.target`` is null): the approval carries the
    one its grant is bound to, the id the Approvals list shows, so the console can title it the
    way it titles a fixed flow's."""
    api, body = agent_proposal(make_runtime)
    assert body["decision"]["kind"] == "open_task" and body["decision"]["target"] is None
    approval = body["approval"]
    assert approval["target"] == "set_item_qty" and approval["label"]
    (listed,) = api.get("/api/approvals", "olga").json()["items"]
    assert (approval["target"], approval["label"]) == (listed["target"], listed["label"])


def test_a_write_planned_by_the_open_task_agent_is_approved_like_any_other(make_runtime):
    api, body = agent_proposal(make_runtime)
    assert body["decision"]["kind"] == "open_task" and body["approval"]["grant_id"]
    assert qty(api) == 40
    done = api.post(f"/api/approvals/{body['approval']['grant_id']}/approve", "olga").json()
    assert done["status"] == "ok" and qty(api) == 3
    sources = {e.route_source for e in api.runtime.audit.list(AuditQuery(target="set_item_qty"))
               .items}
    assert sources == {"agent"}
