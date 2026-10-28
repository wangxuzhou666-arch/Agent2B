"""Provider contract: the only door between twotrack and a language model.

A provider does four things and nothing else:

* ``route``      - name one target from the tool cards it was shown (or abstain);
* ``narrate``    - describe a result from its narrator view (never from rows);
* ``chat``       - free conversation with no data attached;
* ``agent_step`` - one tool-calling turn of the open-task loop.

Everything a provider returns is parsed into the Pydantic models below, so the rest of the
system never handles a bare model reply. Pipeline code must not know which provider it talks
to; swapping mock for a real endpoint is a configuration change.

Egress: ``GuardedProvider`` wraps any provider and passes every outgoing string through a
scrubber before the call. The graph, workflow runner and agent loop always use it.

Failures: a call that fails (an HTTP error, a timeout, an unparseable reply) raises
``ProviderError``; ``route`` included, so the routing arm can tell a failed opinion from an
abstention. ``GuardedProvider`` records each failed call as a ``ModelCallError`` (the call, the
failure's class, the HTTP status) before re-raising, and the graph puts those records in the
trace and the answer: a configured model that fails is never mistaken for one that answered,
nor for the zero-key mock.

Labels: a provider may declare ``name``, ``model`` and ``mock`` attributes; ``provider_info``
reads them so the health check, the trace and the demo can say *which* model answered (and that
the zero-key mock writes template text, not model text). They are labels only: no code path may
behave differently because of them.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ProviderError(RuntimeError):
    """A model call failed: an HTTP error, a timeout, an unparseable or empty reply.

    ``error`` names the failure's class (the SDK's exception, e.g. ``BadRequestError``) and
    ``http_status`` its HTTP status, when it has one. Neither carries the request or the key,
    so both may be recorded; the message is not (it names the call and the class only, but
    other providers may say more)."""

    def __init__(self, message: str = "model call failed", *, error: str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.error = error or type(self).__name__
        self.http_status = http_status


class UnparseableReply(ProviderError):
    """The model answered, but not in the shape the call needs: no text, or a routing reply
    that fails its schema."""


def http_status_of(exc: BaseException) -> int | None:
    """The HTTP status an SDK exception carries (``status_code``), if it has a valid one."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return status
    return None


ModelCall = Literal["route", "narrate", "chat", "agent_step"]
TEXT_CALLS: frozenset[str] = frozenset({"narrate", "chat", "agent_step"})


class ModelCallError(BaseModel):
    """A model call that failed, as the trace and the answer record it. Never the error's
    message, the request or the key: a message may echo the request."""

    model_config = ConfigDict(frozen=True)

    call: ModelCall  # route, narrate, chat (also a workflow's understand step) or agent_step
    error: str  # the failure's class: "BadRequestError", "APITimeoutError", "UnparseableReply"
    http_status: int | None = None  # the endpoint's status code, for an HTTP error

    @classmethod
    def of(cls, call: ModelCall, exc: BaseException) -> "ModelCallError":
        if isinstance(exc, ProviderError):
            return cls(call=call, error=exc.error, http_status=exc.http_status)
        return cls(call=call, error=type(exc).__name__, http_status=http_status_of(exc))


class ToolCard(BaseModel):
    """What a model may know about one candidate target. Built only from candidates."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["tool", "workflow"]
    description: str
    effect: str
    route_hint: str = ""
    examples: tuple[str, ...] = ()
    params_schema: dict[str, JsonValue] = Field(default_factory=lambda: {"type": "object"})


class LLMRoute(BaseModel):
    """A model's routing opinion. Unknown target names are dropped by the router."""

    target: str
    params: dict[str, JsonValue] = {}
    confidence: float = Field(ge=0.0, le=1.0)


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, JsonValue] = {}
    malformed: bool = False  # arguments were not a JSON object; the loop refuses to run it


class AgentStep(BaseModel):
    """One open-task turn: either tool calls to run, or final text (or both empty)."""

    text: str = ""
    tool_calls: list[ToolCall] = []


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = []  # assistant turns that requested tools
    tool_call_id: str | None = None  # tool turns: which call this answers


@runtime_checkable
class Provider(Protocol):
    # None: the model abstains. A call that fails raises (``ProviderError``).
    def route(self, query: str, tool_cards: Sequence[ToolCard]) -> LLMRoute | None: ...

    def narrate(self, question: str, view: Mapping[str, JsonValue]) -> str: ...

    def chat(self, messages: Sequence[ChatMessage]) -> str: ...

    def agent_step(
        self, messages: Sequence[ChatMessage], tool_cards: Sequence[ToolCard]
    ) -> AgentStep: ...


