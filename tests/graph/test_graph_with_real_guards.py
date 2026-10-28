"""The graph wired to the real pipeline, numeric lock and egress guard (no fakes in between)."""

from tests.execution_world import OPERATOR, build_world
from tests.fakes import ScriptedProvider
from twotrack.core.graph import Engine, run_query
from twotrack.core.guards.egress import REDACTED, EgressGuard
from twotrack.providers import MockProvider


def engine(world, provider, **kw):
    return Engine(registry=world.registry, provider=provider, pipeline=world.pipeline, **kw)


def test_read_tool_through_real_pipeline_and_default_lock():
    world = build_world()
    response = run_query(engine(world, MockProvider(hallucinated_number=777)),
                         world.ctx(OPERATOR), "count", explicit_target="part_count")
    assert response.error is None and response.aggregates == {"part_count": 2}
    assert "777" not in response.answer and response.narrative_stripped == 1
    executes = [s for s in response.trace if s.step == "execute"]
    assert len(executes) == len({id(s) for s in executes})  # emitted once each


def test_write_needs_grant_then_runs_with_it():
    world = build_world()
    eng = engine(world, MockProvider())
    params = {"sku": "F-1", "qty": 12}
    first = run_query(eng, world.ctx(OPERATOR), "set it", explicit_target="set_qty",
                      explicit_params=params)
    assert first.grant is not None and world.store.get("part", "F-1")["qty"] == 40
    second = run_query(eng, world.ctx(OPERATOR), "set it", explicit_target="set_qty",
                       explicit_params=params, grant_token=first.grant.token)
    assert second.error is None and world.store.get("part", "F-1")["qty"] == 12


def test_egress_guard_redacts_before_the_provider_sees_anything():
    world = build_world()
    provider = ScriptedProvider()
    guard = EgressGuard(lambda: ["Supplier Alpha"])
    run_query(engine(world, provider, scrubber=guard), world.ctx(OPERATOR),
              "count parts from Supplier Alpha", explicit_target="part_count")
    sent = provider.payloads()
    assert "Supplier Alpha" not in sent and REDACTED in sent
