"""Scope guard: never answer a narrower question with a broader number.

Runs inside ``decide_route`` once the arms (or an explicit call) picked a tool or workflow. The
constraints the question names (``Registry.vocabulary``, core.scope) are matched against the
params the target declares as binding them (``Registry.bindings``, core.params):

* a constraint the target can apply fills its param when the param is unset; a param that is
  already set (an explicit call, the model, the target's own extractor) wins;
* a constraint the target always applies (``Routable.implied_scope``) needs nothing;
* a constraint it cannot apply, an excluded one ("except packaging"), or several values for a
  param that takes one, turns the decision into a clarification instead of a run. The choices:
  the same target without that filter (``explicit_params`` carry ``SCOPE_ACK_PARAM``, which lets
  that one request through and marks the answer's provenance as unfiltered), and up to
  ``MAX_ALTERNATIVES`` candidates that can apply it, most relevant first; or, for several
  values, the target once per value.

A ``scope`` trace step records the check (facts ``mentioned``, ``bound``, ``unbound``) whenever
the question names a constraint or carries an acknowledgement; otherwise nothing is emitted.
Pure apart from that trace step.
"""

import time
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from ..contract import (
    SCOPE_ACK_PARAM,
    RouteDecision,
    RouteKind,
    ScopeCheck,
    ScopeChoice,
    ScopeMention,
    ToolContext,
    ToolResult,
    TraceStatus,
    TraceStep,
)
from ..i18n import as_language, language_of, say
from ..params import target_title
from ..registry import Candidates, Registry
from ..scope import Mention, Vocabulary, plural
from ..text import tokens
from .rule_arm import match_rules
from .semantic import BM25, document

MAX_ALTERNATIVES = 2


def split_ack(explicit_params: Mapping[str, JsonValue] | None
              ) -> tuple[dict[str, JsonValue] | None, list[str] | None]:
    """(explicit params without the acknowledgement, the acknowledged constraint ids)."""
    if explicit_params is None or SCOPE_ACK_PARAM not in explicit_params:
        return (dict(explicit_params) if explicit_params is not None else None), None
    raw = explicit_params[SCOPE_ACK_PARAM]
    ack = [v for v in raw if isinstance(v, str)] if isinstance(raw, list) else []
    return {k: v for k, v in explicit_params.items() if k != SCOPE_ACK_PARAM}, ack


def without_ack(decision: RouteDecision) -> RouteDecision:
    """Only a user's explicit call may acknowledge; the key is dropped from any other params."""
    if SCOPE_ACK_PARAM not in decision.params:
        return decision
    params = {k: v for k, v in decision.params.items() if k != SCOPE_ACK_PARAM}
    return decision.model_copy(update={"params": params})


def apply_scope(query: str, decision: RouteDecision, candidates: Candidates, registry: Registry,
                ctx: ToolContext, *, ack: Sequence[str] | None = None) -> RouteDecision:
    """The decision with bound params filled, or a clarification when the target cannot apply
    what the question names. ``ack``: constraint ids the user's explicit pick runs without."""
    if decision.kind not in (RouteKind.TOOL, RouteKind.WORKFLOW) or decision.target is None:
        return decision
    vocabulary: Vocabulary | None = getattr(registry, "vocabulary", None)
    if vocabulary is None:
        return decision
    started = time.perf_counter()
    bindings = dict(getattr(registry, "bindings", {}).get(decision.target, {}))
    params = _canonical_params(dict(decision.params), bindings, vocabulary)
    mentions = vocabulary.find(query)
    if not mentions and not ack:
        return decision if params == decision.params else \
            decision.model_copy(update={"params": params})
    target = registry.target(decision.target)
    language = language_of(ctx.facts)
    check, verdict = _evaluate(target, params, mentions, bindings, vocabulary, language,
                               frozenset(ack or ()))
    run_params = {**params, **check.filled}
    if verdict == "pass" or verdict == "unfiltered":
        out = decision.model_copy(update={"params": run_params, "scope": check})
    else:
        choices, reason = (_conflict(target, check, run_params, language) if verdict == "conflict"
                           else _gap(query, target, check, run_params, candidates, registry,
                                     language, frozenset(ack or ())))
        check = check.model_copy(update={"choices": choices})
        out = RouteDecision(kind=RouteKind.CLARIFY, target=target.name, params=run_params,
                            confidence=decision.confidence, source=decision.source,
                            clarify_options=list(dict.fromkeys(c.target for c in choices)),
                            reason=reason, scope=check)
    ctx.emit(TraceStep(step="scope",
                       status=TraceStatus.OK if out.kind is decision.kind else TraceStatus.BLOCKED,
                       source=decision.source, facts=_facts(check, verdict),
                       duration_ms=round((time.perf_counter() - started) * 1000, 3)))
    return out


