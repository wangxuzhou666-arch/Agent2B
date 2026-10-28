"""Semantic fallback: pure-Python BM25 over each candidate's declared language.

Runs only when both arms abstain. The document for a target is its name, description,
route hint, keywords and examples (intent text only, never data rows). The description counts
in every language the target declares it in (``Routable.descriptions``, each text once), so a
Chinese question meets Chinese words; English words and Chinese bigrams do not match each
other, so the other language's text mostly just lengthens a document.

The acceptance score is BM25 normalized by the query's own IDF mass:
``score = min(1, bm25(query, doc) / sum(idf(term) for term in query))``. A query term that
appears in no document carries the maximum IDF in the denominator, so off-topic words pull
the score down instead of being ignored. A hit needs ``score >= SEMANTIC_THRESHOLD``.

The same scorer settles a contested rule hit (``explanation_scores``, used by the rule arm):
when keywords of different targets match different words of one question, each contender is
scored on the words its own keywords did not match. Its document there is its *evidence*
(``evidence_document``): name, description (every language), keywords, examples and the params
it accepts, by name and by their label in every other language ("supplier id", "供应商编码"),
without the route hint, because a route hint is written to send questions to a rival ("for
overdue ones use late_shipments") and its words are evidence for that rival.
"""

import math
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from ..contract import Routable
from ..i18n import DEFAULT_LANGUAGE, LANGUAGES
from ..params import param_label, params_model
from ..registry import Candidates
from .text import tokens

SEMANTIC_THRESHOLD = 0.5
K1 = 1.5
B = 0.75


@dataclass(frozen=True)
class SemanticResult:
    target: str | None  # set only when best_score clears the threshold
    best: str | None
    score: float


def descriptions(target: Routable) -> list[str]:
    """The target's description in every language it declares, each distinct text once (a
    plain-string description is one text)."""
    table = getattr(target, "descriptions", None)
    texts = table.values() if isinstance(table, Mapping) and table else [target.description]
    return list(dict.fromkeys(t.strip() for t in texts if isinstance(t, str) and t.strip()))


def document(target: Routable) -> list[str]:
    parts = [target.name.replace("_", " "), *descriptions(target), target.route_hint,
             *target.keywords, *target.examples]
    return tokens(" ".join(p for p in parts if p))


class BM25:
    def __init__(self, docs: dict[str, list[str]]) -> None:
        self.docs = {name: Counter(terms) for name, terms in docs.items()}
        self.lengths = {name: len(terms) for name, terms in docs.items()}
        n = len(docs)
        self.avg_len = (sum(self.lengths.values()) / n) if n else 0.0
        df = Counter(term for terms in docs.values() for term in set(terms))
        self.idf = {t: math.log((n - c + 0.5) / (c + 0.5) + 1) for t, c in df.items()}
        self.unseen_idf = math.log((n + 0.5) / 0.5 + 1)

    def score(self, query_terms: list[str], name: str) -> float:
        counts, length = self.docs[name], self.lengths[name]
        norm = 1 - B + B * (length / self.avg_len if self.avg_len else 0.0)
        total = 0.0
        for term in set(query_terms):
            tf = counts.get(term, 0)
            if tf:
                total += self.idf[term] * tf * (K1 + 1) / (tf + K1 * norm)
        return total

    def normalized(self, query_terms: list[str], name: str) -> float:
        mass = sum(self.idf.get(t, self.unseen_idf) for t in set(query_terms))
        return min(1.0, self.score(query_terms, name) / mass) if mass else 0.0


def param_names(target: Routable, entities: Mapping[str, Any] | None = None) -> list[str]:
    """How a question may name what the target can be asked by: each param's name ("supplier
    id"), and its label in every other language that gives it one of its own ("供应商编码";
    core.params ``param_label``: the declared label, else the target entity's field label)."""
    model = params_model(target)
    out: list[str] = []
    for name in model.model_fields if model else ():
        out.append(name.replace("_", " "))
        english = param_label(target, name, DEFAULT_LANGUAGE, entities)
        for language in LANGUAGES:
            label = param_label(target, name, language, entities)
            if language != DEFAULT_LANGUAGE and label != english:
                out.append(label)
    return out


def evidence_document(target: Routable, entities: Mapping[str, Any] | None = None
                      ) -> list[str]:
    """What a target declares it is about, for settling a contested rule hit: name,
    description (every language), keywords, examples and params (it can be asked "by
    supplier" if it takes a ``supplier_id``; ``entities`` name params in other languages). No
    route hint (see the module docstring)."""
    parts = [target.name.replace("_", " "), *descriptions(target), *target.keywords,
             *target.examples, *param_names(target, entities)]
    return tokens(" ".join(p for p in parts if p))


def explanation_scores(query: str, own_terms: Mapping[str, Collection[str]],
                       candidates: Candidates) -> dict[str, float]:
    """For each contender (``own_terms``: name -> the index terms its own keywords matched),
    how much of the rest of the query its evidence explains: BM25 of the query terms outside
    its own match, over the IDF mass of the whole query. The index spans every candidate, so
    a term shared by many targets weighs little; only the contenders are scored."""
    names = sorted(candidates.names())
    terms = tokens(query)
    index = BM25({n: evidence_document(candidates.get(n), candidates.entities) for n in names})
    mass = sum(index.idf.get(t, index.unseen_idf) for t in set(terms))
    out = {}
    for name, own in own_terms.items():
        rest = [t for t in terms if t not in own]
        out[name] = index.score(rest, name) / mass if mass and name in index.docs else 0.0
    return out


def semantic_match(
    query: str, candidates: Candidates, threshold: float = SEMANTIC_THRESHOLD
) -> SemanticResult:
    names = sorted(candidates.names())
    query_terms = tokens(query)
    if not names or not query_terms:
        return SemanticResult(None, None, 0.0)
    index = BM25({n: document(candidates.get(n)) for n in names})
    scored = sorted(((index.normalized(query_terms, n), n) for n in names),
                    key=lambda pair: (-pair[0], pair[1]))
    score, best = scored[0]
    if score <= 0:
        return SemanticResult(None, None, 0.0)
    return SemanticResult(best if score >= threshold else None, best, round(score, 4))
