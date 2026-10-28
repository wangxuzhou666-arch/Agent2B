"""Two-arm routing: a deterministic rule arm and an LLM arm, reconciled; BM25 fallback; then
the scope guard (a question's constraints must be ones the routed target can apply)."""

from .exclusion import excluded_at, excludes
from .interrogative import is_question
from .llm_arm import LLM_MIN, LLM_STRONG, LLMOpinion, ask_llm, tool_card, tool_cards
from .negation import negates
from .reconcile import LLM_AUTO_ACCEPT, fallback, gate_writes, reconcile
from .router import decide_route, unavailable_reason
from .rule_arm import (
    CONTEST_MARGIN,
    STRONG_EXAMPLE,
    STRONG_KEYWORDS,
    WEAK,
    Contest,
    RuleHit,
    RuleOpinion,
    match_rules,
)
from .scope_guard import MAX_ALTERNATIVES, apply_scope, mark_unfiltered
from .semantic import SEMANTIC_THRESHOLD, SemanticResult, semantic_match

__all__ = [
    "CONTEST_MARGIN",
    "LLM_AUTO_ACCEPT",
    "LLM_MIN",
    "LLM_STRONG",
    "MAX_ALTERNATIVES",
    "SEMANTIC_THRESHOLD",
    "STRONG_EXAMPLE",
    "STRONG_KEYWORDS",
    "WEAK",
    "Contest",
    "LLMOpinion",
    "RuleHit",
    "RuleOpinion",
    "SemanticResult",
    "apply_scope",
    "ask_llm",
    "decide_route",
    "excluded_at",
    "excludes",
    "fallback",
    "gate_writes",
    "is_question",
    "mark_unfiltered",
    "match_rules",
    "negates",
    "reconcile",
    "semantic_match",
    "tool_card",
    "tool_cards",
    "unavailable_reason",
]
