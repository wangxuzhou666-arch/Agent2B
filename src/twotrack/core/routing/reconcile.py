"""Reconcile the two arms into one RouteDecision. Pure functions; no I/O.

R = rule arm's top hit, L = LLM opinion after dropping non-candidates (a dropped or failed
opinion is "silent"). Strong/weak thresholds live in rule_arm and llm_arm.

    R \\ L         silent           same target       strong, differs      weak, differs
    silent         -> fallback      -                 llm_confirm [L] (*)   llm_confirm [L]
    weak           rule (+gate)     agree             llm_confirm [L, R]    clarify [R, L]
    strong         rule             agree             clarify [R, L]        rule
    tied R1..Rn    clarify [R..]    agree on that R   clarify [R.., L]      clarify [R.., L]

(*) Exception: a read-only target with L.confidence >= LLM_AUTO_ACCEPT goes straight to
    source=llm. Anything else the model proposes alone is offered back as a one-option
    confirmation (kind=clarify, source=llm_confirm, target=the suggestion, params kept so the
    client can confirm with one click).

A contested rule result (keywords of different targets matched different words, see rule_arm)
needs no row of its own: when one contender clearly explains more of the question, R is that
contender as a *weak* hit (so a write it names is gated, and any model opinion weighs in as in
the weak row), or as a strong one when it is a complete write and the other contenders matched
only names of its params, exactly as without a contest; when none does, the close contenders
are R1..Rn of the tied row.

Fallback when both arms are silent: semantic hit (+gate, source=semantic) -> open_task (if
enabled and there are candidates, source=agent) -> chat (source=fallback: no arm picked
anything, so the audit's per-source shares do not count it as a BM25 decision).

Write gate: a write/external target reached without strong evidence (explicit call, both arms
agreeing, or a strong rule hit) is turned into a one-option confirmation. The pipeline's grant
still guards execution; the gate only stops routing from silently aiming at a write. A strong
rule hit includes a complete write: one phrase of a write target in a question that gives every
param the target requires (rule_arm). That request reaches the grant directly, because the
grant's approval already shows exactly what will run.

A request that holds a write back (``RuleOpinion.holds``: it reads as a question, negates the
write's phrase or excludes a value; rule_arm) is strong evidence for that write on neither arm:
the rule arm caps its top hit at weak, and the arms agreeing (the "agree" cells above) gates
the write too, so the user confirms it before anything is held for approval.
"""

from typing import Any

from ..contract import ResourceEffect, RouteDecision, RouteKind, RouteSource
from ..registry import Candidates
from .llm_arm import LLMOpinion
from .rule_arm import BY_COMPLETE_PARAMS, RuleHit, RuleOpinion, extract_params
from .semantic import SemanticResult

LLM_AUTO_ACCEPT = 0.9


def _kind(name: str, candidates: Candidates) -> RouteKind:
    return RouteKind.WORKFLOW if name in candidates.workflows else RouteKind.TOOL


def changes_state(name: str, candidates: Candidates) -> bool:
    target = candidates.get(name)
    return target is None or target.effect is not ResourceEffect.READ


def go(name: str, candidates: Candidates, source: RouteSource, confidence: float,
       params: dict[str, Any], reason: str) -> RouteDecision:
    return RouteDecision(kind=_kind(name, candidates), target=name, params=params,
                         confidence=confidence, source=source, reason=reason)


def ask(options: list[str], source: RouteSource, confidence: float, reason: str,
        params: dict[str, Any] | None = None) -> RouteDecision:
    unique = list(dict.fromkeys(options))
    return RouteDecision(kind=RouteKind.CLARIFY, target=unique[0] if len(unique) == 1 else None,
                         params=params or {}, confidence=confidence, source=source,
                         clarify_options=unique, reason=reason)


def gate_writes(decision: RouteDecision, candidates: Candidates, *, strong: bool) -> RouteDecision:
    """Downgrade a weakly supported route to a write/external target into a confirmation."""
    if strong or decision.kind not in (RouteKind.TOOL, RouteKind.WORKFLOW):
        return decision
    if not changes_state(decision.target, candidates):
        return decision
    return ask([decision.target], decision.source, decision.confidence,
               f"'{decision.target}' changes data; confirm before it runs", decision.params)


