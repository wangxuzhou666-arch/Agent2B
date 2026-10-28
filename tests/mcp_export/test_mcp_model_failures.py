"""Over MCP, as over the REST API, a configured model that fails is never silent: the result names
each failed call (``model_errors``), and the audited narrate step names the one its template
text stands in for (``model_error``). A plain tool calls no model and reports none."""

import json
from types import SimpleNamespace

from mcp import Client
from pydantic import BaseModel, Field

from tests.server.fixtures.stock import PLUGIN
from twotrack.core import (
    Plugin,
    Provenance,
    Step,
    StepKind,
    ToolOutputs,
    ToolResult,
    Workflow,
    build_registry,
)
from twotrack.mcp import McpOptions, TwotrackMcp, build_server, resolve_identity
from twotrack.providers import OpenAICompatProvider

REFUSED = {"error": "BadRequestError", "http_status": 400}
COUNT = {"query": "how many items do we have"}


class BadRequestError(Exception):
    status_code = 400


class RefusingClient:
    """Every call is refused with HTTP 400, as a model that rejects the temperature it got."""

    def __init__(self) -> None:
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        raise BadRequestError("rejected: temperature")


def refusing() -> OpenAICompatProvider:
    return OpenAICompatProvider(model="a-model", client=RefusingClient())


# ---------------------------------------------------------------- two workflows with model steps


class Wanted(BaseModel):
    sku: str = Field(min_length=1)


def count_items(ctx, data) -> ToolResult:
    return ToolResult(aggregates={"items": ctx.store.count("item")}, message="Items counted.",
                      provenance=Provenance(tool="item_digest", row_count=0, as_of=ctx.now(),
                                            scope="all items"))


def look_up(ctx, wanted: Wanted) -> ToolResult:
    item = ctx.store.get("item", wanted.sku) or {}
    return ToolResult(aggregates={"qty": item.get("qty") or 0},
                      provenance=Provenance(tool="restock_check", row_count=0, as_of=ctx.now()))


WORKFLOWS = Plugin(name="digests", version="0.1.0", workflows=(
    Workflow(name="item_digest", description="Count the items and summarize the count.",
             roles=("viewer", "operator"), effect="read", approval="none", explorable=False,
             outputs=ToolOutputs(aggregates=("items",), columns=()),
             steps=(Step(id="count", kind=StepKind.DETERMINISTIC, description="Count the items.",
                         fn=count_items),
                    Step(id="narrate", kind=StepKind.LLM,
                         description="Summarize the item count.", fn=lambda ctx, result: ""))),
    Workflow(name="restock_check", description="Read a restock request, look the item up.",
             roles=("viewer", "operator"), effect="read", approval="none", explorable=False,
             outputs=ToolOutputs(aggregates=("qty",), columns=()),
             steps=(Step(id="understand", kind=StepKind.LLM, description="Which item is meant.",
                         fn=lambda ctx, data: json.dumps(data), output_model=Wanted),
                    Step(id="look_up", kind=StepKind.DETERMINISTIC,
                         description="Look the item up.", fn=look_up, input_model=Wanted))),
))


def registry():
    return build_registry([PLUGIN, WORKFLOWS])


def server(runtime, user="olga"):
    return build_server(TwotrackMcp(runtime, resolve_identity(runtime, user=user),
                                    McpOptions()))


def steps_of(runtime, audit_id) -> dict[str, dict]:
    return {s.step: s.facts for s in runtime.audit.get(audit_id).trace}


# ---------------------------------------------------------------- tests


async def test_ask_names_each_failed_model_call(make_runtime):
    runtime = make_runtime(provider=refusing())
    async with Client(server(runtime)) as client:
        result = await client.call_tool("ask", COUNT)
    data = result.structured_content
    # still answered, from code: rule routing and the template
    assert not result.is_error and data["status"] == "ok" and "6" in data["answer"]
    assert data["answer_source"] == "template"
    assert data["model_errors"] == [{"call": "route", **REFUSED}, {"call": "narrate", **REFUSED}]
    steps = steps_of(runtime, data["audit_ids"][-1])
    assert steps["narrate"]["model_error"] == {"call": "narrate", **REFUSED}
    assert steps["narrate"]["mock"] is False
    assert steps["respond"]["model_errors"] == data["model_errors"]
    assert "rejected" not in json.dumps(data)  # the endpoint's message is not passed on


async def test_a_workflow_names_its_failed_narration(make_runtime):
    runtime = make_runtime(provider=refusing(), registry=registry())
    async with Client(server(runtime)) as client:
        result = await client.call_tool("item_digest", {})
    data = result.structured_content
    assert not result.is_error and data["status"] == "ok"
    # the template stood in: the message, the row count, the figure
    assert data["summary"] == "Items counted. No rows matched (all items). Items: 6."
    assert data["model_errors"] == [{"call": "narrate", **REFUSED}]
    steps = steps_of(runtime, data["audit_id"])
    assert steps["narrate"]["source"] == "template"
    assert steps["narrate"]["model_error"] == {"call": "narrate", **REFUSED}
    assert steps["respond"]["model_errors"] == [{"call": "narrate", **REFUSED}]
    assert steps["export"]["channel"] == "mcp"  # the record still closes with what left


async def test_a_workflow_that_could_not_understand_says_the_model_failed(make_runtime):
    runtime = make_runtime(provider=refusing(), registry=registry())
    async with Client(server(runtime)) as client:
        result = await client.call_tool("restock_check", {"request": "the hex bolts"})
    data = result.structured_content
    assert result.is_error and data["status"] == "invalid" and data["problem"]
    # a request without details reads apart from a model that failed to read it
    assert data["model_errors"] == [{"call": "chat", **REFUSED}]


async def test_no_failed_call_is_an_empty_list(make_runtime):
    failing = make_runtime(provider=refusing())
    async with Client(server(failing)) as client:
        tool = await client.call_tool("item_count", {})  # a plain tool: no model call
    assert tool.structured_content["model_errors"] == []
    narrate = steps_of(failing, tool.structured_content["audit_id"])["narrate"]
    assert narrate["source"] == "template" and "model_error" not in narrate
    assert failing.provider.client.calls == 0

    mock = make_runtime()
    async with Client(server(mock)) as client:
        asked = await client.call_tool("ask", COUNT)
        digest = await client.call_tool("item_count", {})
    assert asked.structured_content["model_errors"] == []
    assert digest.structured_content["model_errors"] == []
    steps = steps_of(mock, asked.structured_content["audit_ids"][-1])
    assert steps["narrate"]["mock"] is True and "model_error" not in steps["narrate"]
    assert "model_errors" not in steps["respond"]
