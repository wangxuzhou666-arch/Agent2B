"""decide_route end to end: candidates-only, explicit calls, arms, fallback, trace."""

from tests.fakes import REGISTRY, ScriptedProvider, make_ctx
from twotrack.core import RouteKind, RouteSource, TraceStatus
from twotrack.core.routing import (
    STRONG_EXAMPLE,
    WEAK,
    ask_llm,
    decide_route,
    match_rules,
    semantic_match,
    tool_cards,
)
from twotrack.providers import LLMRoute, MockProvider


def route(query, *roles, provider=None, steps=None, **kw):
    sink = steps.append if steps is not None else None
    ctx = make_ctx(*roles, sink=sink, **kw.pop("facts", {}))
    return decide_route(query, ctx, REGISTRY, provider or MockProvider(), **kw)


# ---------------------------------------------------------------- rule arm


def test_exact_example_is_strong_and_single_keyword_is_weak():
    candidates = REGISTRY.candidates(make_ctx("operator"))
    assert match_rules("What is our stock level?", candidates).top.strength == STRONG_EXAMPLE
    assert match_rules("anything on hand today", candidates).top.strength == WEAK


def test_keywords_match_whole_words_only():
    candidates = REGISTRY.candidates(make_ctx("operator"))
    assert match_rules("the vendors list", REGISTRY.candidates(
        make_ctx("operator", vendor_file=True))).top is None
    assert match_rules("orders statuses", candidates).top is None


def test_rule_arm_runs_param_extractor():
    decision = route("stock level for item k-77", "viewer")
    assert (decision.source, decision.target) == (RouteSource.RULE, "stock_level")
    assert decision.params == {"item": "k-77"}


def test_mock_provider_routes_by_rule_alone():
    decision = route("show open orders", "viewer")
    assert (decision.kind, decision.target) == (RouteKind.TOOL, "order_status")


# ---------------------------------------------------------------- candidates only


def test_hallucinated_llm_target_is_dropped_and_recorded_as_invalid():
    steps = []
    provider = ScriptedProvider(route=LLMRoute(target="make_coffee", confidence=0.99))
    decision = route("show open orders", "viewer", provider=provider, steps=steps)
    assert (decision.source, decision.target) == (RouteSource.RULE, "order_status")
    understand = next(s for s in steps if s.step == "understand")
    assert understand.facts["llm"]["target"] == "invalid"
    assert "make_coffee" not in str(understand.facts)


def test_target_hidden_by_role_is_never_routed_even_if_llm_names_it():
    provider = ScriptedProvider(route=LLMRoute(target="admin_purge", confidence=0.99))
    decision = route("purge archived records now", "operator", provider=provider)
    assert decision.target != "admin_purge"
    assert "admin_purge" not in provider.payloads("route")


def test_target_hidden_by_precondition_is_never_routed():
    provider = ScriptedProvider(route=LLMRoute(target="vendor_lookup", confidence=0.99))
    decision = route("which supplier is cheapest", "viewer", provider=provider)
    assert decision.target is None and "vendor_lookup" not in decision.clarify_options
    assert "vendor_lookup" not in provider.payloads("route")
    unlocked = route("which supplier is cheapest", "viewer", facts={"vendor_file": "f1"})
    assert unlocked.target == "vendor_lookup"


def test_tool_cards_are_built_from_candidates_sorted():
    cards = tool_cards(REGISTRY.candidates(make_ctx("viewer")))
    names = [c.name for c in cards]
    assert names == sorted(names) and "stock_adjust" not in names
    stock = next(c for c in cards if c.name == "stock_level")
    assert "item" in stock.params_schema["properties"]


def test_provider_failure_counts_as_silence():
    provider = ScriptedProvider(fail={"route"})
    opinion = ask_llm("show open orders", REGISTRY.candidates(make_ctx("viewer")), provider)
    assert opinion.target is None and opinion.failed
    assert route("show open orders", "viewer", provider=provider).target == "order_status"


def test_llm_below_minimum_confidence_is_silence():
    provider = ScriptedProvider(route=LLMRoute(target="order_status", confidence=0.1))
    assert route("zzz unrelated words", "viewer", provider=provider).kind is RouteKind.CHAT


# ---------------------------------------------------------------- explicit target


def test_explicit_target_in_candidates_skips_the_arms():
    provider = ScriptedProvider(route=LLMRoute(target="lead_time", confidence=0.99))
    decision = route("anything", "viewer", provider=provider, explicit_target="order_status",
                     explicit_params={"x": 1})
    assert (decision.source, decision.target, decision.params) == (
        RouteSource.EXPLICIT, "order_status", {"x": 1})
    assert provider.received == []


def test_explicit_target_outside_role_is_blocked_without_revealing_it():
    steps = []
    decision = route("purge", "viewer", explicit_target="admin_purge", steps=steps)
    assert decision.kind is RouteKind.CLARIFY and decision.target is None
    gate = next(s for s in steps if s.step == "gate")
    assert gate.status is TraceStatus.BLOCKED
    unknown = route("x", "viewer", explicit_target="no_such_tool")
    assert decision.reason.replace("admin_purge", "?") == unknown.reason.replace(
        "no_such_tool", "?")


def test_explicit_target_with_unmet_precondition_says_what_is_missing():
    decision = route("vendors", "viewer", explicit_target="vendor_lookup")
    assert decision.kind is RouteKind.CLARIFY
    assert "Upload a vendor file first." in decision.reason


# ---------------------------------------------------------------- semantic fallback


def test_semantic_fallback_hit_and_miss():
    candidates = REGISTRY.candidates(make_ctx("viewer"))
    hit = semantic_match("current quantity per item across our sites", candidates)
    assert hit.target == "stock_level"
    miss = semantic_match("write me a poem about the sea", candidates)
    assert miss.target is None


def test_semantic_fallback_used_only_when_both_arms_silent():
    steps = []
    decision = route("current quantity per item across our sites", "viewer", steps=steps)
    assert (decision.source, decision.target) == (RouteSource.SEMANTIC, "stock_level")
    understand = next(s for s in steps if s.step == "understand")
    assert understand.facts["semantic"]["best"] == "stock_level"


def test_no_match_goes_to_open_task_only_when_enabled():
    assert route("write a poem about the sea", "viewer").kind is RouteKind.CHAT
    decision = route("write a poem about the sea", "viewer", allow_open_task=True)
    assert (decision.kind, decision.source) == (RouteKind.OPEN_TASK, RouteSource.AGENT)


def test_write_intent_never_auto_routes_to_write_tool():
    steps = []
    decision = route("adjust stock please", "operator", steps=steps)
    assert decision.kind is RouteKind.CLARIFY and decision.clarify_options == ["stock_adjust"]
    semantic = route("record a quantity correction for an item", "operator")
    assert semantic.kind is RouteKind.CLARIFY


def test_trace_records_candidates_and_understanding():
    steps = []
    route("show open orders", "viewer", steps=steps)
    assert [s.step for s in steps] == ["candidates", "understand"]
    assert "stock_adjust" not in steps[0].facts["names"]
    assert steps[1].facts["rule"]["target"] == "order_status"


def test_param_extractor_must_be_callable():
    from tests.fakes import tool

    assert any("param_extractor" in p for p in tool("t_x", param_extractor="nope").problems())


def test_failing_or_non_json_extractor_yields_no_params():
    from tests.fakes import tool
    from twotrack.core.routing.rule_arm import extract_params

    assert extract_params(tool("t_x", param_extractor=lambda q: 1 / 0), "q") == {}
    assert extract_params(tool("t_x", param_extractor=lambda q: {"d": object()}), "q") == {}
