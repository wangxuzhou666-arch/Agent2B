"""Rule arm: deterministic keyword and example matching over the candidate set only.

Strength (named constants, compared by reconcile):
* the query equals one of the target's examples (normalized)  -> STRONG_EXAMPLE
* two or more of the target's keywords appear                 -> STRONG_KEYWORDS
* exactly one keyword appears                                 -> WEAK
  (unless it is a complete write, see below                   -> STRONG_KEYWORDS)
* a target that changes data, in a request that holds it back -> WEAK, whatever the row above
  (a question, a negation or an exclusion, see the end)

Ranking is (strength, keyword hits, matched characters), then name, so the result never
depends on dict order. Targets that tie on the ranking key are reported as ``tied``.

That ranking only decides between matches that compete for the same words. Every hit records
where its keywords matched (``spans``, in the normalized query; an example match spans all of
it). Going down the ranking, a hit is *independent* when at least one of its spans overlaps no
span of an independent hit ranked above it; the others are explained away by a more specific
match ("create a purchase order" holds "purchase order"). One independent hit: the ranking
stands, exactly as before.

Two or more independent hits matched different words ("late" and "purchase orders" in "late
purchase orders"), and how long each keyword is says nothing about which report was asked
for. That is a *contest*, settled by meaning instead (``semantic.explanation_scores``): each
contender is scored on how much of the rest of the question, the words its own keywords did
not match, its declared evidence explains. A late-shipments report that is about purchase
orders explains "purchase orders"; a purchase-order listing does not explain "late". The best
wins when the runner-up scores at most ``1 / CONTEST_MARGIN`` of it; otherwise the contenders
within that margin are ``tied`` and reconcile asks between them. Either way the top hit is
capped at ``WEAK``: a write it names is confirmed before it is routed to
(``reconcile.gate_writes``) unless it is a complete write that won the contest on words of its
own params (below), and a model opinion can still overrule it.

A complete write is the one single-keyword hit that is strong. When the top hit stands alone
(no tie at the top of the ranking, or a contest won by the margin over rivals that matched
only names of its params) and it is one phrase of a write target, and the request also gave
every param that target requires, the params count as the second keyword: the hit takes
``STRONG_KEYWORDS``. "Write" means ``ResourceEffect.WRITE``, which the pipeline never runs
without a one-time grant, so a complete request goes straight to that grant, whose approval is
the confirmation, instead of asking "did you mean Create purchase order?" first. "Every param
it requires" is read from the target's own params model and the extractor's output
(``core.params.gives_required_params``: its required fields, all extracted from the question,
valid as they are), so the rule names no tool. A contest won by the margin counts like an
uncontested ranking only when every keyword its losing contenders matched is the name, or a
label in any language, of one of the winner's own params (``param_words``): a request that
names its supplier also matches the word "supplier" (供应商) of a supplier report, and the
approval card shows that word as the write's supplier param. A loser that matched anything else
("reorder plan", "vendor", "low on stock") may have asked for something the approval card does
not show, so the hit stays weak and routing confirms the write first. It leaves alone: a request
missing a required param (weak); a target that requires none (its question gave nothing beyond
the phrase); a tie, or a contest no target won by the margin, which is asked back; an external
target, which the lift does not cover (a grant follows a target's declared ``approval``, not its
effect); and anything the rule arm did not match, BM25 hits included (a bare verb is no keyword
of a write, so it never starts this). The lift comes after ranking and
contest, so a complete write never outranks, or explains away, a hit it would not have beaten
anyway.

Last, a request can hold a write back from its approval, however it was named
(``RuleOpinion.holds``): it reads as a question (``interrogative.is_question``), because asking
about an order, or whether to place one, is not asking to place it; a negator stands before the
phrase of the top hit or of any hit tied with it (``negation.negates``), which says not to place
the order, also when a read ranks first and a model then picks the write it is tied with; or it
excludes a value (``exclusion.excludes``: "not SUP-003"), which the approval card cannot show.
Then a top hit whose target changes data (a write, or an external target) is capped at ``WEAK``
on every path to strength: an example, two keywords ("place an order and raise a PO"), complete
params. Its target stays the same, so routing confirms it before anything is held for approval,
also when a model agrees with it, or with a hit tied with it (``reconcile``); a read keeps the
strength it has.

``RuleHit.strong_by`` says why a hit is strong, for the ``understand`` trace: ``"example"``,
``"keywords"`` or ``"complete_params"``; None when it is weak. ``RuleHit.capped_from`` is what
made a capped hit strong.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ..contract import ResourceEffect, Routable
from ..i18n import LANGUAGES
from ..params import gives_required_params, param_label, params_model
from ..registry import Candidates
from .exclusion import excludes
from .interrogative import is_question
from .negation import negates
from .semantic import explanation_scores
from .text import normalize, phrase_spans, tokens

STRONG_EXAMPLE = 1.0
STRONG_KEYWORDS = 0.9
WEAK = 0.6
STRONG_MIN = STRONG_KEYWORDS  # strength at or above this is "strong"
# a contest is won by explaining at least this many times more of the question than the
# runner-up; a closer call is asked back
CONTEST_MARGIN = 2.0

# why a hit is strong (RuleHit.strong_by)
BY_EXAMPLE = "example"
BY_KEYWORDS = "keywords"
BY_COMPLETE_PARAMS = "complete_params"

Span = tuple[int, int]


@dataclass(frozen=True)
class RuleHit:
    target: str
    strength: float
    matched: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    spans: tuple[Span, ...] = ()  # where the match sits in the normalized query
    strong_by: str | None = None  # BY_EXAMPLE, BY_KEYWORDS or BY_COMPLETE_PARAMS; None: weak
    capped_from: str | None = None  # the strong_by a held request capped (RuleOpinion.holds)

    @property
    def strong(self) -> bool:
        return self.strength >= STRONG_MIN

    def rank_key(self) -> tuple[float, int, int]:
        return (self.strength, len(self.matched), sum(len(m) for m in self.matched))


@dataclass(frozen=True)
class Contest:
    """Independent hits on different words, scored by how much of the question each explains.
    ``scores`` is best first; ``winner`` is set when the margin held, else ``close`` holds
    the contenders reconcile asks between."""

    scores: tuple[tuple[str, float], ...]
    winner: str | None = None
    close: tuple[str, ...] = ()

    @property
    def contenders(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.scores)


@dataclass(frozen=True)
class RuleOpinion:
    hits: tuple[RuleHit, ...] = ()
    contest: Contest | None = None
    question: bool = False  # the request reads as a question
    negated: bool = False  # a negator stands before the phrase of the top hit or a tied one
    excluded: bool = False  # the request excludes a value ("not SUP-003")

    @property
    def top(self) -> RuleHit | None:
        return self.hits[0] if self.hits else None

    @property
    def holds(self) -> tuple[str, ...]:
        """Why the request holds a write back from its approval: "question", "negated" and
        "excluded", those that apply (module docstring)."""
        return tuple(name for name, holds in (("question", self.question),
                                              ("negated", self.negated),
                                              ("excluded", self.excluded)) if holds)

    @property
    def tied(self) -> tuple[str, ...]:
        """Names sharing the top rank (empty when the top hit is unique). After a contest:
        the contenders it could not separate."""
        if self.contest is not None:
            return self.contest.close
        if len(self.hits) < 2 or self.hits[1].rank_key() != self.hits[0].rank_key():
            return ()
        key = self.hits[0].rank_key()
        return tuple(h.target for h in self.hits if h.rank_key() == key)


def extract_params(target: Routable, query: str) -> dict[str, Any]:
    """Run the target's deterministic extractor. A failing extractor, or one returning
    something that is not a JSON object, yields no params (the pipeline then validates)."""
    if target.param_extractor is None:
        return {}
    try:
        out = target.param_extractor(query)
        json.dumps(out)
    except Exception:
        return {}
    return dict(out) if isinstance(out, dict) else {}


def _hit(target: Routable, query: str) -> RuleHit | None:
    norm = normalize(query)
    if any(normalize(e) == norm for e in target.examples if e.strip()):
        return RuleHit(target.name, STRONG_EXAMPLE, ("example",), spans=((0, len(norm)),),
                       strong_by=BY_EXAMPLE)
    matched, spans = [], []
    for keyword in target.keywords:
        found = phrase_spans(keyword, query)
        if found:
            matched.append(keyword)
            spans.extend(found)
    if not matched:
        return None
    strong = len(matched) >= 2
    return RuleHit(target.name, STRONG_KEYWORDS if strong else WEAK, tuple(matched),
                   spans=tuple(sorted(set(spans))), strong_by=BY_KEYWORDS if strong else None)


def _overlaps(a: Span, b: Span) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def independent(hits: tuple[RuleHit, ...]) -> list[RuleHit]:
    """The hits (ranked best first) that matched at least one word that no better independent
    hit matched."""
    out: list[RuleHit] = []
    claimed: list[Span] = []
    for hit in hits:
        if any(not any(_overlaps(s, c) for c in claimed) for s in hit.spans):
            out.append(hit)
            claimed.extend(hit.spans)
    return out


def _own_terms(hit: RuleHit, norm: str) -> set[str]:
    return {t for start, end in hit.spans for t in tokens(norm[start:end])}


def _contest(query: str, hits: tuple[RuleHit, ...], contenders: list[RuleHit],
             candidates: Candidates) -> RuleOpinion:
    norm = normalize(query)
    raw = explanation_scores(query, {h.target: _own_terms(h, norm) for h in contenders},
                             candidates)
    rank = {h.target: i for i, h in enumerate(contenders)}
    order = sorted(rank, key=lambda name: (-raw[name], rank[name]))
    best, runner_up = raw[order[0]], raw[order[1]]
    scores = tuple((name, round(raw[name], 4)) for name in order)
    if best > 0 and runner_up * CONTEST_MARGIN <= best:
        contest = Contest(scores, winner=order[0])
    else:
        close = [n for n in order if best <= 0 or raw[n] * CONTEST_MARGIN > best]
        contest = Contest(scores, close=tuple(close))
    lead = next(h for h in hits if h.target == order[0])
    capped = replace(lead, strength=min(lead.strength, WEAK), strong_by=None)
    return RuleOpinion((capped, *(h for h in hits if h is not lead)), contest)


def param_words(target: Routable, entities: Mapping[str, Any] | None = None) -> frozenset[str]:
    """Every name of one of ``target``'s params, normalized: its key, also with spaces
    ("supplier_id", "supplier id"), and its label in every language ("Supplier", "供应商";
    ``core.params.param_label``, which reads ``entities`` for a label the param does not
    declare)."""
    model = params_model(target)
    words: set[str] = set()
    for key in model.model_fields if model else ():
        words.update((key, key.replace("_", " ")))
        words.update(param_label(target, key, language, entities) for language in LANGUAGES)
    return frozenset(normalize(word) for word in words)


def _rivals_name_its_params(opinion: RuleOpinion, target: Routable,
                            candidates: Candidates) -> bool:
    """No contest, or every keyword its losing contenders matched names one of ``target``'s
    params (``param_words``), so the approval card shows what they matched."""
    contest = opinion.contest
    if contest is None:
        return True
    names = param_words(target, candidates.entities)
    losers = [h for h in opinion.hits
              if h.target in contest.contenders and h.target != contest.winner]
    return all(normalize(word) in names for hit in losers for word in hit.matched)


def _complete_write(opinion: RuleOpinion, candidates: Candidates) -> RuleOpinion:
    """The opinion, its top hit made strong when it is a complete write (one phrase of a write
    target, every param the target requires given; see the module docstring). A tie, a contest
    no target won by the margin (its close contenders are ``tied``) or one whose losers matched
    words other than names of the winner's params leave it as it is. A request that holds the
    write back is capped afterwards (``_hold``)."""
    top = opinion.top
    if top is None or top.strong or opinion.tied:
        return opinion
    target = candidates.get(top.target)
    if target is None or target.effect is not ResourceEffect.WRITE \
            or not gives_required_params(target, top.params) \
            or not _rivals_name_its_params(opinion, target, candidates):
        return opinion
    lifted = replace(top, strength=STRONG_KEYWORDS, strong_by=BY_COMPLETE_PARAMS)
    return replace(opinion, hits=(lifted, *opinion.hits[1:]))


def _hold(opinion: RuleOpinion, candidates: Candidates) -> RuleOpinion:
    """The opinion, its top hit capped at ``WEAK`` when its target changes data and the request
    holds it back (``RuleOpinion.holds``), whatever made it strong; the target stays."""
    top = opinion.top
    if top is None or not top.strong or not opinion.holds:
        return opinion
    target = candidates.get(top.target)
    if target is None or target.effect is ResourceEffect.READ:
        return opinion
    capped = replace(top, strength=WEAK, strong_by=None, capped_from=top.strong_by)
    return replace(opinion, hits=(capped, *opinion.hits[1:]))


def match_rules(query: str, candidates: Candidates) -> RuleOpinion:
    hits = []
    for name in sorted(candidates.names()):
        target = candidates.get(name)
        hit = _hit(target, query) if target is not None else None
        if hit is not None:
            hits.append(replace(hit, params=extract_params(target, query)))
    hits.sort(key=lambda h: (tuple(-v for v in h.rank_key()), h.target))
    ranked = tuple(hits)
    contenders = independent(ranked)
    opinion = (_contest(query, ranked, contenders, candidates) if len(contenders) >= 2
               else RuleOpinion(ranked))
    # a tied hit is as much what the request may have named as the top one (reconcile routes to
    # it when the model picks it), so a negator before its phrase holds the request back too
    heads = [h for h in opinion.hits if h is opinion.top or h.target in opinion.tied]
    opinion = replace(opinion, question=is_question(query),
                      negated=negates(query, [span for h in heads for span in h.spans]),
                      excluded=excludes(query))
    return _hold(_complete_write(opinion, candidates), candidates)
