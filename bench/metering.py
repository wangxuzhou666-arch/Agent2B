"""A transparent wrapper around an OpenAI-SDK-shaped client: usage, latency and purpose per call.

Both systems talk to the endpoint through it (the baseline directly, twotrack through
``OpenAICompatProvider(client=...)``), so tokens are counted the same way for both and the
product's provider code stays untouched. It can pin ``temperature``; ``bench.run`` does not use
that: each system sends its own (the baseline's ``temperature`` argument, twotrack's provider
``temperature`` setting), so ``--omit-temperature`` reaches the wire as the product sends it.

Usage comes from the endpoint's ``usage`` field. When an endpoint omits it (or the client is an
offline fake), tokens are approximated from characters and the record says ``estimated``.
"""

import json
import math
import re
import time
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from twotrack.core.agent_loop import AGENT_SYSTEM
from twotrack.core.graph.builder import CHAT_SYSTEM
from twotrack.core.workflow import UNDERSTAND_SYSTEM
from twotrack.providers.openai_compat import NARRATE_SYSTEM, ROUTE_SYSTEM

from .baseline import BASELINE_SYSTEM

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")  # same ranges as core.routing.text


def approx_tokens(text: str) -> int:
    """chars / 4 for non-CJK text plus one token per CJK character (no tokenizer needed)."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return math.ceil((len(text) - cjk) / 4) + cjk


def request_text(kwargs: dict[str, Any]) -> str:
    """Everything a chat-completions request sends that costs prompt tokens."""
    parts = {"messages": kwargs.get("messages"), "tools": kwargs.get("tools"),
             "response_format": kwargs.get("response_format")}
    return json.dumps({k: v for k, v in parts.items() if v}, ensure_ascii=False, default=str)


def reply_text(reply: Any) -> tuple[str, int]:
    """(content plus tool-call arguments, number of tool calls) of a completion."""
    try:
        message = reply.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return "", 0
    calls = list(getattr(message, "tool_calls", None) or [])
    pieces = [getattr(message, "content", None) or ""]
    for call in calls:
        function = getattr(call, "function", None)
        pieces += [getattr(function, "name", "") or "", getattr(function, "arguments", "") or ""]
    return "".join(pieces), len(calls)


def _prefix(template: str) -> str:
    return template.split("{", 1)[0][:60]


_PURPOSES = ((ROUTE_SYSTEM, "route", True), (NARRATE_SYSTEM, "narrate", True),
             (_prefix(CHAT_SYSTEM), "chat", False), (_prefix(AGENT_SYSTEM), "agent", False),
             (_prefix(UNDERSTAND_SYSTEM), "understand", False),
             (_prefix(BASELINE_SYSTEM), "baseline", False))


def purpose_of(kwargs: dict[str, Any]) -> str:
    messages = kwargs.get("messages") or []
    system = messages[0].get("content", "") if messages and isinstance(messages[0], dict) else ""
    for text, name, exact in _PURPOSES:
        if (system == text) if exact else system.startswith(text):
            return name
    return "other"


@dataclass
class CallRecord:
    purpose: str
    ok: bool
    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated: bool
    prompt_chars: int
    completion_chars: int
    tool_calls: int = 0
    error: str | None = None  # exception type name only: messages may echo request details

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MeteredClient:
    """``pin_temperature`` overrides every request's temperature; None leaves requests as sent."""

    def __init__(self, inner: Any, *, pin_temperature: float | None = 0.0) -> None:
        self.inner = inner
        self.pin_temperature = pin_temperature
        self.records: list[CallRecord] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        if self.pin_temperature is not None:
            kwargs["temperature"] = self.pin_temperature
        request = request_text(kwargs)
        purpose = purpose_of(kwargs)
        started = time.perf_counter()
        try:
            reply = self.inner.chat.completions.create(**kwargs)
        except Exception as exc:
            self.records.append(CallRecord(
                purpose=purpose, ok=False, latency_ms=_ms(started), prompt_tokens=None,
                completion_tokens=None, estimated=True, prompt_chars=len(request),
                completion_chars=0, error=type(exc).__name__))
            raise
        text, calls = reply_text(reply)
        usage = getattr(reply, "usage", None)
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        estimated = bool(getattr(usage, "estimated", False)) or prompt is None
        self.records.append(CallRecord(
            purpose=purpose, ok=True, latency_ms=_ms(started),
            prompt_tokens=int(prompt) if prompt is not None else approx_tokens(request),
            completion_tokens=int(completion) if completion is not None
            else approx_tokens(text),
            estimated=estimated, prompt_chars=len(request), completion_chars=len(text),
            tool_calls=calls))
        return reply

    def take(self) -> list[CallRecord]:
        """Records since the last call to take (one question's worth)."""
        out, self.records = self.records, []
        return out


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