def mark_unfiltered(result: ToolResult, check: ScopeCheck | None,
                    language: str | None) -> ToolResult:
    """The result of an acknowledged unfiltered run, with a provenance scope that says so."""
    if check is None or not check.acknowledged or not check.unbound:
        return result
    note = say("scope_unfiltered", language,
               constraints=_join([_constraint(m, language, negation=True)
                                  for m in check.unbound], say("scope_and", language)),
               kinds=_kinds(check.unbound, language))
    scope = result.provenance.scope.strip()
    joined = (f"{scope}；{note}" if as_language(language) == "zh" else f"{scope}; {note}") \
        if scope else note
    return result.model_copy(update={
        "provenance": result.provenance.model_copy(update={"scope": joined})})


# ---------------------------------------------------------------- evaluation


def _canonical_params(params: dict[str, Any], bindings: Mapping[str, str],
                      vocabulary: Vocabulary) -> dict[str, Any]:
    """Bound params given as a label or synonym ("Spare Parts") become the value id."""
    for param, key in bindings.items():
        value = params.get(param)
        canonical = vocabulary.canonical(key, value) if isinstance(value, str) else None
        if canonical is not None and canonical != value:
            params[param] = canonical
    return params


def _option(mention: Mention, param_for: Mapping[str, str],
            implied: Mapping[str, str]) -> tuple[str, str]:
    """The (key, value) the target can act on, else the first one the words name."""
    for key, value in mention.options:
        if key in param_for:
            return key, value
    for key, value in mention.options:
        if implied.get(key) == value:
            return key, value
    return mention.options[0]


def _evaluate(target: Any, params: Mapping[str, Any], mentions: Sequence[Mention],
              bindings: Mapping[str, str], vocabulary: Vocabulary, language: str,
              ack: frozenset[str]) -> tuple[ScopeCheck, str]:
    param_for = {key: param for param, key in bindings.items()}
    implied = dict(target.implied_scope)
    mentioned: list[ScopeMention] = []
    bound: list[ScopeMention] = []
    unbound: list[ScopeMention] = []
    pending: dict[str, list[ScopeMention]] = {}
    seen: set[tuple[str, str, bool]] = set()
    for mention in mentions:
        key, value = _option(mention, param_for, implied)
        if (key, value, mention.negated) in seen:
            continue
        seen.add((key, value, mention.negated))
        item = ScopeMention(key=key, value=value, text=mention.text.strip(),
                            kind=vocabulary.name(key, language),
                            label=vocabulary.label(key, value, language),
                            negated=mention.negated)
        mentioned.append(item)
        param = param_for.get(key)
        if mention.negated:
            unbound.append(item)
        elif param is not None:
            item = item.model_copy(update={"param": param})
            if params.get(param) is None:
                pending.setdefault(param, []).append(item)
            else:
                bound.append(item)  # a param that is already set wins
        elif implied.get(key) == value:
            bound.append(item.model_copy(update={"implied": True}))
        else:
            unbound.append(item)
    filled: dict[str, JsonValue] = {}
    conflicts: list[ScopeMention] = []
    for param, items in pending.items():
        if len(items) == 1:
            filled[param] = items[0].value
            bound.append(items[0])
        else:
            conflicts.extend(items)
    acknowledged = bool(unbound) and all(m.id in ack for m in unbound)
    check = ScopeCheck(target=target.name, mentioned=mentioned, bound=bound, unbound=unbound,
                       conflicts=conflicts, filled=filled, acknowledged=acknowledged)
    if conflicts:
        return check, "conflict"
    if unbound and not acknowledged:
        return check, "gap"
    return check, "unfiltered" if acknowledged else "pass"


# ---------------------------------------------------------------- clarifications


