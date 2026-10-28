"""The MCP listing describes a target by the approval it declares, not by its effect alone: an
external call that needs no approval runs at once, and the listing must not say it only
records a pending confirmation."""

from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from twotrack.core import Principal, Tool, ToolOutputs
from twotrack.mcp.server import TwotrackMcp


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def target(effect: str, approval: str) -> Tool:
    return Tool(name="t", description="Does a thing.", params_model=NoArgs,
                handler=lambda ctx, params: None, roles=("operator",), effect=effect,
                approval=approval, outputs=ToolOutputs(aggregates=(), columns=()),
                explorable=False)


def describe(tool: Tool) -> str:
    stub = SimpleNamespace(_rows_policy=lambda: "Rows are limited.")
    return TwotrackMcp._describe(stub, tool, Principal(user_id="olga", roles={"operator"}))


def test_a_target_that_needs_approval_only_records_a_pending_confirmation():
    for effect in ("write", "external"):
        text = describe(target(effect, "required"))
        assert f"Effect: {effect}." in text and "pending confirmation" in text


def test_an_external_target_without_approval_is_described_as_running_at_once():
    text = describe(target("external", "none"))
    assert "Effect: external." in text and "runs it at once" in text
    assert "pending confirmation" not in text


def test_a_read_changes_no_data():
    assert "Effect: read (changes no data)." in describe(target("read", "none"))