# ---------------------------------------------------------------- egress scrubbing


class Scrubbed(Protocol):
    text: str
    redacted_count: int


class Scrubber(Protocol):
    def scrub(self, payload: str) -> Scrubbed: ...


@dataclass(frozen=True)
class PassThrough:
    text: str
    redacted_count: int = 0


class NoScrub:
    """Default scrubber: sends payloads unchanged. Deployments inject a real egress guard."""

    def scrub(self, payload: str) -> PassThrough:
        return PassThrough(payload)


@dataclass
class GuardedProvider:
    """Scrubs every outgoing string (query, view values, message contents) before delegating.

    Tool cards are not scrubbed: they are built from plugin declarations, not request data.
    Numbers pass unchanged so the narrator can still cite computed values.

    Built once per request, it also keeps that request's failed calls in ``failures`` (a scrub
    that fails is one too: nothing was sent) and re-raises, so callers degrade as before.
    """

    inner: Provider
    scrubber: Scrubber = field(default_factory=NoScrub)
    redacted_count: int = 0
    failures: list[ModelCallError] = field(default_factory=list)

    @contextmanager
    def _recorded(self, call: ModelCall) -> Iterator[None]:
        try:
            yield
        except Exception as exc:
            self.failures.append(ModelCallError.of(call, exc))
            raise

    def text_failure(self) -> ModelCallError | None:
        """The last failed call that writes text (narrate, chat, agent step): why an answer
        that should have come from the model is template text."""
        return next((f for f in reversed(self.failures) if f.call in TEXT_CALLS), None)

    def _text(self, value: str) -> str:
        out = self.scrubber.scrub(value)
        self.redacted_count += out.redacted_count
        return out.text

    def scrub(self, value: str) -> str:
        """``value`` as it may leave for a model. For a caller whose model calls do not pass
        through this provider (the Pi explorer's worker talks to its endpoint itself), so what
        it sends is scrubbed and counted the same way."""
        return self._text(value)

    def _deep(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, Mapping):
            return {k: self._deep(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._deep(v) for v in value]
        return value

    def _messages(self, messages: Sequence[ChatMessage]) -> list[ChatMessage]:
        return [m.model_copy(update={
            "content": self._text(m.content),
            "tool_calls": [c.model_copy(update={"arguments": self._deep(c.arguments)})
                           for c in m.tool_calls],
        }) for m in messages]

    def route(self, query: str, tool_cards: Sequence[ToolCard]) -> LLMRoute | None:
        with self._recorded("route"):
            return self.inner.route(self._text(query), tool_cards)

    def narrate(self, question: str, view: Mapping[str, JsonValue]) -> str:
        with self._recorded("narrate"):
            return self.inner.narrate(self._text(question), self._deep(view))

    def chat(self, messages: Sequence[ChatMessage]) -> str:
        with self._recorded("chat"):
            return self.inner.chat(self._messages(messages))

    def agent_step(
        self, messages: Sequence[ChatMessage], tool_cards: Sequence[ToolCard]
    ) -> AgentStep:
        with self._recorded("agent_step"):
            return self.inner.agent_step(self._messages(messages), tool_cards)


def guarded(provider: Provider, scrubber: Scrubber | None = None) -> GuardedProvider:
    """Wrap once: an already guarded provider is returned as is."""
    if isinstance(provider, GuardedProvider):
        return provider
    return GuardedProvider(provider, scrubber or NoScrub())


# ---------------------------------------------------------------- labels


class ProviderInfo(BaseModel):
    """Which language model answers. Never an endpoint URL or a key."""

    name: str  # "mock", "openai_compat", ...
    model: str | None  # the model id, when the provider has one
    mock: bool  # the zero-key mock: its "narration" is the deterministic template


def provider_info(provider: Any) -> ProviderInfo:
    """What ``provider`` declares about itself (a guarded provider: the one it wraps). A
    provider that declares nothing is named after its class and is not the mock."""
    inner = provider.inner if isinstance(provider, GuardedProvider) else provider
    name, model = getattr(inner, "name", None), getattr(inner, "model", None)
    return ProviderInfo(name=name if isinstance(name, str) and name else type(inner).__name__,
                        model=model if isinstance(model, str) and model else None,
                        mock=getattr(inner, "mock", False) is True)
