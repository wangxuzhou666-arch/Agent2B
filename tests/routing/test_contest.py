"""Rule hits on different words of one question: a contest settled by meaning, not by length.

See core/routing/rule_arm.py. Built-in tools over seeded data (``builtin``, conftest.py), plus
the fixture registry of tests/fakes.py for the reconcile cells.
"""

import pytest

from tests.fakes import REGISTRY, NoParams, ScriptedProvider, make_ctx
from twotrack.core import RouteKind, RouteSource, Tool, ToolOutputs
from twotrack.core.routing import (
    STRONG_KEYWORDS,
    WEAK,
    Contest,
    LLMOpinion,
    RuleHit,
    RuleOpinion,
    match_rules,
    reconcile,
)
from twotrack.core.routing.semantic import document, evidence_document
from twotrack.core.text import phrase_in, phrase_spans, tokens
from twotrack.providers import LLMRoute

# ---------------------------------------------------------------- spans


@pytest.mark.parametrize(("phrase", "text", "spans"), [
    ("late", "late purchase orders", [(0, 4)]),
    ("purchase orders", "Late Purchase-Orders!", [(5, 20)]),  # normalized text positions
    ("late", "a late, later, late order", [(2, 6), (13, 17)]),  # whole words only
    ("采购单", "哪些采购单逾期, 采购单", [(2, 5), (8, 11)]),  # CJK: substrings
    ("aa", "aa aa aa", [(0, 2), (3, 5), (6, 8)]),
    ("", "anything", []),
])
def test_phrase_spans_agree_with_phrase_in(phrase, text, spans):
    assert phrase_spans(phrase, text) == spans
    assert phrase_in(phrase, text) is bool(spans)


@pytest.mark.parametrize(("text", "terms"), [
    ("the late purchase orders", ["late", "purchase", "order"]),  # English stopwords
    ("延迟的采购订单", ["延迟", "采购", "购订", "订单"]),  # no "迟的" / "的采"
    ("缺货和逾期了", ["缺货", "逾期"]),
    ("草稿或已下达与已发货", ["草稿", "已下", "下达", "已发", "发货"]),
    ("的了和", []),
    ("我的", ["我"]),  # a one-character piece is kept as it is
])
def test_chinese_particles_and_conjunctions_make_no_index_terms(text, terms):
    """A Chinese stop character splits a run before bigrams are made, as an English stopword
    is dropped: the grammar of a sentence is no evidence of what it is about."""
    assert tokens(text) == terms


# ---------------------------------------------------------------- which hits contest


def test_a_contained_match_is_not_a_contest(builtin):
    opinion = match_rules("please create a purchase order for 75 units of SKU-10205",
                          builtin.candidates())
    assert opinion.contest is None
    # one phrase (create_po declares no bare verb that would count twice), strong only because
    # the question also gives every param the write requires; without the quantity it is weak
    assert opinion.top.matched == ("create a purchase order",)
    assert (opinion.top.target, opinion.top.strength, opinion.top.strong_by) == (
        "create_po", STRONG_KEYWORDS, "complete_params")
    assert "po_status" in [h.target for h in opinion.hits]  # matched, but explained away
    partial = match_rules("please create a purchase order for SKU-10205", builtin.candidates())
    assert partial.contest is None
    assert (partial.top.target, partial.top.strength, partial.top.strong_by) == (
        "create_po", WEAK, None)
    late = match_rules("show the late shipments", builtin.candidates())
    assert late.contest is None and late.top.strength == STRONG_KEYWORDS  # "late" + phrase
    assert late.top.strong_by == "keywords"


def test_hits_on_different_words_contest_and_the_more_specific_report_wins(builtin):
    opinion = match_rules("late purchase orders", builtin.candidates())
    assert opinion.contest.winner == "late_shipments"
    assert opinion.contest.contenders == ("late_shipments", "po_status")
    (_, best), (_, other) = opinion.contest.scores
    assert best > 0 and other == 0  # po_status's evidence never mentions lateness
    assert (opinion.top.target, opinion.top.strength) == ("late_shipments", WEAK)
    assert opinion.tied == ()


def test_a_contested_hit_is_a_weak_signal_however_many_keywords_it_matched(builtin):
    opinion = match_rules("which purchase orders are overdue", builtin.candidates())
    assert opinion.contest.winner == "late_shipments"
    assert opinion.top.strength == WEAK  # "overdue" and "are overdue" made it strong before


def test_a_contest_no_report_wins_is_asked_back(builtin):
    steps = []
    decision = builtin.decide("low stock and late shipments", sink=steps.append)
    assert decision.kind is RouteKind.CLARIFY and decision.source is RouteSource.RULE
    assert set(decision.clarify_options) == {"low_stock", "late_shipments"}
    assert "different words" in decision.reason
    rule = next(s for s in steps if s.step == "understand").facts["rule"]
    assert rule["contest"]["winner"] is None
    assert set(rule["tied"]) == {"low_stock", "late_shipments"}


