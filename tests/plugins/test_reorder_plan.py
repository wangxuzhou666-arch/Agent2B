"""reorder_plan end to end with the mock provider, through the real pipeline and the graph."""

from world import ADMIN, OPERATOR, VIEWER, build_world

from twotrack.core.graph import Engine, run_query
from twotrack.core.guards.numeric_lock import lock_narrative
from twotrack.core.narration import allowed_numbers
from twotrack.core.workflow import run_workflow
from twotrack.providers.mock import MockProvider

INVENTED = 987654.0


def _runner(provider, narrations):
    return lambda ctx, wf, params: run_workflow(ctx, wf, params, provider,
                                                on_narration=narrations.append)


def test_plan_on_the_handcrafted_store(small):
    narrations = []
    outcome = small.run(VIEWER, "reorder_plan", {},
                        workflow_runner=_runner(MockProvider(), narrations))
    assert outcome.status == "ok", outcome.problem
    result = outcome.result
    # class A first (larger spend first), then B, then C; quantities match reorder_suggestion
    assert [(r["sku_id"], r["suggested_qty"]) for r in result.rows] == [
        ("SKU-1", 27), ("SKU-5", 1), ("SKU-4", 6), ("SKU-3", 6)]
    assert [r["supplier_id"] for r in result.rows] == ["SUP-002", "SUP-003", "SUP-003",
                                                        "SUP-003"]
    assert [r["supplier_source"] for r in result.rows] == ["history", "history",
                                                            "best overall", "best overall"]
    # SKU-1: 27 x 2.6 (SUP-002's last price); SKU-5: 1 x 0.45; SKU-4: 6 x 10.0 (list cost);
    # SKU-3 has no price at all
    assert [r["estimated_cost"] for r in result.rows] == [70.2, 0.45, 60.0, None]
    assert result.aggregates == {"sku_count": 4, "total_suggested_qty": 40, "class_a_skus": 2,
                                 "class_b_skus": 1, "class_c_skus": 1, "supplier_count": 2,
                                 "estimated_cost": 130.65}
    suggestion = small.call("reorder_suggestion")
    assert suggestion.aggregates["total_suggested_qty"] == result.aggregates[
        "total_suggested_qty"]

    [narration] = narrations
    assert narration.source == "model" and narration.stripped == 0
    assert result.message == narration.text
    assert lock_narrative(narration.text, allowed_numbers(result)).stripped_count == 0
    assert "130.65" in narration.text and "40" in narration.text


def test_plan_filtered_by_criticality(small):
    outcome = small.run(OPERATOR, "reorder_plan", {"criticality": "a"})
    assert [r["sku_id"] for r in outcome.result.rows] == ["SKU-1", "SKU-5"]
    assert outcome.result.provenance.scope == "Criticality: A"


def test_plan_with_nothing_low_is_an_empty_plan():
    world = build_world()
    world.store.upsert_many("inventory", [{"id": "INV-1", "sku_id": "SKU-1",
                                           "warehouse": "WH-EAST", "on_hand": 5}])
    outcome = world.run(ADMIN, "reorder_plan")
    assert outcome.status == "ok"
    assert outcome.result.rows == [] and outcome.result.aggregates["sku_count"] == 0


def test_invented_figures_are_stripped_by_the_numeric_lock(small):
    narrations = []
    outcome = small.run(OPERATOR, "reorder_plan", {}, workflow_runner=_runner(
        MockProvider(hallucinated_number=INVENTED), narrations))
    [narration] = narrations
    assert narration.stripped == 1
    assert "987654" not in narration.text and "987,654" not in narration.text
    assert outcome.result.aggregates["sku_count"] == 4  # the numbers themselves are untouched


def _engine(world, provider):
    return Engine(registry=world.registry, provider=provider, pipeline=world.pipeline)


def test_query_to_narrated_plan_through_the_graph(seeded):
    response = run_query(_engine(seeded, MockProvider()), seeded.ctx(VIEWER),
                         "build me a purchasing plan")
    assert response.error is None
    assert response.decision.target == "reorder_plan"
    assert response.answer_source == "model" and response.narrative_stripped == 0
    low = seeded.call("low_stock").aggregates["low_stock_count"]
    assert response.aggregates["sku_count"] == low > 0
    assert len(response.rows) == low
    allowed = {float(v) for v in [*response.aggregates.values(), len(response.rows)]}
    assert lock_narrative(response.answer, allowed).stripped_count == 0
    steps = [(s.step, s.facts.get("step")) for s in response.trace if s.step == "execute"]
    assert [name for _, name in steps if name] == ["find_low_stock", "compute_suggestions",
                                                   "group_and_source", "narrate"]


def test_graph_narration_with_an_invented_figure_is_locked(seeded):
    response = run_query(_engine(seeded, MockProvider(hallucinated_number=INVENTED)),
                         seeded.ctx(VIEWER), "生成补货计划")
    assert response.decision.target == "reorder_plan"
    assert response.narrative_stripped == 1 and "987654" not in response.answer


def test_create_po_through_the_graph_needs_confirmation():
    """With no model, one phrase ("create a PO") and every param the write requires go
    straight to the grant; the grant runs it. Without the quantity the phrase is weak
    evidence: routing first offers the tool as a one-option choice, and picking it asks for
    the grant."""
    world = build_world(seed=True)
    engine = _engine(world, MockProvider())
    query = "create a PO for SKU-10042, 25 units"
    first = run_query(engine, world.ctx(OPERATOR), query)
    assert first.clarify_options == [] and first.grant is not None and first.rows == []
    before = world.store.count("purchase_order")
    done = run_query(engine, world.ctx(OPERATOR), query, explicit_target="create_po",
                     grant_token=first.grant.token)
    assert done.error is None and done.rows[0]["status"] == "draft"
    assert done.rows[0]["qty"] == 25
    assert world.store.count("purchase_order") == before + 1

    partial = "create a PO for SKU-10042"
    offered = run_query(engine, world.ctx(OPERATOR), partial)
    assert [o.name for o in offered.clarify_options] == ["create_po"]
    assert offered.grant is None and offered.rows == []
    picked = run_query(engine, world.ctx(OPERATOR), partial, explicit_target="create_po")
    assert picked.grant is None and "qty" in (picked.answer or "")  # it still needs a quantity
