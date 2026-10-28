"""The MCP export, driven by the official SDK's client against the server in process."""

import json
import os
import subprocess
import sys

import pytest
from mcp import Client, StdioServerParameters

from tests.server.conftest import Api
from twotrack import cli
from twotrack.core.audit import AuditQuery
from twotrack.mcp import IdentityError, McpOptions, TwotrackMcp, build_server, resolve_identity

RESTRICTED = {"unit_cost", "supplier", "vendor_ref", "rent"}  # the fixture's restricted fields


def server(runtime, user="olga", **options):
    app = TwotrackMcp(runtime, resolve_identity(runtime, user=user), McpOptions(**options))
    return build_server(app)


def dump(result) -> str:
    return json.dumps(result.structured_content) + "\n".join(c.text for c in result.content)


# ---------------------------------------------------------------- identity


def test_no_identity_no_server(runtime, capsys):
    with pytest.raises(IdentityError, match="identity is required"):
        resolve_identity(runtime)
    with pytest.raises(IdentityError, match="only accepted in demo mode"):
        resolve_identity(runtime, role="admin")
    with pytest.raises(IdentityError, match="does not exist"):
        resolve_identity(runtime, user="nobody")
    with runtime.database.transaction() as conn:
        conn.execute("UPDATE app_user SET disabled = 1 WHERE user_id = 'omar'")
    with pytest.raises(IdentityError, match="disabled"):
        resolve_identity(runtime, user="omar")
    assert cli.main(["mcp"]) == 2
    assert "needs an identity" in capsys.readouterr().err


def test_demo_roles_only_in_demo_mode(make_runtime, tmp_path, monkeypatch, capsys):
    identity = resolve_identity(make_runtime(demo_mode=True), role="viewer")
    assert identity.user_id == "demo-viewer" and identity.demo
    monkeypatch.delenv("TWOTRACK_DEMO_MODE", raising=False)
    data = tmp_path / "never"
    assert cli.main(["--data-dir", str(data), "mcp", "--role", "admin"]) == 2
    assert "only accepted in demo mode" in capsys.readouterr().err
    assert not data.exists()  # refused before any data was touched


async def test_a_user_disabled_while_serving_loses_access(runtime):
    async with Client(server(runtime, user="omar")) as client:
        with runtime.database.transaction() as conn:
            conn.execute("UPDATE app_user SET disabled = 1 WHERE user_id = 'omar'")
        assert (await client.list_tools()).tools == []
        result = await client.call_tool("item_count", {})
        assert result.is_error and "disabled" in result.content[0].text


# ---------------------------------------------------------------- listing


async def test_the_tool_list_is_the_users_candidate_set(runtime):
    async with Client(server(runtime, user="vera")) as client:
        viewer = {t.name: t for t in (await client.list_tools()).tools}
    async with Client(server(runtime, user="olga")) as client:
        operator = {t.name: t for t in (await client.list_tools()).tools}
    assert "set_item_qty" not in viewer and "set_item_qty" in operator
    assert {"item_count", "item_list", "ask"} <= set(viewer)
    write = operator["set_item_qty"]
    assert set(write.input_schema["properties"]) == {"sku", "qty"}
    assert "Effect: write" in write.description and "Approvals" in write.description
    assert write.annotations.read_only_hint is False
    assert operator["item_count"].annotations.read_only_hint is True
    assert "never table rows" in operator["item_count"].description
    assert not [name for name in operator if "approv" in name]  # no way to approve over MCP


# ---------------------------------------------------------------- reads and the rows policy


async def test_a_read_returns_figures_and_no_rows_by_default(runtime):
    async with Client(server(runtime)) as client:
        result = await client.call_tool("item_list", {})
    data = result.structured_content
    assert not result.is_error and data["status"] == "ok" and data["rows_included"] is False
    assert "rows" not in data and data["row_count"] == 6 and data["aggregates"] == {"listed": 6}
    assert data["labels"] == {"listed": "Listed"} and data["provenance"]["tool"] == "item_list"
    assert not RESTRICTED & set(data["columns"])
    text = dump(result)
    assert "Hex bolt" not in text and "Supplier Alpha" not in text  # no row value anywhere
    assert "stay in the twotrack console" in result.content[0].text


