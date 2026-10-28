"""What the pipeline says in words: a result names its tool in the request language, and a
confirmation that already waits can be worded again for a reader in the other language."""

from dataclasses import replace

import pytest

from execution_world import OPERATOR, PLUGIN, NoParams, build_world
from twotrack.core import Provenance, Tool, ToolOutputs, ToolResult, build_registry
from twotrack.core.pipeline import Pipeline, PipelineStatus

TITLES = {"part_count": {"en": "Part count", "zh": "零件数量"}}


def own_label(ctx, params):
    return ToolResult(provenance=Provenance(tool="own_label", row_count=0, as_of=ctx.now(),
                                            label="Chosen by the tool"))


def delegating(ctx, params):  # reports under the name of a helper, not of the tool that ran
    return ToolResult(provenance=Provenance(tool="helper_report", row_count=0, as_of=ctx.now()))


def plain(ctx, params):
    return ToolResult(provenance=Provenance(tool="plain_report", row_count=0, as_of=ctx.now()))


def tool(name, run):
    return Tool(name=name, description=name, params_model=NoParams, handler=run,
                roles=("viewer", "operator"), effect="read", approval="none",
                outputs=ToolOutputs(aggregates=(), columns=()), explorable=False)


@pytest.fixture
def world():
    world = build_world()
    titled = tuple(replace(t, title=TITLES[t.name]) if t.name in TITLES else t
                   for t in PLUGIN.tools)
    registry = build_registry([replace(PLUGIN, tools=(
        *titled, tool("own_label", own_label), tool("delegating", delegating),
        tool("plain_report", plain)))])
    world.pipeline = Pipeline(registry, world.store, world.audit, world.grants,
                              clock=world.clock)
    return world


def label_of(world, target, **facts):
    out = world.pipeline.run(world.ctx(OPERATOR, **facts), target, {})
    assert out.status is PipelineStatus.OK, out.problem
    assert out.result.provenance.tool == target or target == "delegating"
    return out.result.provenance.label


def test_a_result_is_named_by_its_tools_title_in_the_request_language(world):
    assert label_of(world, "part_count", language="zh") == "零件数量"
    assert label_of(world, "part_count", language="en") == "Part count"
    assert label_of(world, "part_count") == "Part count"  # no language recorded: English
    assert label_of(world, "part_count", language="fr") == "Part count"


def test_an_untitled_tool_is_named_by_its_humanized_id(world):
    assert label_of(world, "plain_report", language="zh") == "Plain report"


def test_a_label_the_tool_chose_is_kept(world):
    assert label_of(world, "own_label", language="zh") == "Chosen by the tool"


def test_a_result_reported_under_another_name_is_named_for_that_name(world):
    assert label_of(world, "delegating") == "Helper report"  # not a target: humanized


def test_the_tool_id_stays_the_id(world):
    out = world.pipeline.run(world.ctx(OPERATOR, language="zh"), "part_count", {})
    assert out.result.provenance.tool == "part_count"


# ---------------------------------------------------------------- wording a waiting request

PARAMS = {"sku": "F-1", "qty": 99}


def test_a_waiting_request_is_worded_exactly_as_the_confirmation_was(world):
    grant = world.pipeline.run(world.ctx(OPERATOR, language="en"), "set_qty", PARAMS).grant
    assert world.pipeline.summarize("set_qty", PARAMS, "en") == grant.summary
    zh = world.pipeline.run(world.ctx(OPERATOR, language="zh"), "set_qty", PARAMS).grant
    assert world.pipeline.summarize("set_qty", PARAMS, "zh") == zh.summary
    assert zh.summary == "Set the on-hand quantity of a part.（SKU：F-1；Quantity：99）"
    assert world.pipeline.summarize("set_qty", PARAMS, None) == grant.summary


def test_a_description_declared_per_language_words_the_summary_in_the_readers(world):
    """A description declared once (a plain string, as above) reads the same in every
    language; one declared per language starts the summary in the reader's language."""
    tools = tuple(replace(t, description={"en": t.description, "zh": "设置某个零件的在库数量。"})
                  if t.name == "set_qty" else t for t in PLUGIN.tools)
    pipeline = Pipeline(build_registry([replace(PLUGIN, tools=tools)]), world.store,
                        world.audit, world.grants, clock=world.clock)
    assert pipeline.summarize("set_qty", PARAMS, "zh") == \
        "设置某个零件的在库数量。（SKU：F-1；Quantity：99）"
    assert pipeline.summarize("set_qty", PARAMS, "en") == \
        "Set the on-hand quantity of a part. (SKU: F-1; Quantity: 99)"
    assert pipeline.summarize("set_qty", PARAMS, "fr") == \
        pipeline.summarize("set_qty", PARAMS, "en")


def test_equivalent_params_give_the_same_summary(world):
    assert world.pipeline.summarize("set_qty", {"sku": "F-1", "qty": "99"}, "en") == \
        world.pipeline.summarize("set_qty", PARAMS, "en")


def test_a_restricted_value_stays_masked_when_the_request_is_worded_again(world):
    summary = world.pipeline.summarize("set_qty", {**PARAMS, "unit_cost": 9.75}, "en")
    assert "9.75" not in summary and "Unit Cost" in summary


@pytest.mark.parametrize(("target", "params"), [
    ("gone_tool", PARAMS),  # the target left the registry
    ("set_qty", {"sku": "F-1", "qty": -1}),  # the params no longer validate
    ("set_qty", {"sku": "F-1"}),
])
def test_a_request_that_no_longer_fits_has_no_new_wording(world, target, params):
    assert world.pipeline.summarize(target, params, "zh") is None
