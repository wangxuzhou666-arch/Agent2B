"""A complete write is strong evidence (core/routing/rule_arm.py): one phrase of a write target,
and every param that target requires given by the question. With no model it goes straight to
the write's one-time grant, also when it won a contest by the margin over rivals that matched
only names of its params; weak hits, ties, contests no target won and the reconcile matrix are
as before.

A request that holds a write back never goes straight to its approval: a question
(core/routing/interrogative.py), a negated phrase (core/routing/negation.py) or an excluded value
(core/routing/exclusion.py) caps a target that changes data at weak on every path to strength
(an example, two keywords, complete params), and a model that agrees with it does not make it
strong again (core/routing/reconcile.py). Routing confirms it first.

The rule is generic, so most cases use a fixture plugin of their own (no supply-chain names);
the built-in tools (``builtin``, conftest.py) show it on the real write.
"""

import re

import pytest
from pydantic import BaseModel, ConfigDict, Field

from tests.fakes import NoParams, ScriptedProvider, make_ctx
from twotrack.core import (
    Plugin,
    RouteKind,
    RouteSource,
    Step,
    StepKind,
    Tool,
    ToolOutputs,
    Workflow,
)
from twotrack.core import build_registry as build
from twotrack.core.params import gives_required_params, required_params
from twotrack.core.routing import STRONG_KEYWORDS, WEAK, decide_route, match_rules
from twotrack.core.routing.rule_arm import param_words
from twotrack.providers import LLMRoute, MockProvider


class MoveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str
    qty: int = Field(gt=0)
    note: str | None = None


def extract(query: str) -> dict:
    found = {}
    if item := re.search(r"\bitem\s+([a-z0-9-]+)", query.lower()):
        found["item"] = item.group(1)
    if qty := re.search(r"\b(\d+)\s+units?\b", query):
        found["qty"] = int(qty.group(1))
    return found


def tool(name: str, effect: str, *keywords: str, params_model=MoveParams,
         examples: tuple[str, ...] = ()) -> Tool:
    return Tool(name=name, description=f"{name.replace('_', ' ')} for an item.",
                params_model=params_model, handler=lambda ctx, params: None,
                roles=("operator",), effect=effect, keywords=keywords, examples=examples,
                param_extractor=extract,
                approval="required" if effect == "write" else "none", explorable=False,
                outputs=ToolOutputs(aggregates=(), columns=()))


PLAN = Workflow(name="move_plan", description="Plan stock moves.", roles=("operator",),
                effect="read", approval="none", explorable=False,
                outputs=ToolOutputs(aggregates=(), columns=()), keywords=("move plan",),
                steps=(Step(id="plan", kind=StepKind.DETERMINISTIC, description="plan",
                            fn=lambda ctx, data: data, input_model=MoveParams),))

REGISTRY = build([Plugin(name="complete_fixture", version="0.1.0", workflows=(PLAN,), tools=(
    tool("move_stock", "write", "move stock"),
    tool("ship_out", "external", "ship out"),  # leaves the deployment; approval="none": no grant
    tool("count_items", "read", "count items"),
    tool("reset_bins", "write", "reset bins", params_model=NoParams),  # requires nothing
    tool("transfer_stock", "write", "transfer stock"),
    tool("relocate_stock", "write", "transfer stock"),  # the same phrase: a tie
))])


def route(query: str, provider=None, steps=None):
    ctx = make_ctx("operator", sink=steps.append if steps is not None else None)
    return decide_route(query, ctx, REGISTRY, provider or MockProvider())


def top(query: str):
    return match_rules(query, REGISTRY.candidates(make_ctx("operator"))).top


def rule_facts(steps) -> dict:
    return next(s for s in steps if s.step == "understand").facts["rule"]


# ---------------------------------------------------------------- what "complete" means


def test_the_required_params_are_the_params_models_required_fields():
    move = REGISTRY.tool("move_stock")
    assert required_params(move) == ("item", "qty")
    assert required_params(REGISTRY.tool("reset_bins")) == ()
    assert required_params(REGISTRY.workflows["move_plan"]) == ("item", "qty")  # first step


