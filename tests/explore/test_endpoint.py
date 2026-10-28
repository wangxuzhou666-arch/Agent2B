"""The zero-key model endpoint: a provider's ``agent_step`` behind OpenAI-compatible streaming
chat completions, on 127.0.0.1, for one run (no Node needed).

A request is turned back into the provider's own messages and tool cards, answered by
``agent_step`` and streamed as server-sent events: one delta with the text and the tool calls,
a finish reason, a usage chunk, ``[DONE]``. Only a request with the run's token, on the one
path, streaming, within the size cap, is served.
"""

import http.client
import json

import httpx
import pytest

from tests.fakes import ScriptedProvider
from twotrack.core.explore.endpoint import (
    MAX_REQUEST_BYTES,
    ModelEndpoint,
    sse_body,
    to_cards,
    to_messages,
)
from twotrack.providers.base import AgentStep, GuardedProvider, ToolCall

TOKEN = "run-token-0123456789"
TOOL = {"type": "function", "function": {
    "name": "low_stock", "description": "SKUs below their reorder point.", "strict": False,
    "parameters": {"type": "object", "additionalProperties": False, "properties": {
        "criticality": {"anyOf": [{"enum": ["A", "B", "C"], "type": "string"},
                                  {"type": "null"}], "binds": "field:sku.criticality"}}}}}


def events(body: bytes) -> list:
    """The ``data:`` payloads of an SSE body, JSON-decoded (``[DONE]`` kept as text)."""
    frames = [f for f in body.decode().split("\n\n") if f]
    assert all(f.startswith("data: ") for f in frames)
    return [f[6:] if f[6:] == "[DONE]" else json.loads(f[6:]) for f in frames]


@pytest.fixture
def served():
    """``(provider, base_url, endpoint)`` of a running endpoint; the scripted provider (behind
    the request's ``GuardedProvider``) replays ``provider.steps``."""
    provider = ScriptedProvider()
    endpoint = ModelEndpoint(GuardedProvider(provider), token=TOKEN)
    base_url = endpoint.start()
    yield provider, base_url, endpoint
    endpoint.close()