def _gap(query: str, target: Any, check: ScopeCheck, run_params: dict[str, Any],
         candidates: Candidates, registry: Registry, language: str,
         ack: frozenset[str]) -> tuple[list[ScopeChoice], str]:
    """The choices, and a plain sentence: what the target cannot apply (a filter it lacks, or
    an exclusion, which no target applies), then what the unfiltered choice shows instead."""
    open_items = [m for m in check.unbound if m.id not in ack]
    show_all = say("scope_show_all", language, kinds=_kinds(check.unbound, language))
    unfiltered = ScopeChoice(
        target=target.name, label=show_all,
        params={**run_params, SCOPE_ACK_PARAM: [m.id for m in check.unbound]}, unfiltered=True)
    others = _alternatives(query, target, check, candidates, registry)
    either = say("scope_or", language)
    limit = _join([_constraint(m, language) for m in open_items if not m.negated], either)
    exclude = _join([_constraint(m, language) for m in open_items if m.negated], either)
    key = "scope_gap_both" if limit and exclude else \
        "scope_gap_exclude" if exclude else "scope_gap_limit"
    gap = say(key, language, target=target_title(target, language), limit=limit,
              exclude=exclude)
    then = say("scope_gap_choose" if others else "scope_gap_only", language, choice=show_all,
               kinds=_kinds(open_items, language))
    reason = f"{gap}{then}" if as_language(language) == "zh" else f"{gap} {then}"
    return [unfiltered, *(ScopeChoice(target=name) for name in others)], reason


def _conflict(target: Any, check: ScopeCheck, run_params: dict[str, Any],
              language: str) -> tuple[list[ScopeChoice], str]:
    choices = [ScopeChoice(target=target.name, label=m.label,
                           params={**run_params, m.param: m.value}) for m in check.conflicts]
    first = check.conflicts[0]
    reason = say("scope_conflict", language, kind=first.kind,
                 target=target_title(target, language),
                 labels=_join([m.label for m in check.conflicts if m.key == first.key],
                              "、" if as_language(language) == "zh" else ", "))
    return choices, reason


def _alternatives(query: str, target: Any, check: ScopeCheck, candidates: Candidates,
                  registry: Registry) -> list[str]:
    """Candidates (same effect) that can apply every open constraint, most relevant first:
    most mentioned constraints covered, then keyword strength plus BM25 score. A candidate
    the question has nothing in common with is left out."""
    needed = check.unbound
    if not needed or any(m.negated for m in needed):
        return []
    names = sorted(candidates.names())
    strength = {h.target: h.strength for h in match_rules(query, candidates).hits}
    terms = tokens(query)
    index = BM25({n: document(candidates.get(n)) for n in names}) if terms else None
    ranked = []
    for name in names:
        other = candidates.get(name)
        if name == target.name or other is None or other.effect is not target.effect:
            continue
        keys = frozenset(registry.bindings.get(name, {}).values())
        if not all(_covers(m, keys, other.implied_scope) for m in needed):
            continue
        relevance = strength.get(name, 0.0) + (index.normalized(terms, name) if index else 0.0)
        if relevance > 0:
            covered = sum(1 for m in check.mentioned if _covers(m, keys, other.implied_scope))
            ranked.append((-covered, -relevance, name))
    return [name for *_, name in sorted(ranked)[:MAX_ALTERNATIVES]]


def _covers(m: ScopeMention, keys: frozenset[str], implied: Mapping[str, str]) -> bool:
    return m.key in keys or implied.get(m.key) == m.value


# ---------------------------------------------------------------- text and trace


def _join(parts: Sequence[str], sep: str) -> str:
    return sep.join(parts)


def _constraint(m: ScopeMention, language: str | None, *, negation: bool = False) -> str:
    """'category Packaging'; with ``negation``, an excluded one reads 'category other than
    Packaging' (where the sentence around it does not already say "leave out")."""
    key = "scope_constraint_not" if negation and m.negated else "scope_constraint"
    return say(key, language, kind=m.kind, label=m.label)


def _kinds(mentions: Sequence[ScopeMention], language: str | None) -> str:
    names = list(dict.fromkeys(m.kind for m in mentions))
    if as_language(language) != "zh":
        names = [plural(n) for n in names]
    return _join(names, say("scope_and", language))


def _facts(check: ScopeCheck, verdict: str) -> dict[str, JsonValue]:
    def short(m: ScopeMention) -> dict[str, JsonValue]:
        return {"key": m.key, "value": m.value, **({"negated": True} if m.negated else {})}

    return {
        "target": check.target,
        "verdict": verdict,
        "mentioned": [{**short(m), "text": m.text} for m in check.mentioned],
        "bound": [{**short(m), "param": m.param, "implied": m.implied} for m in check.bound],
        "unbound": [short(m) for m in check.unbound],
        "conflicts": [{**short(m), "param": m.param} for m in check.conflicts],
        "filled": dict(check.filled),
        "acknowledged": check.acknowledged,
        "options": [c.target for c in check.choices],
    }