def test_complete_means_every_required_param_given_and_valid_as_it_is():
    move = REGISTRY.tool("move_stock")
    assert gives_required_params(move, {"item": "k-1", "qty": 5})
    assert gives_required_params(move, {"item": "k-1", "qty": 5, "note": "urgent"})
    assert not gives_required_params(move, {"item": "k-1"})  # no quantity
    assert not gives_required_params(move, {"item": "k-1", "qty": None})
    assert not gives_required_params(move, {"item": "k-1", "qty": 0})  # given, but invalid
    # a target that requires nothing is never complete this way: nothing came with the phrase
    assert not gives_required_params(REGISTRY.tool("reset_bins"), {})


# ---------------------------------------------------------------- the rule


def test_one_phrase_and_every_required_param_make_a_strong_hit():
    hit = top("move stock item k-1 5 units")
    assert (hit.target, hit.strength, hit.strong_by) == ("move_stock", STRONG_KEYWORDS,
                                                         "complete_params")
    steps = []
    decision = route("move stock item k-1 5 units", steps=steps)
    assert (decision.kind, decision.target, decision.source) == (
        RouteKind.TOOL, "move_stock", RouteSource.RULE)
    assert decision.params == {"item": "k-1", "qty": 5}
    assert decision.confidence == STRONG_KEYWORDS
    assert "every param it requires" in decision.reason
    facts = rule_facts(steps)
    assert (facts["strong_by"], facts["strength"]) == ("complete_params", STRONG_KEYWORDS)


def test_a_missing_or_invalid_required_param_keeps_the_phrase_weak():
    for query, params in (("move stock item k-1", {"item": "k-1"}),
                          ("move stock 5 units", {"qty": 5}),
                          ("move stock item k-1 0 units", {"item": "k-1", "qty": 0})):
        assert (top(query).strength, top(query).strong_by) == (WEAK, None), query
        decision = route(query)
        assert decision.kind is RouteKind.CLARIFY and decision.clarify_options == [
            "move_stock"], query
        assert decision.params == params, query


def test_only_a_write_the_pipeline_holds_for_a_grant_is_lifted():
    # external, declared approval="none": no grant follows it, so routing's own confirmation is
    # the only one it gets
    shipped = route("ship out item k-1 5 units")
    assert shipped.kind is RouteKind.CLARIFY and shipped.clarify_options == ["ship_out"]
    assert top("ship out item k-1 5 units").strength == WEAK
    # a read runs on one keyword anyway; it keeps the strength the reconcile matrix expects
    assert top("count items item k-1 5 units").strength == WEAK
    assert route("count items item k-1 5 units").kind is RouteKind.TOOL
    # a write that requires nothing: the phrase is all the question gave
    reset = route("reset bins")
    assert reset.kind is RouteKind.CLARIFY and reset.clarify_options == ["reset_bins"]


def test_a_tie_at_the_top_is_asked_back_however_complete():
    decision = route("transfer stock item k-1 5 units")
    assert decision.kind is RouteKind.CLARIFY
    assert decision.clarify_options == ["relocate_stock", "transfer_stock"]


def test_a_read_workflow_named_next_to_the_write_params_is_not_lifted():
    hit = top("move plan item k-1 5 units")
    assert (hit.target, hit.strength) == ("move_plan", WEAK)


def test_a_question_is_never_lifted_however_complete():
    """Asking about a write, or whether to make one, is not asking for it: the phrase keeps its
    strength, so routing confirms the write before anything is held for approval."""
    for query in ("did anyone move stock item k-1 5 units", "move stock item k-1 5 units?",
                  "why does move stock item k-1 5 units fail"):
        assert (top(query).strength, top(query).strong_by) == (WEAK, None), query
        steps = []
        decision = route(query, steps=steps)
        assert decision.kind is RouteKind.CLARIFY, query
        assert decision.clarify_options == ["move_stock"], query
        assert decision.params == {"item": "k-1", "qty": 5}, query
        assert rule_facts(steps)["question"] is True, query
    steps = []
    route("please move stock item k-1 5 units", steps=steps)
    assert (rule_facts(steps)["question"], rule_facts(steps)["strong_by"]) == (
        False, "complete_params")