def _agree(hit: RuleHit, llm: LLMOpinion, rule: RuleOpinion,
           candidates: Candidates) -> RouteDecision:
    """Both arms name ``hit``'s target: strong evidence, unless the request holds a write back
    (``RuleOpinion.holds``), so a write the arms agree on is then confirmed first."""
    decision = go(hit.target, candidates, RouteSource.RULE, max(hit.strength, llm.confidence),
                  {**llm.params, **hit.params}, "keyword match and model agree")
    return gate_writes(decision, candidates, strong=not rule.holds)


def _keyword_reason(rule: RuleOpinion) -> str:
    top, contest = rule.top, rule.contest
    reason = f"keyword match: {', '.join(top.matched)}"
    if contest is not None and contest.winner is not None:
        others = [f"{h.target} ({', '.join(h.matched)})" for h in rule.hits[1:]
                  if h.target in contest.contenders]
        (_, best), (_, runner_up) = contest.scores[0], contest.scores[1]
        reason = (f"{reason}; other keywords matched other words: {'; '.join(others)}. "
                  f"{top.target} explains more of the question ({best} vs {runner_up})")
    if top.strong_by == BY_COMPLETE_PARAMS:
        reason = f"{reason}, and the question gives every param it requires"
    return reason


def reconcile(rule: RuleOpinion, llm: LLMOpinion, candidates: Candidates) -> RouteDecision | None:
    """The matrix above. ``None`` means both arms are silent: run ``fallback``."""
    top, tied = rule.top, rule.tied
    if top is None and llm.target is None:
        return None

    if tied:
        if llm.target in tied:
            return _agree(next(h for h in rule.hits if h.target == llm.target), llm, rule,
                          candidates)
        options = [*tied, *([llm.target] if llm.target else [])]
        source = RouteSource.LLM if llm.target else RouteSource.RULE
        reason = ("keywords of several targets match different words, and none explains the "
                  "rest of the question clearly better" if rule.contest is not None
                  else "several targets match equally well")
        return ask(options, source, top.strength, reason)

    if top is None:
        if llm.confidence >= LLM_AUTO_ACCEPT and not changes_state(llm.target, candidates):
            return go(llm.target, candidates, RouteSource.LLM, llm.confidence, llm.params,
                      "model is confident and the target is read-only")
        return ask([llm.target], RouteSource.LLM_CONFIRM, llm.confidence,
                   "only the model suggested this target; confirm it", llm.params)

    if llm.target is None:
        decision = go(top.target, candidates, RouteSource.RULE, top.strength, top.params,
                      _keyword_reason(rule))
        return gate_writes(decision, candidates, strong=top.strong)

    if llm.target == top.target:
        return _agree(top, llm, rule, candidates)

    if top.strong and not llm.strong:
        return go(top.target, candidates, RouteSource.RULE, top.strength, top.params,
                  "strong keyword match outweighs a weak model opinion")
    if llm.strong and not top.strong:
        return ask([llm.target, top.target], RouteSource.LLM_CONFIRM, llm.confidence,
                   "model and a weak keyword match disagree; confirm the model's suggestion",
                   llm.params)
    return ask([top.target, llm.target], RouteSource.LLM,
               min(top.strength, llm.confidence), "keyword match and model disagree")


def fallback(query: str, semantic: SemanticResult, candidates: Candidates, *,
             allow_open_task: bool) -> RouteDecision:
    if semantic.target is not None:
        target = candidates.get(semantic.target)
        decision = go(semantic.target, candidates, RouteSource.SEMANTIC, semantic.score,
                      extract_params(target, query), f"closest description ({semantic.score})")
        return gate_writes(decision, candidates, strong=False)
    if allow_open_task and candidates.names():
        return RouteDecision(kind=RouteKind.OPEN_TASK, confidence=0.0, source=RouteSource.AGENT,
                             reason="no single target matched; planning with candidate tools")
    return RouteDecision(kind=RouteKind.CHAT, confidence=0.0, source=RouteSource.FALLBACK,
                         reason="no target matched")
