"""One case per cell of the reconcile matrix (see core/routing/reconcile.py docstring)."""

import pytest

from tests.fakes import REGISTRY, make_ctx
from twotrack.core import Candidates, RouteKind, RouteSource
from twotrack.core.routing import (
    STRONG_KEYWORDS,
    WEAK,
    LLMOpinion,
    RuleHit,
    RuleOpinion,
    SemanticResult,
    fallback,
    reconcile,
)

CANDIDATES = REGISTRY.candidates(make_ctx("operator"))
SILENT = LLMOpinion()


def strong(name: str, **params) -> RuleHit:
    return RuleHit(name, STRONG_KEYWORDS, ("kw one", "kw two"), params)


def weak(name: str, **params) -> RuleHit:
    return RuleHit(name, WEAK, ("kw",), params)


def llm(name: str, confidence: float, **params) -> LLMOpinion:
    return LLMOpinion(name, confidence, params)


def tied(*names: str) -> RuleOpinion:
    return RuleOpinion(tuple(weak(n) for n in names))


GO, ASK = "go", "ask"

# (cell id, rule opinion, llm opinion, expected kind, source, target, options)
MATRIX = [
    ("rule_weak__llm_silent", RuleOpinion((weak("order_status"),)), SILENT,
     GO, RouteSource.RULE, "order_status", []),
    ("rule_strong__llm_silent", RuleOpinion((strong("order_status"),)), SILENT,
     GO, RouteSource.RULE, "order_status", []),
    ("rule_weak_write__llm_silent__gated", RuleOpinion((weak("stock_adjust"),)), SILENT,
     ASK, RouteSource.RULE, "stock_adjust", ["stock_adjust"]),
    ("rule_strong_write__llm_silent__not_gated", RuleOpinion((strong("stock_adjust"),)), SILENT,
     GO, RouteSource.RULE, "stock_adjust", []),
    ("rule_weak__llm_same", RuleOpinion((weak("order_status"),)), llm("order_status", 0.4),
     GO, RouteSource.RULE, "order_status", []),
    ("rule_strong__llm_same", RuleOpinion((strong("order_status"),)), llm("order_status", 0.9),
     GO, RouteSource.RULE, "order_status", []),
    ("agree_on_write__not_gated", RuleOpinion((weak("stock_adjust"),)), llm("stock_adjust", 0.9),
     GO, RouteSource.RULE, "stock_adjust", []),
    # a request that holds the write back (a question, a negation, an exclusion): agreeing is no
    # strong evidence for it, so the write is confirmed; a read is not held
    ("agree_on_write__question__gated", RuleOpinion((weak("stock_adjust"),), question=True),
     llm("stock_adjust", 0.95), ASK, RouteSource.RULE, "stock_adjust", ["stock_adjust"]),
    ("agree_on_write__negated__gated", RuleOpinion((weak("stock_adjust"),), negated=True),
     llm("stock_adjust", 0.95), ASK, RouteSource.RULE, "stock_adjust", ["stock_adjust"]),
    ("agree_on_write__excluded__gated", RuleOpinion((weak("stock_adjust"),), excluded=True),
     llm("stock_adjust", 0.95), ASK, RouteSource.RULE, "stock_adjust", ["stock_adjust"]),
    ("agree_on_read__question__not_gated", RuleOpinion((weak("order_status"),), question=True),
     llm("order_status", 0.9), GO, RouteSource.RULE, "order_status", []),
    ("rule_silent__llm_strong", RuleOpinion(), llm("order_status", 0.85),
     ASK, RouteSource.LLM_CONFIRM, "order_status", ["order_status"]),
    ("rule_silent__llm_very_high_read_only", RuleOpinion(), llm("order_status", 0.95),
     GO, RouteSource.LLM, "order_status", []),
    ("rule_silent__llm_very_high_write", RuleOpinion(), llm("stock_adjust", 0.95),
     ASK, RouteSource.LLM_CONFIRM, "stock_adjust", ["stock_adjust"]),
    ("rule_silent__llm_weak", RuleOpinion(), llm("order_status", 0.5),
     ASK, RouteSource.LLM_CONFIRM, "order_status", ["order_status"]),
    ("rule_weak__llm_strong_differs", RuleOpinion((weak("order_status"),)),
     llm("lead_time", 0.85), ASK, RouteSource.LLM_CONFIRM, None, ["lead_time", "order_status"]),
    ("rule_weak__llm_weak_differs", RuleOpinion((weak("order_status"),)), llm("lead_time", 0.5),
     ASK, RouteSource.LLM, None, ["order_status", "lead_time"]),
    ("rule_strong__llm_strong_differs", RuleOpinion((strong("order_status"),)),
     llm("lead_time", 0.9), ASK, RouteSource.LLM, None, ["order_status", "lead_time"]),
    ("rule_strong__llm_weak_differs", RuleOpinion((strong("order_status"),)),
     llm("lead_time", 0.5), GO, RouteSource.RULE, "order_status", []),
    ("rule_tied__llm_silent", tied("delivery_delay", "lead_time"), SILENT,
     ASK, RouteSource.RULE, None, ["delivery_delay", "lead_time"]),
    ("rule_tied__llm_picks_one", tied("delivery_delay", "lead_time"), llm("lead_time", 0.5),
     GO, RouteSource.RULE, "lead_time", []),
    ("rule_tied__llm_other", tied("delivery_delay", "lead_time"), llm("order_status", 0.9),
     ASK, RouteSource.LLM, None, ["delivery_delay", "lead_time", "order_status"]),
    ("rule_tied__question__llm_picks_write__gated",
     RuleOpinion((weak("order_status"), weak("stock_adjust")), question=True),
     llm("stock_adjust", 0.9), ASK, RouteSource.RULE, "stock_adjust", ["stock_adjust"]),
]