def test_a_negated_write_is_never_lifted_however_complete():
    """Saying not to make a write is not asking for it: with a negator before the phrase, the
    phrase keeps its strength and routing confirms the write before anything is held for
    approval, also when the request reads as no question. A negator after the phrase negates
    something else."""
    for query, question in (("please don't move stock item k-1 5 units", False),
                            ("never move stock item k-1 5 units", False),
                            ("no need to move stock item k-1 5 units", False),
                            ("don't move stock item k-1 5 units", True)):  # also a question
        assert (top(query).strength, top(query).strong_by) == (WEAK, None), query
        steps = []
        decision = route(query, steps=steps)
        assert decision.kind is RouteKind.CLARIFY, query
        assert decision.clarify_options == ["move_stock"], query
        assert decision.params == {"item": "k-1", "qty": 5}, query
        assert (rule_facts(steps)["negated"], rule_facts(steps)["question"]) == (
            True, question), query
    steps = []
    decision = route("move stock item k-1 5 units, no need to hurry", steps=steps)
    assert (decision.kind, decision.target) == (RouteKind.TOOL, "move_stock")
    assert (rule_facts(steps)["negated"], rule_facts(steps)["strong_by"]) == (
        False, "complete_params")


# an external target and a read, each named by two keywords: strong evidence for a command
HELD = build([Plugin(name="held_fixture", version="0.1.0", tools=(
    tool("dispatch_truck", "external", "dispatch", "truck"),
    tool("fleet_report", "read", "fleet", "mileage"),
))])


def held(query: str, steps=None):
    ctx = make_ctx("operator", sink=steps.append if steps is not None else None)
    return decide_route(query, ctx, HELD, MockProvider())


def test_an_external_target_is_capped_like_a_write():
    """Two keywords make a command to an external target strong: it runs at once. A question, a
    negation or an exclusion caps it at weak, and the trace says what made it strong and what
    held it back; a read with two keywords keeps its strength in a question."""
    command = held("dispatch a truck for item k-1 5 units")
    assert (command.kind, command.target) == (RouteKind.TOOL, "dispatch_truck")
    for query, by in (("should we dispatch a truck for item k-1 5 units?", ["question"]),
                      ("please never dispatch a truck: item k-1", ["negated"]),
                      ("dispatch a truck for item k-1 5 units, not item k-2", ["excluded"])):
        steps = []
        decision = held(query, steps)
        assert decision.kind is RouteKind.CLARIFY, query
        assert decision.clarify_options == ["dispatch_truck"], query
        facts = rule_facts(steps)
        assert (facts["strength"], facts["strong_by"]) == (WEAK, None), query
        assert facts["capped"] == {"from": "keywords", "by": by}, query
    steps = []
    read = held("what is the fleet mileage?", steps)
    assert (read.kind, read.target) == (RouteKind.TOOL, "fleet_report")
    assert (rule_facts(steps)["strong_by"], rule_facts(steps)["capped"]) == ("keywords", None)


# ---------------------------------------------------------------- an example, held back

# A write that names itself by its own examples: the query equals one, which is strong evidence
# ("example"). Three of them read as a question, a negation and an exclusion.
EXAMPLES = ("move stock item k-1 5 units", "should we move stock item k-1 5 units?",
            "never move stock item k-1 5 units", "move stock item k-1 5 units, not item k-2")
BY_EXAMPLE = build([Plugin(name="example_fixture", version="0.1.0", tools=(
    tool("move_stock", "write", "move stock", examples=EXAMPLES),))])


def by_example(query: str, steps):
    ctx = make_ctx("operator", sink=steps.append)
    return decide_route(query, ctx, BY_EXAMPLE, MockProvider())


