"""Audit is fail-closed: no record, no operation."""

from execution_world import OPERATOR, VIEWER, FailingAudit
from twotrack.core.audit import AuditStatus
from twotrack.core.canonical import MASK
from twotrack.core.pipeline import PipelineStatus

PARAMS = {"sku": "F-1", "qty": 99, "unit_cost": 0.31}


def granted(world):
    return world.pipeline.run(world.ctx(OPERATOR), "set_qty", PARAMS).grant.token


def test_outcome_audit_failure_rolls_the_write_back(world):
    token = granted(world)
    pipeline = world.with_audit(FailingAudit(world.audit, {AuditStatus.OK}))
    out = pipeline.run(world.ctx(OPERATOR), "set_qty", PARAMS, grant_token=token)
    assert out.status is PipelineStatus.ERROR and out.result is None
    assert "rolled back" in out.problem
    assert world.store.get("part", "F-1")["qty"] == 40  # data change undone
    assert world.audit_statuses()[-2:] == [AuditStatus.STARTED, AuditStatus.ERROR]


def test_intent_audit_failure_means_nothing_executes(world):
    token = granted(world)
    pipeline = world.with_audit(FailingAudit(world.audit, {AuditStatus.STARTED}))
    out = pipeline.run(world.ctx(OPERATOR), "set_qty", PARAMS, grant_token=token)
    assert out.status is PipelineStatus.ERROR
    assert [s.step for s in out.trace] == ["gate"]  # never reached execute
    assert world.store.get("part", "F-1")["qty"] == 40


def test_read_with_failing_audit_withholds_the_result(world):
    pipeline = world.with_audit(FailingAudit(world.audit, {AuditStatus.OK}))
    out = pipeline.run(world.ctx(VIEWER), "part_count", {})
    assert out.status is PipelineStatus.ERROR and out.result is None


def test_blocked_call_with_failing_audit_is_an_error(world):
    pipeline = world.with_audit(FailingAudit(world.audit, {AuditStatus.BLOCKED}))
    out = pipeline.run(world.ctx(VIEWER), "set_qty", PARAMS)
    assert out.status is PipelineStatus.ERROR


def test_confirmation_with_failing_audit_revokes_the_grant(world):
    pipeline = world.with_audit(FailingAudit(world.audit, {AuditStatus.NEEDS_CONFIRMATION}))
    out = pipeline.run(world.ctx(OPERATOR), "set_qty", PARAMS)
    assert out.status is PipelineStatus.ERROR and out.grant is None
    assert world.grants.pending(None) == []


def test_handler_exception_rolls_back_partial_writes(world):
    token = world.pipeline.run(world.ctx(OPERATOR), "write_then_fail",
                               {"sku": "NEW-1", "qty": 3}).grant.token
    out = world.pipeline.run(world.ctx(OPERATOR), "write_then_fail", {"sku": "NEW-1", "qty": 3},
                             grant_token=token)
    assert out.status is PipelineStatus.ERROR and "RuntimeError" in out.problem
    assert world.store.get("part", "NEW-1") is None
    assert world.audit_statuses()[-1] is AuditStatus.ERROR


def test_audit_masks_restricted_params_and_keeps_trace(world):
    token = granted(world)
    out = world.pipeline.run(world.ctx(OPERATOR), "set_qty", PARAMS, grant_token=token)
    entry = world.audit.get(out.audit_ids[-1])
    assert entry.params == {"sku": "F-1", "qty": 99, "unit_cost": MASK}
    assert [s.step for s in entry.trace] == ["gate", "execute"]
    assert entry.params_digest == world.audit.get(out.audit_ids[0]).params_digest
