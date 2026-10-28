"""A configured model that fails is never silent: the trace names each failed call (its class
and HTTP status, never the request) and the answer says that template narration stood in."""

import json
from types import SimpleNamespace

from tests.fakes import REGISTRY, FakePipeline, ScriptedProvider, fake_lock, make_ctx
from twotrack.core.graph import Engine, run_query
from twotrack.providers import MockProvider, ModelCallError, OpenAICompatProvider

QUESTION = "what is our stock level"


class BadRequestError(Exception):
    """Shaped like the SDK's HTTP errors, e.g. a model that refuses the temperature it got."""

    status_code = 400


class RefusingClient:
    """An OpenAI-shaped client whose endpoint answers every call with HTTP 400."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        raise BadRequestError("temperature: range is 1 to 1")


def ask(provider, query=QUESTION):
    engine = Engine(registry=REGISTRY, provider=provider, pipeline=FakePipeline(),
                    lock=fake_lock)
    return run_query(engine, make_ctx("viewer"), query)


def step(response, name):
    return next(s for s in response.trace if s.step == name)


def test_a_refused_call_is_named_in_the_trace_and_the_answer():
    client = RefusingClient()
    response = ask(OpenAICompatProvider(model="m", client=client))
    assert len(client.requests) == 2  # the routing opinion and the narration were both asked
    refused = {"error": "BadRequestError", "http_status": 400}
    # still answered, from code: rule routing and the template
    assert response.provenance.tool == "stock_level" and "15543" in response.answer
    assert response.answer_source == "template" and response.error is None
    assert response.model_errors == [ModelCallError(call="route", **refused),
                                     ModelCallError(call="narrate", **refused)]
    narrate = step(response, "narrate").facts
    assert narrate["mock"] is False and narrate["provider"] == "openai_compat"
    assert narrate["model_error"] == {"call": "narrate", **refused}
    assert step(response, "respond").facts["model_errors"] == [
        {"call": "route", **refused}, {"call": "narrate", **refused}]
    recorded = json.dumps([s.model_dump(mode="json") for s in response.trace])
    assert "temperature" not in recorded  # the endpoint's message is not recorded


def test_no_model_configured_reads_apart_from_a_failing_model():
    response = ask(MockProvider())
    narrate = step(response, "narrate").facts
    assert narrate["mock"] is True and "model_error" not in narrate
    assert "model_errors" not in step(response, "respond").facts
    assert response.model_errors == []


def test_template_text_from_the_numeric_lock_is_not_a_failure():
    response = ask(ScriptedProvider(narration="We expect 999 more next month."))
    narrate = step(response, "narrate").facts
    assert narrate["source"] == "template" and narrate["stripped"] == 1
    assert "model_error" not in narrate and response.model_errors == []


def test_a_failed_chat_call_is_named_where_its_template_text_stands():
    response = ask(ScriptedProvider(fail={"chat"}), "write a poem about the sea")
    assert response.answer_source == "template"
    assert step(response, "narrate").facts["model_error"] == {
        "call": "chat", "error": "ProviderError", "http_status": None}
    assert [e.call for e in response.model_errors] == ["chat"]


def test_a_failed_routing_call_alone_leaves_model_narration_standing():
    response = ask(ScriptedProvider(fail={"route"}))
    assert response.answer_source == "model"
    assert "model_error" not in step(response, "narrate").facts
    assert [e.call for e in response.model_errors] == ["route"]
