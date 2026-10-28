"""decide_route: the single place that decides what a query runs.

Order: candidates -> explicit target (if given) -> rule arm + LLM arm -> reconcile ->
fallback (semantic -> open_task -> chat) -> scope guard. Every arm only ever sees the candidate
set, so a target hidden by role or precondition cannot be routed to, whatever the model says.
The scope guard (``scope_guard.apply_scope``) fills bound params from the question, and turns a
tool or workflow that cannot apply a constraint the question names into a clarification.

Trace steps emitted through ``ctx.emit``:
* ``candidates`` - which targets this principal may reach right now;
* ``gate`` (blocked) - an explicit target outside the candidate set;
* ``understand`` - each arm's opinion and the arms' decision;
* ``scope`` - the constraints the question names and which the target applies (only when it
  names one);
* ``handover`` - the request goes to exploration (``open_task``) because no fixed flow covers
  it: reason ``intent_not_covered`` and its evidence (core.explore.handover).
"""

import time

from pydantic import JsonValue

from twotrack.providers.base import Provider

from ..contract import RouteDecision, RouteKind, RouteSource, ToolContext, TraceStatus, TraceStep
from ..explore.handover import handover_step
from ..registry import Candidates, Registry
from .llm_arm import ask_llm
from .reconcile import fallback, go, reconcile
from .rule_arm import RuleOpinion, extract_params, match_rules
from .scope_guard import apply_scope, split_ack, without_ack
from .semantic import SEMANTIC_THRESHOLD, SemanticResult, semantic_match


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def unavailable_reason(name: str, registry: Registry, ctx: ToolContext) -> str:
    """Why an explicit target is not a candidate. Unknown and forbidden read the same, so the
    message never reveals which targets exist for other roles."""
    if name in registry.tools or name in registry.workflows:
        target = registry.target(name)
        if target.allows(ctx.principal):
            needs = [registry.preconditions[p].description for p in registry.unmet(target, ctx)]
            if needs:
                return f"'{name}' cannot run yet: " + " ".join(needs)
    return f"'{name}' is not available to you."


def _explicit(query: str, name: str, params: dict[str, JsonValue] | None,
              candidates: Candidates, registry: Registry, ctx: ToolContext) -> RouteDecision:
    if name in candidates:
        target = candidates.get(name)
        chosen = dict(params) if params is not None else extract_params(target, query)
        return go(name, candidates, RouteSource.EXPLICIT, 1.0, chosen, "explicitly selected")
    reason = unavailable_reason(name, registry, ctx)
    known = name in registry.tools or name in registry.workflows
    ctx.emit(TraceStep(step="gate", status=TraceStatus.BLOCKED, source=RouteSource.EXPLICIT,
                       facts={"target": name if known else "unknown", "reason": reason}))
    return RouteDecision(kind=RouteKind.CLARIFY, confidence=1.0, source=RouteSource.EXPLICIT,
                         reason=reason)


def _facts(decision: RouteDecision, rule: RuleOpinion | None, llm_audit: dict | None,
           semantic: SemanticResult | None) -> dict[str, JsonValue]:
    top = rule.top if rule else None
    return {
        "kind": decision.kind.value,
        "target": decision.target,
        "confidence": round(decision.confidence, 3),
        "options": list(decision.clarify_options),
        "reason": decision.reason,
        "rule": None if rule is None else {
            "target": top.target if top else None,
            "strength": top.strength if top else 0.0,
            # why the top hit is strong: "example", "keywords", or "complete_params" (one
            # phrase of a write, with every param it requires); None when weak
            "strong_by": top.strong_by if top and top.strong else None,
            "matched": list(top.matched) if top else [],
            # the request reads as a question (interrogative.is_question), so no write it
            # names completely is lifted to strong
            "question": rule.question,
            # a negator stands before the phrase of the top hit or of a hit tied with it
            # (negation.negates), so a write either names is confirmed first
            "negated": rule.negated,
            # the request excludes a value ("not SUP-003", exclusion.excludes)
            "excluded": rule.excluded,
            # a target that changes data was strong (from: example, keywords or complete_params)
            # and is capped at weak because the request holds it back (by: question, negated,
            # excluded; rule_arm._hold), so routing confirms it first
            "capped": None if top is None or top.capped_from is None else {
                "from": top.capped_from, "by": list(rule.holds)},
            "tied": list(rule.tied),
            # keywords of several targets matched different words (rule_arm): how much of the
            # question each one explains, best first
            "contest": None if rule.contest is None else {
                "scores": {name: score for name, score in rule.contest.scores},
                "winner": rule.contest.winner,
            },
        },
        "llm": llm_audit,
        "semantic": None if semantic is None else {"best": semantic.best,
                                                   "score": semantic.score},
    }


def decide_route(
    query: str,
    ctx: ToolContext,
    registry: Registry,
    provider: Provider,
    *,
    explicit_target: str | None = None,
    explicit_params: dict[str, JsonValue] | None = None,
    allow_open_task: bool = False,
) -> RouteDecision:
    start = time.perf_counter()
    candidates = registry.candidates(ctx)
    ctx.emit(TraceStep(step="candidates", status=TraceStatus.OK,
                       facts={"count": len(candidates.names()),
                              "names": sorted(candidates.names())},
                       duration_ms=_ms(start)))

    start = time.perf_counter()
    rule = llm = semantic = ack = None
    if explicit_target is not None:
        # only the user's explicit pick may acknowledge running without a filter
        explicit_params, ack = split_ack(explicit_params)
        decision = _explicit(query, explicit_target, explicit_params, candidates, registry, ctx)
    else:
        rule = match_rules(query, candidates)
        llm = ask_llm(query, candidates, provider)
        decision = reconcile(rule, llm, candidates)
        if decision is None:
            semantic = semantic_match(query, candidates)
            decision = fallback(query, semantic, candidates, allow_open_task=allow_open_task)
        decision = without_ack(decision)
    ctx.emit(TraceStep(step="understand", status=TraceStatus.OK, source=decision.source,
                       facts=_facts(decision, rule, llm.audit() if llm else None, semantic),
                       duration_ms=_ms(start)))
    decision = apply_scope(query, decision, candidates, registry, ctx, ack=ack)
    if decision.kind is RouteKind.OPEN_TASK:
        # the explicit hand-over to exploration, with its evidence (core.explore.handover)
        ctx.emit(handover_step(
            rule_target=rule.top.target if rule is not None and rule.top is not None else None,
            llm_target=llm.target if llm is not None else None,
            semantic_best=semantic.best if semantic is not None else None,
            semantic_score=semantic.score if semantic is not None else 0.0,
            threshold=SEMANTIC_THRESHOLD, candidates=len(candidates.names())))
    return decision