def test_a_write_named_by_its_own_example_is_strong_until_the_request_holds_it_back():
    steps = []
    command = by_example(EXAMPLES[0], steps)
    assert (command.kind, command.target, command.source) == (
        RouteKind.TOOL, "move_stock", RouteSource.RULE)
    assert (rule_facts(steps)["strong_by"], rule_facts(steps)["capped"]) == ("example", None)
    # An example's span is the whole query, so no negator stands before it and "never ..." is
    # held back as an exclusion, not as a negation.
    held = zip(EXAMPLES[1:], (["question"], ["excluded"], ["excluded"]), strict=True)
    for query, by in held:
        steps = []
        decision = by_example(query, steps)
        assert decision.kind is RouteKind.CLARIFY, query
        assert decision.clarify_options == ["move_stock"], query
        facts = rule_facts(steps)
        assert (facts["strength"], facts["strong_by"]) == (WEAK, None), query
        assert facts["capped"] == {"from": "example", "by": by}, query


# ---------------------------------------------------------------- a contested write


class LabelledMoveParams(MoveParams):
    note: str | None = Field(default=None, json_schema_extra={
        "label": {"en": "Remark", "zh": "备注"}})


def read_tool(name: str, description: str, *keywords: str) -> Tool:
    return Tool(name=name, description=description, params_model=NoParams,
                handler=lambda ctx, params: None, roles=("operator",), effect="read",
                approval="none", outputs=ToolOutputs(aggregates=(), columns=()),
                explorable=False, keywords=keywords)


# a write whose note param is labelled, a log whose keywords are that param's key and labels,
# and a report whose keyword names no param of the write
CONTESTED = build([Plugin(name="contest_fixture", version="0.1.0", tools=(
    tool("move_stock", "write", "move stock", params_model=LabelledMoveParams),
    read_tool("remark_log", "Remarks left by operators.", "note", "remark", "备注"),
    read_tool("audit_log", "Audits held by operators.", "audit"),
))])


def contested(query: str):
    return match_rules(query, CONTESTED.candidates(make_ctx("operator")))


def test_param_words_are_every_key_and_label_of_the_params(builtin):
    move = CONTESTED.tool("move_stock")
    assert param_words(move) == {"item", "qty", "note", "remark", "备注"}
    create_po = builtin.registry.tool("create_po")
    words = param_words(create_po, builtin.registry.entities)
    assert {"supplier", "供应商", "supplier id", "supplier_id", "sku", "quantity", "数量"} <= words
    assert "vendor" not in words


def test_a_contest_won_over_names_of_its_params_lifts_a_complete_write():
    """The rival matched the key of a param of the write, or its label in either language:
    the approval card shows what it matched, so the request is as complete as without it."""
    for query in ("move stock item k-1 5 units, note: fragile",
                  "move stock item k-1 5 units, remark: fragile",
                  "move stock item k-1 5 units，备注：易碎"):
        opinion = contested(query)
        assert opinion.contest.winner == "move_stock", query
        assert (opinion.top.strength, opinion.top.strong_by) == (
            STRONG_KEYWORDS, "complete_params"), query


def test_a_contest_won_over_any_other_word_is_confirmed_first():
    """A rival that matched anything else may have asked for something the approval card does
    not show: the write wins the contest but stays weak, also when only one of two rivals did."""
    for query in ("move stock item k-1 5 units after the audit",
                  "move stock item k-1 5 units, note: after the audit"):
        opinion = contested(query)
        assert opinion.contest.winner == "move_stock", query
        assert (opinion.top.strength, opinion.top.strong_by) == (WEAK, None), query
        decision = decide_route(query, make_ctx("operator"), CONTESTED, MockProvider())
        assert decision.kind is RouteKind.CLARIFY, query
        assert decision.clarify_options == ["move_stock"], query


# ---------------------------------------------------------------- the built-in write