def post(base_url: str, payload, *, token: str | None = TOKEN, path: str = "/chat/completions"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.post(base_url + path, json=payload, headers=headers, timeout=10)


def chat(**extra) -> dict:
    return {"model": "mock", "stream": True, "tools": [TOOL],
            "messages": [{"role": "system", "content": "You explore."},
                         {"role": "user", "content": "where do we stand"}], **extra}


# ---------------------------------------------------------------- the stream


def test_a_tool_call_streams_as_one_delta_then_the_finish_reason_and_usage(served):
    provider, base_url, _ = served
    provider.steps = [AgentStep(text="Looking.", tool_calls=[
        ToolCall(id="c-1", name="low_stock", arguments={"criticality": "A"}),
        ToolCall(name="po_status", arguments={})])]
    response = post(base_url, chat())

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    delta, finish, usage, done = events(response.content)
    assert delta["object"] == "chat.completion.chunk" and delta["model"] == "mock"
    (choice,) = delta["choices"]
    assert choice["delta"]["role"] == "assistant" and choice["delta"]["content"] == "Looking."
    assert choice["delta"]["tool_calls"] == [
        {"index": 0, "id": "c-1", "type": "function",
         "function": {"name": "low_stock", "arguments": '{"criticality": "A"}'}},
        {"index": 1, "id": "call_1_1", "type": "function",
         "function": {"name": "po_status", "arguments": "{}"}}]
    assert finish["choices"] == [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
    assert usage["choices"] == [] and usage["usage"]["total_tokens"] == 0
    assert done == "[DONE]"
    assert len({delta["id"], finish["id"], usage["id"]}) == 1


def test_a_text_answer_finishes_with_stop_and_no_tool_calls(served):
    provider, base_url, _ = served
    provider.steps = [AgentStep(text="All quiet.")]
    delta, finish, _, _ = events(post(base_url, chat()).content)
    assert delta["choices"][0]["delta"] == {"role": "assistant", "content": "All quiet."}
    assert finish["choices"][0]["finish_reason"] == "stop"


def test_a_malformed_call_keeps_its_shape_on_the_wire():
    step = AgentStep(tool_calls=[ToolCall(name="low_stock", arguments={}, malformed=True)])
    (delta, *_) = events(sse_body(step, model="m", request_number=3))
    (wire,) = delta["choices"][0]["delta"]["tool_calls"]
    assert wire["function"]["arguments"] == "[]" and wire["id"] == "call_3_0"


def test_each_request_is_answered_by_its_own_agent_step(served):
    provider, base_url, endpoint = served
    provider.steps = [AgentStep(text="one"), AgentStep(text="two")]
    texts = [events(post(base_url, chat()).content)[0]["choices"][0]["delta"]["content"]
             for _ in range(2)]
    assert texts == ["one", "two"] and endpoint.requests == 2


# ---------------------------------------------------------------- what the provider is handed


def test_the_request_reaches_the_provider_as_its_own_messages_and_cards(served):
    provider, base_url, _ = served
    seen = []
    provider.steps = lambda messages, cards: seen.append((messages, cards)) or AgentStep(text="ok")
    post(base_url, chat(messages=[
        {"role": "developer", "content": "You explore."},
        {"role": "user", "content": [{"type": "text", "text": "where "},
                                     {"type": "image_url", "image_url": {"url": "x"}},
                                     {"type": "text", "text": "do we stand"}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "low_stock", "arguments": '{"criticality": "A"}'}},
            {"id": "c2", "type": "function", "function": {"name": "po_status",
                                                          "arguments": "not json"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"low_stock_count\": 19}"},
        "not a message"]))

    ((messages, cards),) = seen
    assert [(m.role, m.content) for m in messages] == [
        ("system", "You explore."), ("user", "where do we stand"), ("assistant", ""),
        ("tool", '{"low_stock_count": 19}')]
    first, second = messages[2].tool_calls
    assert (first.id, first.name, first.arguments, first.malformed) == (
        "c1", "low_stock", {"criticality": "A"}, False)
    assert (second.name, second.arguments, second.malformed) == ("po_status", {}, True)
    assert messages[3].tool_call_id == "c1"
    (card,) = cards
    assert (card.name, card.description) == ("low_stock", "SKUs below their reorder point.")
    assert card.params_schema == TOOL["function"]["parameters"]  # unchanged, extra keywords too


def test_cards_without_a_usable_function_are_dropped():
    cards = to_cards([TOOL, {"type": "function"}, {"function": {"name": 7}}, "x",
                      {"function": {"name": "bare"}}])
    assert [c.name for c in cards] == ["low_stock", "bare"]
    assert cards[1].params_schema == {"type": "object"}
    assert to_messages([{"role": "narrator", "content": "?"}]) == []


# ---------------------------------------------------------------- what is refused


@pytest.mark.parametrize(("token", "status"), [(None, 401), ("wrong-token", 401)])
def test_only_the_runs_own_token_is_served(served, token, status):
    provider, base_url, _ = served
    response = post(base_url, chat(), token=token)
    assert response.status_code == status
    assert response.json() == {"error": {"message": "unauthorized"}}
    assert provider.received == []


def test_other_paths_and_methods_are_not_found(served):
    _, base_url, _ = served
    assert post(base_url, chat(), path="/completions").status_code == 404
    assert httpx.get(base_url + "/chat/completions", timeout=10).status_code == 404


@pytest.mark.parametrize(("payload", "status"), [
    ({**chat(), "stream": False}, 400),
    ([chat()], 400),
])
def test_only_streaming_chat_completions_are_served(served, payload, status):
    provider, base_url, _ = served
    assert post(base_url, payload).status_code == status
    assert provider.received == []


def raw_post(base_url: str, body: bytes, length: int) -> int:
    url = httpx.URL(base_url)
    conn = http.client.HTTPConnection(url.host, url.port, timeout=10)
    try:
        conn.putrequest("POST", url.path + "/chat/completions")
        conn.putheader("Authorization", f"Bearer {TOKEN}")
        conn.putheader("Content-Length", str(length))
        conn.endheaders()
        if body:
            conn.send(body)
        return conn.getresponse().status
    finally:
        conn.close()


def test_a_body_that_is_empty_too_big_or_not_json_is_refused(served):
    provider, base_url, _ = served
    assert raw_post(base_url, b"", 0) == 413
    assert raw_post(base_url, b"", MAX_REQUEST_BYTES + 1) == 413  # refused before it is read
    assert raw_post(base_url, b"{nope", 5) == 400
    assert provider.received == []


def test_a_failed_model_call_is_a_502_recorded_with_the_request(served):
    _, base_url, endpoint = served
    guarded = endpoint.provider
    guarded.inner.fail.add("agent_step")
    response = post(base_url, chat())
    assert response.status_code == 502
    assert response.json() == {"error": {"message": "model call failed"}}
    assert [(f.call, f.error) for f in guarded.failures] == [("agent_step", "ProviderError")]


def test_it_listens_on_loopback_on_a_port_the_os_picked(served):
    _, base_url, _ = served
    url = httpx.URL(base_url)
    assert url.host == "127.0.0.1" and url.port and url.path == "/v1"


def test_closing_it_stops_serving(served):
    _, base_url, endpoint = served
    endpoint.close()
    with pytest.raises(httpx.TransportError):
        post(base_url, chat())
    endpoint.close()  # closing twice is harmless