async def test_rows_are_opt_in_capped_and_never_restricted(runtime):
    console = Api(runtime).post("/api/query?stream=false", "olga",
                                json={"query": "x", "explicit_target": "item_list"}).json()
    assert {"unit_cost", "supplier"} <= set(console["rows"][0])  # olga may see them in the UI
    async with Client(server(runtime, include_rows=True, max_rows=2)) as client:
        result = await client.call_tool("item_list", {})
    data = result.structured_content
    assert data["rows_included"] is True and data["rows_truncated"] is True
    assert len(data["rows"]) == 2 and data["rows"][0]["name"] == "Hex bolt"
    assert not RESTRICTED & {k for row in data["rows"] for k in row}
    assert not RESTRICTED & set(data["columns"])
    entry = runtime.audit.get(data["audit_id"])
    assert entry.trace[-1].step == "export" and entry.trace[-1].facts["rows_sent"] == 2


async def test_restricted_values_are_redacted_wherever_they_appear(tmp_path):
    """A tool that echoes a restricted value in its message (an authoring mistake) still
    cannot leak it: every result passes the egress guard, as any model payload does."""
    from tests.server.conftest import ITEMS, PASSWORD
    from tests.server.fixtures.stock import PLUGIN
    from twotrack.core import Plugin, Provenance, Tool, ToolOutputs, ToolResult, build_registry
    from twotrack.providers import MockProvider
    from twotrack.runtime import build_runtime
    from twotrack.settings import Settings

    def leaky(ctx, params):
        first = ctx.store.get("item", "A-1")
        return ToolResult(aggregates={"items": 1}, message=f"Buy from {first['supplier']}.",
                          provenance=Provenance(tool="leaky_note", row_count=0,
                                                as_of=ctx.now()))

    tool = Tool(name="leaky_note", description="A careless report.",
                params_model=PLUGIN.tools[0].params_model, handler=leaky,
                roles=("operator",), effect="read", approval="none", explorable=False,
                outputs=ToolOutputs(aggregates=("items",), columns=()))
    runtime = build_runtime(Settings(data_dir=tmp_path / "d"), provider=MockProvider(),
                            registry=build_registry([PLUGIN, Plugin(name="leak", version="1",
                                                                    tools=(tool,))]))
    try:
        runtime.store.upsert_many("item", ITEMS)
        runtime.users.add("olga", PASSWORD, ["operator"])
        async with Client(server(runtime)) as client:
            result = await client.call_tool("leaky_note", {})
        assert "Supplier Alpha" not in dump(result) and "[REDACTED]" in dump(result)
        entry = runtime.audit.get(result.structured_content["audit_id"])
        assert entry.trace[-1].facts["redacted"] >= 2  # in the summary and in the message
    finally:
        runtime.close()


# ---------------------------------------------------------------- writes


async def test_a_write_waits_for_approval_in_the_console(runtime):
    async with Client(server(runtime)) as client:
        result = await client.call_tool("set_item_qty", {"sku": "A-1", "qty": 3})
    data = result.structured_content
    assert not result.is_error and data["status"] == "needs_confirmation"
    assert "Approvals" in result.content[0].text and runtime.store.get("item", "A-1")["qty"] == 40
    pending = runtime.approvals.get(data["grant_id"])
    assert pending.route_source == "mcp" and pending.token not in dump(result)

    api = Api(runtime)
    listed = api.get("/api/approvals", "olga").json()["items"]
    assert [(i["grant_id"], i["route_source"]) for i in listed] == [(data["grant_id"], "mcp")]
    assert api.post(f"/api/approvals/{data['grant_id']}/approve", "olga").json()["status"] == "ok"
    assert runtime.store.get("item", "A-1")["qty"] == 3
    executed = runtime.audit.list(AuditQuery(status="ok", target="set_item_qty")).items[0]
    assert executed.route_source == "mcp" and executed.parent_id == data["audit_id"]


