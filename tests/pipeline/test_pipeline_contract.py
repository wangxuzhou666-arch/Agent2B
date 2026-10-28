"""Run-time half of the tool contract: grants follow the declared approval, and a result may
carry only what its target declared. A breach is an audited plugin error, never a crash."""

import pytest
from pydantic import BaseModel, ConfigDict

from execution_world import OPERATOR, OPERATOR_2, PLUGIN, VIEWER, build_world
from twotrack.core import (
    Plugin,
    Provenance,
    Step,
    StepKind,
    Tool,
    ToolOutputs,
    ToolResult,
    Workflow,
    build_registry,
)
from twotrack.core.audit import AuditStatus
from twotrack.core.pipeline import Pipeline, PipelineStatus

SENT: list[str] = []
MARK = 777001  # a value that must never show up in a refusal or an audit record
TABLE = ToolOutputs(aggregates=("n",), columns=("a",))


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str


def make(ctx, tool, rows=(), columns=(), **aggregates):
    return ToolResult(rows=list(rows), columns=list(columns), aggregates=aggregates,
                      provenance=Provenance(tool=tool, row_count=len(rows), as_of=ctx.now()))


def tidy(ctx, params):
    return make(ctx, "tidy", [{"a": 1, "_entity": "x"}], ["a"], n=1)  # the framework key is free


def quiet(ctx, params):
    return make(ctx, "quiet")  # may emit less than it declares


def extra_aggregate(ctx, params):
    return make(ctx, "extra_aggregate", n=1, secret=MARK)


def extra_column(ctx, params):
    return make(ctx, "extra_column", [{"a": MARK}], ["a", "b"], n=1)


def extra_row_key(ctx, params):
    return make(ctx, "extra_row_key", [{"a": 1, "hidden": MARK}], ["a"], n=1)


def write_extra(ctx, params):
    ctx.store.upsert_many("part", [{"sku": "X-9", "name": "half done"}])
    return make(ctx, "write_extra", leaked=1)


def ship(ctx, params):
    SENT.append(params.to)
    return make(ctx, "ship", sent=1)


def declared(name, handler, effect, approval, outputs=TABLE, params=NoArgs, **kw):
    return Tool(name=name, description=name, params_model=params, handler=handler,
                roles=("operator", "admin"), effect=effect, approval=approval, outputs=outputs,
                explorable=False, **kw)


CONTRACT = Plugin(name="contract", version="0.1.0", tools=(
    declared("tidy", tidy, "read", "none"),
    declared("quiet", quiet, "read", "none"),
    declared("extra_aggregate", extra_aggregate, "read", "none"),
    declared("extra_column", extra_column, "read", "none"),
    declared("extra_row_key", extra_row_key, "read", "none"),
    declared("write_extra", write_extra, "write", "required",
             ToolOutputs(aggregates=(), columns=())),
    declared("ship", ship, "external", "required", ToolOutputs(aggregates=("sent",), columns=()),
             params=Dest),
    declared("ping", ship, "external", "none", ToolOutputs(aggregates=("sent",), columns=()),
             params=Dest),
), workflows=(
    Workflow(name="leaky_plan", description="A plan that adds a figure.", roles=("operator",),
             effect="read", approval="none", outputs=TABLE, explorable=False,
             steps=(Step(id="plan", kind=StepKind.DETERMINISTIC, description="plan",
                         fn=lambda ctx, data: data),)),
))


@pytest.fixture
def world():
    SENT.clear()
    world = build_world()
    world.pipeline = Pipeline(build_registry([PLUGIN, CONTRACT]), world.store, world.audit,
                              world.grants, clock=world.clock)
    return world


def run(world, principal, target, params=None, **kw):
    return world.pipeline.run(world.ctx(principal), target, params or {}, **kw)


def latest(world):
    return world.audit.list(limit=50).items[0]


# ---------------------------------------------------------------- outputs


