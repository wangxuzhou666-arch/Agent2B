"""Zero-key provider. Deterministic, offline, and honest about what it cannot do.

* ``route`` always abstains, so the rule arm and the semantic fallback carry routing.
* ``narrate`` returns the deterministic template (numbers come only from the view).
  ``hallucinated_number`` appends one invented figure so the numeric lock can be demonstrated.
* ``chat`` and ``agent_step`` say plainly that no model is configured; they never plan, with
  one exception: for the documented exploration case (``mock_script.EXPLORATION_CASE``)
  ``agent_step`` follows that case's fixed script (``mock_script``), copying every value it
  sends from the request and the fenced tool results it was given. A script, not a planner.

Replies follow the request language: the view's ``language`` for narration, the user's own
message for chat and the agent loop.
"""

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from twotrack.core.i18n import as_language, detect_language, say
from twotrack.core.narration import format_number, template_narration

from .base import AgentStep, ChatMessage, LLMRoute, ToolCard
from .mock_script import script_step

OFFLINE_CHAT = say("offline_chat")
OFFLINE_AGENT = say("offline_agent")


def _user_language(messages: Sequence[ChatMessage], *, last: bool) -> str:
    users = [m.content for m in messages if m.role == "user"]
    if not users:
        return "en"
    return detect_language(users[-1] if last else users[0])


class MockProvider:
    name = "mock"
    mock = True  # labels (providers.base.provider_info): its narration is the template

    def __init__(self, *, hallucinated_number: float | None = None) -> None:
        self.hallucinated_number = hallucinated_number

    def route(self, query: str, tool_cards: Sequence[ToolCard]) -> LLMRoute | None:
        return None

    def narrate(self, question: str, view: Mapping[str, JsonValue]) -> str:
        language = as_language(view.get("language")) or detect_language(question)
        text = template_narration(view, language=language)
        if self.hallucinated_number is not None:
            figure = say("invented_figure", language,
                         figure=format_number(self.hallucinated_number))
            text += figure if language == "zh" else f" {figure}"
        return text

    def chat(self, messages: Sequence[ChatMessage]) -> str:
        return say("offline_chat", _user_language(messages, last=True))

    def agent_step(
        self, messages: Sequence[ChatMessage], tool_cards: Sequence[ToolCard]
    ) -> AgentStep:
        scripted = script_step(messages)  # the documented exploration case only
        if scripted is not None:
            return scripted
        return AgentStep(text=say("offline_agent", _user_language(messages, last=False)))