def test_a_contest_won_over_the_supplier_label_lifts_it_like_no_contest(builtin):
    """Another report's keyword on another word: the contest settles the target first. The write
    won it by the margin, and the rival matched only "supplier", the label of create_po's
    supplier param, so a complete write is strong exactly as without a contest."""
    query = "create a purchase order for 40 units of SKU-10042 from supplier SUP-003"
    opinion = match_rules(query, builtin.candidates())
    assert opinion.contest is not None and opinion.contest.winner == "create_po"
    assert (opinion.top.strength, opinion.top.strong_by) == (STRONG_KEYWORDS, "complete_params")
    steps = []
    decision = builtin.decide(query, sink=steps.append)
    assert (decision.kind, decision.target, decision.source) == (
        RouteKind.TOOL, "create_po", RouteSource.RULE)
    assert "supplier_compare (supplier)" in decision.reason  # the contest
    assert decision.reason.endswith("the question gives every param it requires")
    facts = rule_facts(steps)
    assert (facts["strong_by"], facts["contest"]["winner"]) == ("complete_params", "create_po")


def test_a_contest_won_over_other_words_is_confirmed_first(builtin):
    """"best supplier" asks for a ranking, "vendor" is no label of the supplier param, and a
    plan was asked for first: the write wins each contest, but routing confirms it before its
    approval."""
    for query in ("create a purchase order for 40 units of SKU-10042 from the best supplier",
                  "create a purchase order for 40 units of SKU-10042 from vendor SUP-003",
                  "before we raise a PO for 60 units of SKU-10057 I want a reorder plan"):
        opinion = match_rules(query, builtin.candidates())
        assert opinion.contest.winner == "create_po", query
        assert (opinion.top.strength, opinion.top.strong_by) == (WEAK, None), query
        decision = builtin.decide(query)
        assert decision.kind is RouteKind.CLARIFY, query
        assert decision.clarify_options == ["create_po"], query


def test_a_contest_no_target_won_or_a_question_is_never_lifted(builtin):
    close = match_rules("缺货的 SKU-10057 下单 80 件", builtin.candidates())
    assert close.contest.winner is None and set(close.tied) == {"create_po", "low_stock"}
    assert (close.top.strength, close.top.strong_by) == (WEAK, None)
    query = "I need to raise a PO for 30 units of SKU-10061, which supplier is fastest"
    asked = match_rules(query, builtin.candidates())
    assert asked.contest.winner == "create_po" and asked.question
    assert (asked.top.strength, asked.top.strong_by) == (WEAK, None)
    decision = builtin.decide(query)
    assert decision.kind is RouteKind.CLARIFY and decision.clarify_options == ["create_po"]


def test_a_question_keeps_the_strength_it_has(builtin):
    """A read is never held back: a question with two keywords of a report is as strong as
    ever, and runs it."""
    opinion = match_rules("which deliveries are late?", builtin.candidates())
    assert opinion.question
    assert (opinion.top.strength, opinion.top.strong_by) == (STRONG_KEYWORDS, "keywords")
    decision = builtin.decide("which deliveries are late?")
    assert (decision.kind, decision.target) == (RouteKind.TOOL, "late_shipments")


def test_a_bare_verb_with_every_param_never_wins_the_write(builtin):
    for query in ("create 40 units of SKU-10042", "新建 SKU-10042 数量 40"):
        decision = builtin.decide(query)
        assert not (decision.kind is RouteKind.TOOL and decision.target == "create_po"), query
        assert decision.source is not RouteSource.RULE, query  # BM25 at most, then confirmed


def _with_model(builtin, target, confidence):
    provider = ScriptedProvider(route=LLMRoute(target=target, confidence=confidence))
    return builtin.decide("create a purchase order for 40 units of SKU-10042", provider)


def test_with_a_model_the_complete_write_takes_the_strong_row_of_the_matrix(builtin):
    agreed = _with_model(builtin, "create_po", 0.5)
    assert (agreed.kind, agreed.target, agreed.source) == (
        RouteKind.TOOL, "create_po", RouteSource.RULE)
    outweighed = _with_model(builtin, "po_status", 0.5)  # a weak model opinion
    assert (outweighed.kind, outweighed.target) == (RouteKind.TOOL, "create_po")
    disputed = _with_model(builtin, "po_status", 0.9)  # a strong one: asked back
    assert disputed.kind is RouteKind.CLARIFY
    assert disputed.clarify_options == ["create_po", "po_status"]


