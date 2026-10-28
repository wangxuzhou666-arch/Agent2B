"""Every figure the built-in supply-chain targets report has a label in both languages, and the
template narration of each result reads cleanly and passes the numeric lock."""

import pytest
from world import NOW, OPERATOR, build_world

from twotrack.core import ToolContext
from twotrack.core.guards.numeric_lock import lock_narrative
from twotrack.core.narration import result_view, template_narration

READS = ["inventory_lookup", "low_stock", "reorder_suggestion", "po_status", "late_shipments",
         "supplier_compare"]


@pytest.fixture(scope="module")
def results():
    world = build_world(seed=True)
    out = {name: (world.registry.tool(name), world.call(name)) for name in READS}
    plan = world.run(OPERATOR, "reorder_plan")
    out["reorder_plan"] = (world.registry.workflow("reorder_plan"), plan.result)
    create = world.registry.tool("create_po")
    sku = world.store.list("sku", sort="sku_id", limit=1).rows[0]["sku_id"]
    ctx = ToolContext(principal=OPERATOR, store=world.store, clock=lambda: NOW)
    out["create_po"] = (create, create.handler(ctx, create.parse_params({"sku_id": sku,
                                                                         "qty": 5})))
    return out


@pytest.mark.parametrize("name", [*READS, "reorder_plan", "create_po"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_aggregate_is_labelled_and_the_template_is_locked(results, name, language):
    target, result = results[name]
    assert result.aggregates, name
    missing = set(result.aggregates) - set(target.aggregate_labels[language])
    assert not missing, f"{name}: no {language} label for {sorted(missing)}"
    text = template_narration(result_view(result, language=language,
                                          labels=target.aggregate_labels))
    assert not [key for key in result.aggregates if key in text], text
    assert lock_narrative(text, result.allowed_numbers()).stripped_count == 0, text


def test_messages_and_scopes_follow_the_request_language():
    world = build_world(seed=True)
    late = world.registry.tool("late_shipments")
    zh = late.handler(world.ctx(OPERATOR, language="zh"), late.parse_params({}))
    en = late.handler(world.ctx(OPERATOR), late.parse_params({}))
    assert zh.provenance.scope.startswith("预计到货早于") and zh.aggregates == en.aggregates
    assert en.provenance.scope.startswith("open and shipped orders")
