"""Approving a confirmation that waits no more, and the audit trail of every refusal.

A confirmation that already ran answers 409 ``grant_used`` (it never runs twice), and so does one
that a concurrent approval has taken and is still running; an expired one 410, an id that was
never issued 404. Every refusal of a confirmation that exists or existed is an audit record under
its grant id, so ``GET /api/audit?grant_id=`` always shows why nothing ran: the approval-side
ones with one ``gate`` step (``stage`` ``approval``), the pipeline's and the importer's with their
own steps.
"""

import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.fakes import NOW
from tests.server.conftest import Api
from tests.server.test_approvals import ask_for_write, qty
from tests.server.test_import import CSV, commit, upload
from twotrack.core.i18n import say
from twotrack.server.app import create_app
from twotrack.server.routes.approvals import run_query_approval


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def timed(make_runtime, clock):
    return Api(make_runtime(clock=clock))


def approve(api, grant_id, user="olga"):
    return api.post(f"/api/approvals/{grant_id}/approve", user)


def records(api, grant_id):
    """The grant's records, oldest first, as the audit API lists them."""
    found = api.get("/api/audit", "ada", params={"grant_id": grant_id})
    assert found.status_code == 200
    return list(reversed(found.json()["items"]))


def outcomes(api, grant_id):
    return [(e["status"], e["problem"]) for e in records(api, grant_id)]


def refusal_step(api, entry_id):
    (step,) = api.get(f"/api/audit/{entry_id}", "ada").json()["trace"]
    return step["step"], step["status"], step["facts"]


# ---------------------------------------------------------------- already used: 409