async def test_invalid_params_come_back_as_a_tool_error(runtime):
    async with Client(server(runtime)) as client:
        result = await client.call_tool("set_item_qty", {"sku": "A-1", "qty": -1})
    assert result.is_error and "qty" in result.content[0].text
    assert runtime.grants.pending() == []


# ---------------------------------------------------------------- audit


async def test_every_call_is_audited_with_source_mcp(runtime):
    async with Client(server(runtime)) as client:
        await client.call_tool("item_count", {})
    entry = runtime.audit.list(AuditQuery(route_source="mcp")).items[0]
    detail = runtime.audit.get(entry.id)
    assert [s.step for s in detail.trace] == [
        "candidates", "understand", "gate", "execute", "narrate", "respond", "export"]
    assert detail.trace[1].source == "mcp" and detail.request_id
    assert detail.trace[-1].facts == {"channel": "mcp", "rows_sent": 0, "rows_withheld": 0,
                                      "redacted": 0}


async def test_a_target_outside_reach_is_refused_and_still_audited(runtime):
    async with Client(server(runtime, user="vera")) as client:
        forbidden = await client.call_tool("set_item_qty", {"sku": "A-1", "qty": 1})
        unknown = await client.call_tool("drop_tables", {})
    assert forbidden.is_error and unknown.is_error
    assert forbidden.content[0].text == "'set_item_qty' is not available to you."
    assert unknown.content[0].text == "'drop_tables' is not available to you."
    statuses = {e.status.value for e in runtime.audit.list(AuditQuery(route_source="mcp")).items}
    assert statuses == {"blocked", "invalid"}


async def test_ask_runs_the_engine_and_keeps_the_rows_policy(runtime):
    async with Client(server(runtime)) as client:
        result = await client.call_tool("ask", {"query": "how many items do we have"})
        empty = await client.call_tool("ask", {"query": "  "})
    data = result.structured_content
    assert data["decision"]["source"] == "rule" and data["decision"]["target"] == "item_count"
    assert "6" in data["answer"] and "rows" not in data and data["audit_ids"]
    assert runtime.audit.get(data["audit_ids"][-1]).trace[-1].step == "export"
    assert empty.is_error


# ---------------------------------------------------------------- the real stdio entry point


async def test_the_stdio_server_speaks_only_protocol_on_stdout(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "twotrack.cli", "mcp", "--role", "viewer"],
        env={"TWOTRACK_DEMO_MODE": "1", "TWOTRACK_DATA_DIR": str(tmp_path / "data")})
    async with Client(params) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool("low_stock", {})
    assert {"low_stock", "ask"} <= names and "create_po" not in names  # a viewer
    assert result.structured_content["status"] == "ok"
    assert "rows" not in result.structured_content


@pytest.mark.parametrize(("env", "code", "message"), [
    ({"TWOTRACK_EXPLORER": "bogus"}, 2,
     "error: TWOTRACK_EXPLORER must be one of builtin/pi"),
    ({"TWOTRACK_EXPLORE_TOOLS": "no_such_tool"}, 1,
     "error: cannot start: TWOTRACK_EXPLORE_TOOLS names no_such_tool"),
])
def test_a_bad_exploration_setting_stops_the_server_and_stdout_stays_empty(
        tmp_path, env, code, message):
    """The MCP server builds the runtime ``serve`` builds, so the exploration settings are
    checked here too, with ``TWOTRACK_ALLOW_OPEN_TASK`` unset. stdout is the protocol channel: the
    reason goes to stderr and nothing reaches stdout. A rejected value is exit 2 (``Settings``);
    an allowlist name no plugin provides is exit 1 (``build_runtime``)."""
    data = tmp_path / "data"
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TWOTRACK_")}
    done = subprocess.run(
        [sys.executable, "-m", "twotrack.cli", "mcp", "--user", "olga"], capture_output=True,
        text=True, timeout=60, stdin=subprocess.DEVNULL, check=False,
        env={**clean, **env, "TWOTRACK_DATA_DIR": str(data)})
    assert done.returncode == code and done.stdout == ""
    assert message in done.stderr
    assert not data.exists()  # stopped before any data was touched