def test_the_contest_is_in_the_trace(builtin):
    steps = []
    decision = builtin.decide("late purchase orders", sink=steps.append)
    assert (decision.kind, decision.target, decision.confidence) == (
        RouteKind.TOOL, "late_shipments", WEAK)
    assert "po_status (purchase orders)" in decision.reason
    contest = next(s for s in steps if s.step == "understand").facts["rule"]["contest"]
    assert contest["winner"] == "late_shipments"
    assert list(contest["scores"]) == ["late_shipments", "po_status"]


# ---------------------------------------------------------------- what counts as evidence


def test_route_hints_are_not_evidence_and_params_are(builtin):
    po_status = builtin.registry.tool("po_status")
    assert "overdue" in document(po_status)  # its route hint: "for overdue ones use ..."
    assert "overdue" not in evidence_document(po_status)
    late = builtin.registry.tool("late_shipments")
    assert "supplier" in evidence_document(late)  # it takes a supplier_id
    decision = builtin.decide("late shipments by supplier")
    assert (decision.kind, decision.target) == (RouteKind.TOOL, "late_shipments")


def test_evidence_speaks_every_language_the_target_declares(builtin):
    """Its description in each language (once, for a plain string), and each param by name
    and by its label in another language, which a Chinese question uses."""
    registry = builtin.registry
    late = registry.tool("late_shipments")
    terms = evidence_document(late, registry.entities)
    assert {"overdue", "逾期", "采购"} <= set(terms)  # keyword, Chinese description
    assert {"供应", "应商"} <= set(terms)  # supplier_id as 供应商编码
    assert "供应" not in evidence_document(late)  # without entities: names only
    plain = Tool(name="plain", description="Count parts.", params_model=NoParams,
                 handler=lambda ctx, params: None, roles=("viewer",), effect="read",
                 approval="none", outputs=ToolOutputs(aggregates=(), columns=()),
                 explorable=False)
    assert evidence_document(plain).count("count") == 1  # a plain string counts once
    assert document(plain).count("part") == 1


# ---------------------------------------------------------------- with a model opinion


def _with_model(builtin, query, target, confidence):
    return builtin.decide(query, ScriptedProvider(route=LLMRoute(target=target,
                                                                 confidence=confidence)))


def test_a_model_that_agrees_with_the_contest_winner_runs_it(builtin):
    decision = _with_model(builtin, "late purchase orders", "late_shipments", 0.5)
    assert (decision.kind, decision.target, decision.source) == (
        RouteKind.TOOL, "late_shipments", RouteSource.RULE)


def test_a_strong_model_overrules_a_contest_winner_with_a_confirmation(builtin):
    decision = _with_model(builtin, "late purchase orders", "po_status", 0.9)
    assert decision.kind is RouteKind.CLARIFY and decision.source is RouteSource.LLM_CONFIRM
    assert decision.clarify_options == ["po_status", "late_shipments"]


def test_a_weak_model_against_a_contest_winner_is_asked_back(builtin):
    decision = _with_model(builtin, "late purchase orders", "po_status", 0.5)
    assert decision.kind is RouteKind.CLARIFY
    assert decision.clarify_options == ["late_shipments", "po_status"]


def test_a_model_settles_a_contest_the_keywords_could_not(builtin):
    decision = _with_model(builtin, "low stock and late shipments", "low_stock", 0.5)
    assert (decision.kind, decision.target) == (RouteKind.TOOL, "low_stock")
    other = _with_model(builtin, "low stock and late shipments", "inventory_lookup", 0.9)
    assert other.kind is RouteKind.CLARIFY
    assert set(other.clarify_options) == {"low_stock", "late_shipments", "inventory_lookup"}


# ---------------------------------------------------------------- writes


CANDIDATES = REGISTRY.candidates(make_ctx("operator"))


def test_a_contested_write_is_never_run_on_the_keywords_alone():
    hit = RuleHit("stock_adjust", WEAK, ("adjust stock",))
    contest = Contest((("stock_adjust", 0.5), ("order_status", 0.1)), winner="stock_adjust")
    decision = reconcile(RuleOpinion((hit, RuleHit("order_status", WEAK, ("open orders",))),
                                     contest), LLMOpinion(), CANDIDATES)
    assert decision.kind is RouteKind.CLARIFY and decision.clarify_options == ["stock_adjust"]


def test_a_write_matched_beside_another_report_does_not_run(builtin):
    decision = builtin.decide("create a purchase order for the late items")
    assert not (decision.kind is RouteKind.TOOL and decision.target == "create_po")
