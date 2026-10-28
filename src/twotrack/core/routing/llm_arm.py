"""LLM arm: ask the provider for an opinion over compact cards built from candidates only.

The model never learns that a non-candidate target exists. A name it returns that is not a
candidate (hallucinated, hidden by role, or blocked by a precondition) is dropped and counts
as no opinion, exactly like an abstention. Provider failure also counts as no opinion.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import JsonValue

from twotrack.providers.base import Provider, ToolCard

from ..contract import Routable
from ..model_schema import params_schema
from ..registry import Candidates

LLM_STRONG = 0.8  # confidence at or above this is a strong opinion
LLM_MIN = 0.3  # below this the opinion is treated as silence
MAX_CARD_EXAMPLES = 3


@dataclass(frozen=True)
class LLMOpinion:
    target: str | None = None
    confidence: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)
    dropped: bool = False  # the model named something outside the candidate set
    failed: bool = False  # the provider raised

    @property
    def strong(self) -> bool:
        return self.target is not None and self.confidence >= LLM_STRONG

    def audit(self) -> dict[str, JsonValue]:
        """Trace projection: never echoes a raw out-of-set model string."""
        name = "invalid" if self.dropped else self.target
        return {"target": name, "confidence": round(self.confidence, 3), "failed": self.failed}


def tool_card(target: Routable) -> ToolCard:
    """The card of one target. Its description is the English text in any request language:
    a model reads it whatever language the question is in, and one card per target keeps the
    prompt the same for every request."""
    return ToolCard(
        name=target.name,
        kind=target.route_kind.value,
        description=target.description,
        effect=str(target.effect),
        route_hint=target.route_hint,
        examples=tuple(target.examples[:MAX_CARD_EXAMPLES]),
        params_schema=params_schema(target),  # standard JSON Schema keywords only
    )


def tool_cards(candidates: Candidates) -> list[ToolCard]:
    """Cards sorted by name so the prompt is identical across processes."""
    return [tool_card(candidates.get(n)) for n in sorted(candidates.names())]


def ask_llm(query: str, candidates: Candidates, provider: Provider) -> LLMOpinion:
    if not candidates.names():
        return LLMOpinion()
    try:
        route = provider.route(query, tool_cards(candidates))
    except Exception:
        return LLMOpinion(failed=True)
    if route is None:
        return LLMOpinion()
    if route.target not in candidates:
        return LLMOpinion(confidence=route.confidence, dropped=True)
    if route.confidence < LLM_MIN:
        return LLMOpinion(confidence=route.confidence)
    return LLMOpinion(route.target, route.confidence, dict(route.params))