def test_approving_again_after_it_ran_is_409_grant_used_and_audited(api):
    grant_id = ask_for_write(api)
    assert approve(api, grant_id).json()["status"] == "ok"
    again = approve(api, grant_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"
    assert again.json()["error"]["message"] == say("approval_used", "en")
    assert qty(api) == 12  # ran once
    assert outcomes(api, grant_id) == [("needs_confirmation", None), ("started", None),
                                       ("ok", None), ("blocked", "grant_used")]
    request, *_, refusal = records(api, grant_id)
    assert (refusal["user_id"], refusal["target"], refusal["route_source"]) == (
        "olga", "set_item_qty", None)
    assert (refusal["parent_id"], refusal["request_id"]) == (request["id"],
                                                             request["request_id"])
    assert refusal_step(api, refusal["id"]) == (
        "gate", "blocked", {"stage": "approval", "problem": "grant_used"})


def test_the_used_refusal_speaks_the_language_asked_for(api):
    grant_id = ask_for_write(api)
    approve(api, grant_id)
    zh = api.client.post(f"/api/approvals/{grant_id}/approve",
                         headers={**api.headers("olga"), "Accept-Language": "zh-CN,zh;q=0.9"})
    assert zh.status_code == 409 and zh.json()["error"]["message"] == say("approval_used", "zh")


def test_a_used_confirmation_is_409_after_the_grant_store_forgot_it(timed, clock):
    grant_id = ask_for_write(timed)
    assert approve(timed, grant_id).json()["status"] == "ok"
    clock.now = NOW + timedelta(minutes=20)
    ask_for_write(timed, qty=3)  # issuing a grant purges the expired ones
    assert timed.runtime.grants.find(grant_id) is None
    again = approve(timed, grant_id)  # the audit log still knows it ran
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"
    assert outcomes(timed, grant_id)[-1] == ("blocked", "grant_used")


def test_an_approved_import_is_409_when_approved_again(api):
    preview = upload(api, "olga").json()
    grant_id = commit(api, "olga", preview).json()["approval"]["grant_id"]
    assert approve(api, grant_id).json()["status"] == "ok"
    again = approve(api, grant_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"
    assert outcomes(api, grant_id)[-1] == ("blocked", "grant_used")


# ---------------------------------------------------------------- taken by another approval: 409
#
# Two approvals of one confirmation race for its stored request: the one whose claim lands first
# runs it, the other must hear what a late duplicate hears. Each test sends the second request
# from inside the first, at the point of the race it is about, so nothing depends on thread
# timing. (test_approvals.py runs the same race on real threads, where only the outcome is fixed.)


def before_next(monkeypatch, owner, name, action):
    """Run ``action`` just before the next call of ``owner.name``: the point in a request where
    another request can land."""
    original = getattr(owner, name)

    def interleaved(*args, **kwargs):
        monkeypatch.setattr(owner, name, original)  # once
        action()
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, interleaved)


def assert_refused_as_used(late, api, grant_id):
    assert late.status_code == 409 and late.json()["error"]["code"] == "grant_used"
    assert late.json()["error"]["message"] == say("approval_used", "en")
    request, refusal, *_ = records(api, grant_id)
    assert (refusal["user_id"], refusal["target"], refusal["status"], refusal["problem"]) == (
        "olga", "set_item_qty", "blocked", "grant_used")
    assert (refusal["parent_id"], refusal["request_id"]) == (request["id"],
                                                             request["request_id"])
    assert refusal_step(api, refusal["id"]) == (
        "gate", "blocked", {"stage": "approval", "problem": "grant_used"})


def test_an_approval_that_arrives_while_the_first_runs_is_409_grant_used_and_audited(
        api, monkeypatch):
    """The first has taken the request and not yet used the grant: the request is gone, the
    grant still open."""
    grant_id = ask_for_write(api)
    late = []
    before_next(monkeypatch, api.runtime.pipeline, "run",
                lambda: late.append(approve(api, grant_id)))
    first = approve(api, grant_id)
    assert first.status_code == 200 and first.json()["status"] == "ok"
    assert qty(api) == 12  # ran once
    assert_refused_as_used(late[0], api, grant_id)
    # the refusal came while the first was starting: between the request and the run
    assert outcomes(api, grant_id) == [("needs_confirmation", None), ("blocked", "grant_used"),
                                       ("started", None), ("ok", None)]


def test_an_approval_that_loses_the_claim_is_409_grant_used_and_audited(api, monkeypatch):
    """Both read the request; the other one's claim landed first."""
    grant_id = ask_for_write(api)
    taken = []
    before_next(monkeypatch, api.runtime.approvals, "claim", lambda: taken.append(
        api.runtime.approvals.claim(grant_id, user_id="olga", states=("awaiting",))))
    late = approve(api, grant_id)
    assert qty(api) == 40  # the loser ran nothing
    assert_refused_as_used(late, api, grant_id)
    # the winner carries on from its claim
    ran = run_query_approval(api.runtime, api.runtime.users.get("olga").principal, taken[0])
    assert ran.status == "ok" and qty(api) == 12
    assert outcomes(api, grant_id) == [("needs_confirmation", None), ("blocked", "grant_used"),
                                       ("started", None), ("ok", None)]


def test_a_second_approval_of_an_import_being_committed_is_409_grant_used_and_audited(
        api, monkeypatch):
    preview = upload(api, "olga").json()
    grant_id = commit(api, "olga", preview).json()["approval"]["grant_id"]
    second = []
    before_next(monkeypatch, api.runtime.importer, "commit",
                lambda: second.append(approve(api, grant_id)))
    first = approve(api, grant_id)
    assert first.status_code == 200 and first.json()["status"] == "ok"
    assert api.runtime.store.get("item", "N-1")["name"] == "Nut"
    late = second[0]
    assert late.status_code == 409 and late.json()["error"]["code"] == "grant_used"
    assert outcomes(api, grant_id) == [("needs_confirmation", None), ("blocked", "grant_used"),
                                       ("started", None), ("ok", None)]


def test_an_approval_that_loses_the_claim_to_a_rejection_is_404(api, monkeypatch):
    """A rejection takes the request and closes its grant in one step, so the approval that
    finds it gone finds it rejected, not used: 404, and the rejection is the record."""
    grant_id = ask_for_write(api)
    rejected = []
    before_next(monkeypatch, api.runtime.approvals, "claim", lambda: rejected.append(
        api.post(f"/api/approvals/{grant_id}/reject", "olga")))
    late = approve(api, grant_id)
    assert rejected[0].status_code == 200
    assert late.status_code == 404 and late.json()["error"]["code"] == "not_found"
    assert qty(api) == 40
    assert outcomes(api, grant_id) == [("needs_confirmation", None), ("blocked", "rejected")]


def test_a_rejection_that_cannot_close_the_grant_leaves_the_request_pending(api, monkeypatch):
    """Taking the request and closing its grant are one transaction: if the second fails the
    first is undone, so the confirmation is never half-gone (request taken, grant open)."""

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    grant_id = ask_for_write(api)
    monkeypatch.setattr(api.runtime.grants, "reject", broken)
    failing = TestClient(create_app(api.runtime), raise_server_exceptions=False)
    response = failing.post(f"/api/approvals/{grant_id}/reject", headers=api.headers("olga"))
    assert response.status_code == 500
    monkeypatch.undo()
    assert [i["grant_id"] for i in api.get("/api/approvals", "olga").json()["items"]] == [grant_id]
    assert approve(api, grant_id).json()["status"] == "ok"
    assert qty(api) == 12


# ---------------------------------------------------------------- expired: 410


def test_an_expired_confirmation_is_410_every_time_and_each_refusal_is_audited(timed, clock):
    grant_id = ask_for_write(timed)
    clock.now = NOW + timedelta(minutes=16)
    first = approve(timed, grant_id)
    assert first.status_code == 410 and first.json()["error"]["code"] == "approval_expired"
    # again: the store remembers it ran out (closing it then does not make it "rejected")
    assert timed.runtime.grants.find(grant_id).state == "expired"
    assert approve(timed, grant_id).status_code == 410
    # and once the store has purged it, the audit log still knows it was issued and never ran
    clock.now = NOW + timedelta(minutes=40)
    ask_for_write(timed, qty=3)
    assert timed.runtime.grants.find(grant_id) is None
    assert approve(timed, grant_id).status_code == 410
    assert qty(timed) == 40
    assert outcomes(timed, grant_id) == [("needs_confirmation", None)] + [
        ("blocked", "approval_expired")] * 3
    request, refusal, *_ = records(timed, grant_id)
    assert refusal["parent_id"] == request["id"] and refusal["target"] == "set_item_qty"
    assert refusal_step(timed, refusal["id"]) == (
        "gate", "blocked", {"stage": "approval", "problem": "approval_expired"})


def test_an_import_approved_after_it_expired_is_410_and_audited(timed, clock):
    preview = upload(timed, "olga").json()
    grant_id = commit(timed, "olga", preview).json()["approval"]["grant_id"]
    clock.now = NOW + timedelta(minutes=16)
    late = approve(timed, grant_id)
    assert late.status_code == 410 and late.json()["error"]["code"] == "approval_expired"
    assert timed.runtime.store.get("item", "N-1") is None
    assert outcomes(timed, grant_id) == [("needs_confirmation", None),
                                         ("blocked", "approval_expired")]


# ---------------------------------------------------------------- never issued, rejected: 404


def test_an_id_that_was_never_issued_is_404_and_leaves_no_record(api):
    unknown = "0123456789abcdef"
    response = approve(api, unknown)
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert records(api, unknown) == []


def test_a_rejected_confirmation_stays_404_and_its_rejection_says_why(api):
    grant_id = ask_for_write(api)
    api.post(f"/api/approvals/{grant_id}/reject", "olga")
    assert approve(api, grant_id).status_code == 404
    assert outcomes(api, grant_id) == [("needs_confirmation", None), ("blocked", "rejected")]


# ---------------------------------------------------------------- refused before the grant is used


def test_every_refusal_before_the_grant_is_used_is_under_its_grant_id(api):
    """Someone else's approval (the route), then a role that may no longer run it (the
    pipeline): nothing ran, and the grant's own records say why, each time."""
    grant_id = ask_for_write(api)
    assert approve(api, grant_id, "omar").status_code == 403
    with api.runtime.database.transaction() as conn:
        conn.execute("UPDATE app_user SET roles = 'viewer' WHERE user_id = 'olga'")
    assert approve(api, grant_id).json()["status"] == "blocked"
    assert qty(api) == 40
    request, intruder, pipeline = records(api, grant_id)
    assert (intruder["status"], intruder["problem"], intruder["user_id"]) == (
        "blocked", "not_requester", "omar")
    assert (pipeline["status"], pipeline["user_id"]) == ("blocked", "olga")
    assert "may not run" in pipeline["problem"]
    assert pipeline["parent_id"] == request["id"]


def test_params_that_no_longer_validate_are_refused_under_the_grant_id(api):
    grant_id = ask_for_write(api, qty=5)
    with api.runtime.database.transaction() as conn:
        conn.execute("UPDATE pending_request SET params = ?", ('{"sku": "A-1", "qty": -1}',))
    assert approve(api, grant_id).json()["status"] == "invalid"
    (_, refused) = records(api, grant_id)
    assert refused["status"] == "invalid" and "qty" in refused["problem"]


def test_approving_an_upload_that_was_never_submitted_is_audited(api):
    grant_id = upload(api, "olga").json()["grant"]["grant_id"]
    response = approve(api, grant_id)
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_submitted"
    (_, refusal) = records(api, grant_id)
    assert (refusal["status"], refusal["problem"], refusal["target"]) == (
        "blocked", "not_submitted", "import:item")


def test_an_import_refused_before_its_grant_is_used_is_under_its_grant_id(api):
    preview = upload(api, "olga").json()
    grant_id = commit(api, "olga", preview).json()["approval"]["grant_id"]
    with api.runtime.database.transaction() as conn:  # the staged file changed since
        conn.execute("UPDATE pending_request SET payload = ?", (CSV.replace(b"Nut", b"Evil"),))
    response = approve(api, grant_id)
    assert response.status_code == 409 and response.json()["error"]["code"] == "digest_mismatch"
    assert outcomes(api, grant_id) == [("needs_confirmation", None),
                                       ("blocked", "digest_mismatch")]