def test_a_result_inside_its_declaration_passes_and_the_narrator_sees_declared_figures(world):
    out = run(world, OPERATOR, "tidy")
    assert out.status is PipelineStatus.OK
    view = out.result.narrator_view()
    assert set(view["aggregates"]) <= set(TABLE.aggregates) and view["columns"] == ["a"]
    assert run(world, OPERATOR, "quiet").status is PipelineStatus.OK


@pytest.mark.parametrize(("target", "undeclared"), [
    ("extra_aggregate", "aggregates ['secret']"),
    ("extra_column", "columns ['b']"),
    ("extra_row_key", "columns ['hidden']"),
])
def test_an_undeclared_output_is_an_audited_plugin_error(world, target, undeclared):
    out = run(world, OPERATOR, target)
    assert out.status is PipelineStatus.ERROR and out.result is None
    assert out.problem == "execution failed (OutputContractError)"  # no key, no value
    assert out.trace[-1].step == "execute" and out.trace[-1].status.value == "error"
    entry = latest(world)
    assert (entry.status, entry.target) == (AuditStatus.ERROR, target)
    assert "OutputContractError" in entry.problem and undeclared in entry.problem
    assert str(MARK) not in entry.problem + str(out.model_dump())  # names, never values


def test_an_undeclared_write_result_rolls_the_write_back(world):
    first = run(world, OPERATOR, "write_extra")
    assert first.status is PipelineStatus.NEEDS_CONFIRMATION
    out = run(world, OPERATOR, "write_extra", grant_token=first.grant.token)
    assert out.status is PipelineStatus.ERROR and out.result is None
    assert out.problem == "execution failed and was rolled back (OutputContractError)"
    assert world.store.get("part", "X-9") is None
    assert world.audit_statuses() == [AuditStatus.NEEDS_CONFIRMATION, AuditStatus.STARTED,
                                      AuditStatus.ERROR]


def test_a_workflow_result_is_held_to_its_declaration_too(world):
    def runner(ctx, workflow, params):
        return make(ctx, "leaky_plan", n=1, extra=2)

    out = run(world, OPERATOR, "leaky_plan", workflow_runner=runner)
    assert out.status is PipelineStatus.ERROR and out.result is None
    assert "aggregates ['extra']" in latest(world).problem


# ---------------------------------------------------------------- grants follow the declaration


def test_an_external_target_that_requires_approval_uses_the_write_grant_flow(world):
    first = run(world, OPERATOR, "ship", {"to": "a"})
    assert first.status is PipelineStatus.NEEDS_CONFIRMATION and first.grant and SENT == []
    token = first.grant.token

    stolen = run(world, OPERATOR_2, "ship", {"to": "a"}, grant_token=token)
    changed = run(world, OPERATOR, "ship", {"to": "b"}, grant_token=token)
    assert stolen.status is changed.status is PipelineStatus.BLOCKED and SENT == []

    approved = run(world, OPERATOR, "ship", {"to": "a"}, grant_token=token)
    assert approved.status is PipelineStatus.OK and SENT == ["a"]
    again = run(world, OPERATOR, "ship", {"to": "a"}, grant_token=token)
    assert again.status is PipelineStatus.BLOCKED and SENT == ["a"]  # one time only

    started = [e for e in world.audit.list(limit=50).items if e.status is AuditStatus.STARTED]
    assert [e.grant_id for e in started] == [first.grant.grant_id]  # the start is on record
    assert world.audit_statuses() == [
        AuditStatus.NEEDS_CONFIRMATION, AuditStatus.BLOCKED, AuditStatus.BLOCKED,
        AuditStatus.STARTED, AuditStatus.OK, AuditStatus.BLOCKED]


def test_an_external_target_without_approval_runs_at_once(world):
    out = run(world, OPERATOR, "ping", {"to": "a"})
    assert out.status is PipelineStatus.OK and out.grant is None and SENT == ["a"]
    assert world.audit_statuses() == [AuditStatus.OK]


def test_a_role_the_policy_denies_never_reaches_the_grant(world):
    out = run(world, VIEWER, "ship", {"to": "a"})
    assert out.status is PipelineStatus.BLOCKED and out.grant is None and SENT == []
    assert latest(world).status is AuditStatus.BLOCKED