def test_strong_by_says_why_in_the_trace(builtin):
    def strong_by(query):
        steps = []
        builtin.decide(query, sink=steps.append)
        return rule_facts(steps)["strong_by"]

    assert strong_by("show open purchase orders") == "example"  # po_status's own example
    assert strong_by("show the late shipments") == "keywords"
    assert strong_by("create a purchase order for 40 units of SKU-10042") == "complete_params"
    assert strong_by("create a purchase order for SKU-10042") is None  # weak


# ---------------------------------------------------------------- held writes, built-in

HELD_WRITES = (
    # two phrases of create_po: strong by keywords, no lift involved
    ("别下单，也不要新建采购单：SKU-10042 数量 200", "keywords", ["negated"]),
    ("did we already place an order and raise a PO for SKU-10042?", "keywords", ["question"]),
    # a complete write, and a supplier it rules out
    ("给 SKU-10042 下单 50 件，不要用 SUP-003", "complete_params", ["excluded"]),
)


@pytest.mark.parametrize("query, strong_by, holds", HELD_WRITES)
def test_a_held_write_asks_first_however_it_was_strong(builtin, query, strong_by, holds):
    """However the write was named (two of its phrases, or one with every param), a negation,
    a question or an exclusion caps it: routing confirms it, the trace says what made it strong
    and what held it, and through the whole graph no approval is issued and nothing is
    written. An excluded supplier is no param of the write."""
    steps = []
    decision = builtin.decide(query, sink=steps.append)
    assert (decision.kind, decision.clarify_options) == (RouteKind.CLARIFY, ["create_po"])
    assert "supplier_id" not in decision.params
    facts = rule_facts(steps)
    assert (facts["target"], facts["strength"], facts["strong_by"]) == ("create_po", WEAK, None)
    assert facts["capped"] == {"from": strong_by, "by": holds}
    before = builtin.store.count("purchase_order")
    response = builtin.ask(query)
    assert response.grant is None and response.error is None, response.answer
    assert builtin.store.count("purchase_order") == before


@pytest.mark.parametrize("confidence", [0.5, 0.95])
def test_a_model_that_agrees_does_not_make_a_held_write_strong(builtin, confidence):
    """Both arms naming the write is strong evidence for a command, not for a request that holds
    the write back: at any confidence it is still confirmed first."""
    provider = ScriptedProvider(route=LLMRoute(target="create_po", confidence=confidence))
    for query, _, _ in HELD_WRITES:
        decision = builtin.decide(query, provider)
        assert (decision.kind, decision.clarify_options) == (RouteKind.CLARIFY, ["create_po"])
        assert decision.source is RouteSource.RULE, query
    agreed = _with_model(builtin, "create_po", confidence)  # a command: straight to the write
    assert (agreed.kind, agreed.target, agreed.source) == (
        RouteKind.TOOL, "create_po", RouteSource.RULE)


# a write tied with a read that ranks above it, and a negator between their phrases
TIED_BELOW_A_READ = "SKU-10057 缺货了，但先别下单：数量 80"


def test_a_negated_write_tied_below_a_read_is_asked_back(builtin):
    opinion = match_rules(TIED_BELOW_A_READ, builtin.candidates())
    assert opinion.top.target == "low_stock"
    assert set(opinion.tied) == {"low_stock", "create_po"}
    decision = builtin.decide(TIED_BELOW_A_READ)
    assert decision.kind is RouteKind.CLARIFY
    assert set(decision.clarify_options) == {"low_stock", "create_po"}


def test_a_negated_write_tied_below_a_read_and_picked_by_the_model(builtin):
    """rule.negated reads a negator before the phrase of the top hit and of every hit tied with
    it, so a model that picks the negated write gets a confirmation, not a straight route."""
    provider = ScriptedProvider(route=LLMRoute(target="create_po", confidence=0.95))
    decision = builtin.decide(TIED_BELOW_A_READ, provider)
    assert not (decision.kind is RouteKind.TOOL and decision.target == "create_po")
