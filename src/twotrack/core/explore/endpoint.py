"""The zero-key model endpoint: a provider's ``agent_step`` behind OpenAI-compatible chat
completions, so the Pi worker runs for real with no model configured.

``ModelEndpoint`` serves ``POST /v1/chat/completions`` (streaming only) on 127.0.0.1, on a port
the OS picks, for one exploration run. Each request is turned back into the provider's own types
(``ChatMessage``s and ``ToolCard``s), answered by ``provider.agent_step`` and streamed back as
server-sent events: text, tool calls, a finish reason and token usage. The provider is the
request's guarded provider, so the zero-key mock answers exactly as it does in the built-in loop
(it says no model is configured and plans nothing, except for the documented exploration case,
for which it follows a fixed script: ``providers.mock_script``), what it is sent is scrubbed, and
a call that fails is recorded with the request's model errors.

A request must carry the run's random token as its bearer key, so no other local process can
drive the endpoint; the worker gets the token the way it would get a real key, through its
environment. Requests are size-capped; anything else is refused with a 4xx.
"""

import hmac
import json
import logging
import threading
import time
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from twotrack.providers.base import AgentStep, ChatMessage, Provider, ToolCall, ToolCard

log = logging.getLogger("twotrack.explore.endpoint")

MAX_REQUEST_BYTES = 8 * 1024 * 1024


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content parts: keep the text ones
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _arguments(raw: Any) -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}, True
    return (value, False) if isinstance(value, dict) else ({}, True)


def to_messages(wire: Iterable[Any]) -> list[ChatMessage]:
    """OpenAI-compatible request messages as the provider's own messages."""
    messages = []
    for m in wire:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("system", "developer"):
            messages.append(ChatMessage(role="system", content=_text(m.get("content"))))
        elif role == "user":
            messages.append(ChatMessage(role="user", content=_text(m.get("content"))))
        elif role == "assistant":
            calls = []
            for raw in m.get("tool_calls") or []:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                arguments, malformed = _arguments(function.get("arguments"))
                calls.append(ToolCall(id=str(raw.get("id") or ""), name=str(function.get("name")),
                                      arguments=arguments, malformed=malformed))
            messages.append(ChatMessage(role="assistant", content=_text(m.get("content")),
                                        tool_calls=calls))
        elif role == "tool":
            messages.append(ChatMessage(role="tool", content=_text(m.get("content")),
                                        tool_call_id=str(m.get("tool_call_id") or "")))
    return messages


def to_cards(tools: Iterable[Any]) -> list[ToolCard]:
    cards = []
    for t in tools:
        function = t.get("function") if isinstance(t, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            continue
        parameters = function.get("parameters")
        cards.append(ToolCard(name=function["name"], kind="tool",
                              description=str(function.get("description") or ""), effect="",
                              params_schema=parameters if isinstance(parameters, dict)
                              else {"type": "object"}))
    return cards


def sse_body(step: AgentStep, *, model: str, request_number: int) -> bytes:
    """The streamed reply to one request: one delta with the text and tool calls, a finish
    reason, a usage chunk, then ``[DONE]``."""
    cid = f"chatcmpl-{request_number}"

    def chunk(choices: list[dict[str, Any]], **extra: Any) -> str:
        body = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                "model": model, "choices": choices, **extra}
        return f"data: {json.dumps(body)}\n\n"

    delta: dict[str, Any] = {"role": "assistant", "content": step.text or None}
    if step.tool_calls:
        delta["tool_calls"] = [
            {"index": i, "id": c.id or f"call_{request_number}_{i}", "type": "function",
             "function": {"name": c.name,
                          # a malformed call keeps its shape: arguments that are not an object
                          "arguments": "[]" if c.malformed else json.dumps(c.arguments)}}
            for i, c in enumerate(step.tool_calls)]
    finish = "tool_calls" if step.tool_calls else "stop"
    parts = [chunk([{"index": 0, "delta": delta, "finish_reason": None}]),
             chunk([{"index": 0, "delta": {}, "finish_reason": finish}]),
             chunk([], usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
             "data: [DONE]\n\n"]
    return "".join(parts).encode()


class _Server(ThreadingHTTPServer):
    # a reply the worker no longer waits for (the run timed out or was aborted while the model
    # was still answering) never holds up close(): its thread is neither joined nor waited for
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request: Any, client_address: Any) -> None:
        # the worker hung up mid-reply (it was aborted or killed): nothing to report
        log.debug("model endpoint: a reply could not be delivered", exc_info=True)


class ModelEndpoint:
    """One run's endpoint. ``start()`` returns the base URL; ``close()`` stops it."""

    def __init__(self, provider: Provider, *, token: str) -> None:
        self.provider = provider
        self.token = token
        self.requests = 0
        self._server: _Server | None = None
        self._lock = threading.Lock()

    def start(self) -> str:
        endpoint = self

        class Handler(_Handler):
            owner = endpoint

        server = _Server(("127.0.0.1", 0), Handler)  # port 0: the OS picks a free one
        self._server = server
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}/v1"

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def answer(self, request: dict[str, Any]) -> bytes:
        with self._lock:
            self.requests += 1
            number = self.requests
        step = self.provider.agent_step(to_messages(request.get("messages") or []),
                                        to_cards(request.get("tools") or []))
        return sse_body(step, model=str(request.get("model") or "mock"), request_number=number)


class _Handler(BaseHTTPRequestHandler):
    owner: ModelEndpoint
    protocol_version = "HTTP/1.1"

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._reply(status, json.dumps({"error": {"message": message}}).encode())

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._error(404, "not found")
        expected = f"Bearer {self.owner.token}".encode()
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected):
            return self._error(401, "unauthorized")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if not 0 < size <= MAX_REQUEST_BYTES:
            return self._error(413, "request size not accepted")
        try:
            request = json.loads(self.rfile.read(size))
        except ValueError:
            return self._error(400, "not JSON")
        if not isinstance(request, dict) or request.get("stream") is not True:
            return self._error(400, "only streaming chat completions are served")
        try:
            body = self.owner.answer(request)
        except Exception:  # the guarded provider recorded it; say only that it failed
            return self._error(502, "model call failed")
        self._reply(200, body, "text/event-stream")

    def do_GET(self) -> None:
        self._error(404, "not found")

    def log_message(self, *args: Any) -> None:  # requests are not logged
        pass