@pytest.mark.parametrize(("rule", "opinion", "outcome", "source", "target", "options"),
                         [m[1:] for m in MATRIX], ids=[m[0] for m in MATRIX])
def test_reconcile_cell(rule, opinion, outcome, source, target, options):
    decision = reconcile(rule, opinion, CANDIDATES)
    expected_kind = RouteKind.CLARIFY if outcome == ASK else (
        RouteKind.WORKFLOW if target in CANDIDATES.workflows else RouteKind.TOOL)
    assert decision.kind is expected_kind
    assert decision.source is source
    assert decision.target == target
    assert decision.clarify_options == options


def test_both_arms_silent_defers_to_fallback():
    assert reconcile(RuleOpinion(), SILENT, CANDIDATES) is None


def test_agreement_merges_params_with_rule_extraction_winning():
    decision = reconcile(RuleOpinion((weak("stock_level", item="from-rule"),)),
                         llm("stock_level", 0.6, item="from-model", site="north"), CANDIDATES)
    assert decision.params == {"item": "from-rule", "site": "north"}


def test_llm_confirm_keeps_params_for_one_click_confirmation():
    decision = reconcile(RuleOpinion(), llm("stock_level", 0.85, item="x-9"), CANDIDATES)
    assert decision.params == {"item": "x-9"}


def test_workflow_targets_route_as_workflow_kind():
    decision = reconcile(RuleOpinion((strong("reorder_plan"),)), SILENT, CANDIDATES)
    assert decision.kind is RouteKind.WORKFLOW


# ---------------------------------------------------------------- fallback cells

FALLBACK = [
    ("semantic_hit_read", SemanticResult("order_status", "order_status", 0.7), True,
     RouteKind.TOOL, RouteSource.SEMANTIC),
    ("semantic_hit_write__gated", SemanticResult("stock_adjust", "stock_adjust", 0.7), True,
     RouteKind.CLARIFY, RouteSource.SEMANTIC),
    ("semantic_miss__agent_on", SemanticResult(None, "order_status", 0.2), True,
     RouteKind.OPEN_TASK, RouteSource.AGENT),
    # no arm picked anything: the chat fallback is not a BM25 decision
    ("semantic_miss__agent_off", SemanticResult(None, "order_status", 0.2), False,
     RouteKind.CHAT, RouteSource.FALLBACK),
]


@pytest.mark.parametrize(("semantic", "agent", "kind", "source"), [f[1:] for f in FALLBACK],
                         ids=[f[0] for f in FALLBACK])
def test_fallback_cell(semantic, agent, kind, source):
    decision = fallback("q", semantic, CANDIDATES, allow_open_task=agent)
    assert (decision.kind, decision.source) == (kind, source)


def test_open_task_needs_at_least_one_candidate():
    empty = Candidates(tools={}, workflows={})
    decision = fallback("q", SemanticResult(None, None, 0.0), empty, allow_open_task=True)
    assert decision.kind is RouteKind.CHAT
