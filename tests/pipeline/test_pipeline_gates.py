"""Gate order: lookup -> authz -> preconditions -> params -> confirmation -> execute."""

from execution_world import ADMIN, OPERATOR, VIEWER
from twotrack.core import TraceStatus
from twotrack.core.audit import AuditStatus
from twotrack.core.errors import InputProblem
from twotrack.core.pipeline import PipelineStatus

BAD_PARAMS = {"sku": "", "qty": -5}


def gate_stage(outcome):
    blocked = [s for s in outcome.trace if s.step == "gate" and s.status is TraceStatus.BLOCKED]
    return blocked[-1].facts["stage"]


def test_unknown_target_is_invalid_and_audited(world):
    out = world.pipeline.run(world.ctx(OPERATOR), "no_such_tool", {})
    assert out.status is PipelineStatus.INVALID and gate_stage(out) == "lookup"
    assert world.audit_statuses() == [AuditStatus.INVALID]


def test_authz_runs_before_preconditions_and_params(world):
    # viewer, unmet precondition, invalid params: authz must be the reported stage
    out = world.pipeline.run(world.ctx(VIEWER), "gated_set_qty", BAD_PARAMS)
    assert out.status is PipelineStatus.BLOCKED and gate_stage(out) == "authz"
    assert out.grant is None and out.result is None


def test_preconditions_run_before_params(world):
    out = world.pipeline.run(world.ctx(OPERATOR), "gated_set_qty", BAD_PARAMS)
    assert out.status is PipelineStatus.BLOCKED and gate_stage(out) == "precondition"
    assert out.problem == "Upload a file first."
    assert out.trace[-1].facts["unmet"] == ["has_upload"]


def test_params_validated_before_confirmation(world):
    out = world.pipeline.run(world.ctx(OPERATOR, upload=True), "gated_set_qty", BAD_PARAMS)
    assert out.status is PipelineStatus.INVALID and gate_stage(out) == "params"
    assert out.grant is None  # no grant is issued for a request that cannot run
    assert "qty" in out.problem and "-5" not in out.problem  # no input echo


def test_viewer_cannot_write(world):
    out = world.pipeline.run(world.ctx(VIEWER), "set_qty", {"sku": "F-1", "qty": 1})
    assert out.status is PipelineStatus.BLOCKED and gate_stage(out) == "authz"
    assert world.store.get("part", "F-1")["qty"] == 40
    assert world.grants.pending(None) == []


def test_admin_passes_role_lists(world):
    out = world.pipeline.run(world.ctx(ADMIN), "part_count", {})
    assert out.status is PipelineStatus.OK
    assert out.result.aggregates == {"part_count": 2}


def test_read_runs_without_grant_and_traces_each_stage(world):
    seen = []
    ctx = world.ctx(VIEWER)
    ctx = type(ctx)(principal=ctx.principal, clock=world.clock, trace=seen.append)
    out = world.pipeline.run(ctx, "part_count", {}, route_source="rule")
    assert out.status is PipelineStatus.OK
    assert [s.step for s in out.trace] == ["gate", "execute"]
    assert seen == out.trace  # forwarded to the caller's trace sink as well
    entry = world.audit.get(out.audit_ids[-1])
    assert entry.route_source == "rule" and entry.row_count == 0
    assert entry.effect == "read" and entry.status is AuditStatus.OK


def test_read_tool_cannot_write(world):
    out = world.pipeline.run(world.ctx(OPERATOR), "sneaky_write", {})
    assert out.status is PipelineStatus.ERROR and out.result is None
    assert world.store.get("part", "X-1") is None
    assert world.audit_statuses()[-1] is AuditStatus.ERROR


def test_workflow_write_needs_grant_and_runner(world):
    calls = []

    def runner(ctx, workflow, params):
        calls.append((workflow.name, params))
        return world.registry.tool("part_count").handler(ctx, None)

    first = world.pipeline.run(world.ctx(OPERATOR), "restock_all", {"scope": "all"},
                               workflow_runner=runner)
    assert first.status is PipelineStatus.NEEDS_CONFIRMATION and calls == []
    second = world.pipeline.run(world.ctx(OPERATOR), "restock_all", {"scope": "all"},
                                grant_token=first.grant.token, workflow_runner=runner)
    assert second.status is PipelineStatus.OK
    assert calls == [("restock_all", {"scope": "all"})]


def test_workflow_missing_input_is_invalid_not_error(world):
    def runner(ctx, workflow, params):
        raise InputProblem("Which SKU should be restocked?")

    first = world.pipeline.run(world.ctx(OPERATOR), "restock_all", {"scope": "all"},
                               workflow_runner=runner)
    out = world.pipeline.run(world.ctx(OPERATOR), "restock_all", {"scope": "all"},
                             grant_token=first.grant.token, workflow_runner=runner)
    assert out.status is PipelineStatus.INVALID
    assert out.problem == "Which SKU should be restocked?"
    assert world.audit_statuses()[-1] is AuditStatus.INVALID


def test_workflow_without_runner_is_an_error(world):
    first = world.pipeline.run(world.ctx(OPERATOR), "restock_all", {})
    out = world.pipeline.run(world.ctx(OPERATOR), "restock_all", {},
                             grant_token=first.grant.token)
    assert out.status is PipelineStatus.ERROR
