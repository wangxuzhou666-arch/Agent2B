"""Write operations need a one-time grant bound to (user, target, validated params)."""

import threading

from execution_world import ADMIN, OPERATOR, OPERATOR_2
from twotrack.core.audit import AuditStatus
from twotrack.core.grants import GRANT_TTL, grant_id_for
from twotrack.core.pipeline import PipelineStatus

PARAMS = {"sku": "F-1", "qty": 99}


def ask(world, principal=OPERATOR, params=PARAMS):
    out = world.pipeline.run(world.ctx(principal), "set_qty", params)
    assert out.status is PipelineStatus.NEEDS_CONFIRMATION
    return out.grant


def confirm(world, token, principal=OPERATOR, params=PARAMS):
    return world.pipeline.run(world.ctx(principal), "set_qty", params, grant_token=token)


def test_write_without_grant_needs_confirmation_and_changes_nothing(world):
    grant = ask(world)
    assert grant.token and grant.expires_at == world.clock.now + GRANT_TTL
    # params by their field labels, never key=value or an empty restricted value as "***"
    assert grant.summary == "Set the on-hand quantity of a part. (SKU: F-1; Quantity: 99)"
    assert world.store.get("part", "F-1")["qty"] == 40
    assert world.audit_statuses() == [AuditStatus.NEEDS_CONFIRMATION]


def test_valid_grant_executes_once(world):
    grant = ask(world)
    out = confirm(world, grant.token)
    assert out.status is PipelineStatus.OK
    assert world.store.get("part", "F-1")["qty"] == 99
    assert world.audit_statuses() == [AuditStatus.NEEDS_CONFIRMATION, AuditStatus.STARTED,
                                      AuditStatus.OK]
    assert world.audit.get(out.audit_ids[-1]).grant_id == grant.grant_id


def test_grant_reuse_is_blocked(world):
    grant = ask(world)
    assert confirm(world, grant.token).status is PipelineStatus.OK
    world.store.upsert_many("fastener", [{**world.store.get("fastener", "F-1"), "qty": 1}])
    again = confirm(world, grant.token)
    assert again.status is PipelineStatus.BLOCKED
    assert world.store.get("part", "F-1")["qty"] == 1


def test_grant_of_another_user_is_blocked(world):
    grant = ask(world, OPERATOR)
    for intruder in (OPERATOR_2, ADMIN):
        assert confirm(world, grant.token, intruder).status is PipelineStatus.BLOCKED
    assert world.store.get("part", "F-1")["qty"] == 40
    assert confirm(world, grant.token).status is PipelineStatus.OK  # still usable by owner


def test_param_tamper_is_blocked(world):
    grant = ask(world)
    out = confirm(world, grant.token, params={"sku": "F-1", "qty": 100000})
    assert out.status is PipelineStatus.BLOCKED
    assert world.store.get("part", "F-1")["qty"] == 40


def test_equivalent_params_confirm_the_same_request(world):
    grant = ask(world, params={"sku": "F-1", "qty": "99"})  # validated to qty=99
    assert confirm(world, grant.token, params={"qty": 99, "sku": "F-1"}).status \
        is PipelineStatus.OK


def test_expired_grant_is_blocked(world):
    grant = ask(world)
    world.clock.advance(minutes=15, seconds=1)
    assert confirm(world, grant.token).status is PipelineStatus.BLOCKED
    assert world.store.get("part", "F-1")["qty"] == 40


def test_forged_token_is_blocked(world):
    ask(world)
    assert confirm(world, "not-a-real-token").status is PipelineStatus.BLOCKED
    assert confirm(world, "").status is PipelineStatus.BLOCKED


def test_a_refusal_names_the_grant_it_was_given(world):
    """Another user, another request, already used, expired: nothing ran, and the refusal
    record carries the presented grant's public id, so the grant's records say why."""
    grant = ask(world)
    refused = [confirm(world, grant.token, OPERATOR_2),
               confirm(world, grant.token, params={"sku": "F-1", "qty": 100000})]
    assert confirm(world, grant.token).status is PipelineStatus.OK
    refused.append(confirm(world, grant.token))  # used
    late = ask(world)
    world.clock.advance(minutes=15, seconds=1)
    for out, grant_id in [*((o, grant.grant_id) for o in refused),
                          (confirm(world, late.token), late.grant_id),
                          (confirm(world, "not-a-real-token"), grant_id_for("not-a-real-token")),
                          (confirm(world, ""), None)]:
        assert out.status is PipelineStatus.BLOCKED
        entry = world.audit.get(out.audit_ids[-1])
        assert (entry.status, entry.grant_id) == (AuditStatus.BLOCKED, grant_id)
        assert entry.trace[-1].facts["stage"] == "confirmation"
    assert world.store.get("part", "F-1")["qty"] == 99  # the one valid confirmation ran


def test_find_tells_what_became_of_a_grant(world):
    used, cancelled, late = ask(world), ask(world), ask(world)
    assert world.grants.find(used.grant_id).state == "open"
    assert (world.grants.find(used.grant_id).target, world.grants.find(used.grant_id).user_id) \
        == ("set_qty", "olga")
    confirm(world, used.token)
    world.grants.reject(cancelled.token, OPERATOR)
    assert world.grants.find(used.grant_id).state == "used"
    assert world.grants.find(cancelled.grant_id).state == "rejected"
    world.clock.advance(minutes=15, seconds=1)
    assert world.grants.find(late.grant_id).state == "expired"
    world.grants.reject(late.token, OPERATOR)  # closed only once it had run out: still expired
    assert world.grants.find(late.grant_id).state == "expired"
    assert world.grants.find(cancelled.grant_id).state == "rejected"  # cancelled in time
    assert world.grants.find(used.grant_id).state == "used"  # used stays used
    assert world.grants.find("0" * 16) is None
    ask(world)  # issuing a grant purges the expired ones: the store forgets them
    assert all(world.grants.find(g.grant_id) is None for g in (used, cancelled, late))


def test_pending_and_reject(world):
    mine = ask(world, OPERATOR)
    ask(world, OPERATOR_2)
    assert [g.user_id for g in world.grants.pending(OPERATOR)] == ["olga"]
    assert len(world.grants.pending(ADMIN)) == 2
    assert not hasattr(world.grants.pending(ADMIN)[0], "token")  # listing never leaks tokens
    assert not world.grants.reject(mine.token, OPERATOR_2)
    assert world.grants.reject(mine.token, OPERATOR)
    assert confirm(world, mine.token).status is PipelineStatus.BLOCKED


def test_concurrent_consume_succeeds_once(world):
    grant = ask(world)
    wins = []

    def attempt():
        wins.append(world.grants.consume(grant.token, OPERATOR, "set_qty",
                                         {"sku": "F-1", "qty": 99, "unit_cost": None}))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(wins) == [False] * 7 + [True]


def test_grant_store_failure_fails_closed(world, monkeypatch):
    grant = ask(world)

    def broken(*args):
        raise OSError("grant db unreachable")

    monkeypatch.setattr(world.grants, "consume", broken)
    out = confirm(world, grant.token)
    assert out.status is PipelineStatus.ERROR
    assert world.store.get("part", "F-1")["qty"] == 40
    assert world.audit_statuses()[-1] is AuditStatus.ERROR
