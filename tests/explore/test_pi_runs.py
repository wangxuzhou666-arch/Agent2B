"""The open_task route planned by the real Pi worker, end to end through the server.

Each test asks the server a question no fixed flow covers, so it reaches ``open_task``, and the
engine's explorer is ``PiExplorer``: Node runs pi-agent-core, the model is scripted behind the
zero-key endpoint (or, for the key test, a real endpoint), and every call Pi makes comes back to
the server's pipeline. Every test also shows that nothing fell back to the built-in loop: each
model call carries Pi's system prompt and none the built-in loop's.

Skipped unless the worker is installed (see conftest); ``TWOTRACK_REQUIRE_PI=1`` fails instead.
"""

import json
import logging
import signal
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.explore.test_model_schema import keywords
from tests.explore.world import (
    BUILTIN_MARKER,
    PI_MARKER,
    QUERY,
    answer,
    call,
    calls,
    end_of_run,
)
from tests.fakes import ScriptedProvider
from tests.server.test_approval_refusals import approve, records
from twotrack.core import load_registry
from twotrack.core.explore.endpoint import sse_body
from twotrack.core.explore.pi import (
    EXPLORER_SYSTEM,
    KEY_ENV,
    PiExplorer,
    PiModel,
    params_schema,
    secret_forms,
)
from twotrack.core.i18n import say
from twotrack.core.model_schema import STANDARD_KEYWORDS, model_facing
from twotrack.core.params import params_model
from twotrack.providers import MockProvider
from twotrack.runtime import build_runtime
from twotrack.settings import Settings

pytestmark = pytest.mark.pi

SLOW_MODEL_SECONDS = 30  # a model step that outlasts any run's clock here


def gates(body: dict, status: str = "blocked") -> list[dict]:
    return [s["facts"] for s in body["trace"] if s["step"] == "gate" and s["status"] == status]


def audit_items(world) -> list[dict]:
    """Every audit record, oldest first."""
    found = world.api.get("/api/audit", "ada")
    assert found.status_code == 200
    return list(reversed(found.json()["items"]))


def stopped_early(reason: str) -> str:
    return say("explore_stopped_budget", "en", budget=say(f"budget_{reason}", "en"))


# ---------------------------------------------------------------- (a) Pi really ran


def test_pi_plans_the_run_and_its_last_step_names_the_worker(pi_harness, make_world):
    world = make_world(pi_harness, [call("low_stock"), answer("Here is where things stand.")])
    body = world.ask("olga")

    assert (body["answer"], body["answer_source"]) == ("Here is where things stand.", "model")
    step = end_of_run(body)
    assert step["status"] == "ok"
    assert step["facts"] == {
        "loop": "answer", "turn": 2, "runtime": "pi",
        "runtime_version": {**pi_harness.pins, "node": pi_harness.node_version},
        "model": "mock", "turns": 2, "stop_reason": "answered",
        "calls": [{"tool": "low_stock", "outcome": "ok"}]}
    # the worker said hello with the pinned versions before anything else, and ended the run
    hello, *_, final = world.sent("worker")
    assert hello == {"type": "hello", "protocol": 1, "versions": dict(pi_harness.pins),
                     "node": pi_harness.node_version}
    assert (final["type"], final["stop_reason"], final["forwarded"]) == ("final", "stop", 1)
    # the call it forwarded went through the pipeline and its audit
    (entry,) = audit_items(world)
    assert (entry["target"], entry["status"], entry["route_source"]) == ("low_stock", "ok",
                                                                        "agent")
    world.assert_planned_by_pi()
    assert len(world.model_calls()) == 2
    world.assert_reaped()


def test_the_model_endpoint_gets_every_tool_schema_with_standard_keywords_only(
        pi_harness, make_world, wire):
    world = make_world(pi_harness, [answer("Nothing to look up.")])
    body = world.ask("olga")

    (request,) = wire
    registry = world.runtime.registry
    (start,) = world.sent("host", "start")
    on_wire = {t["function"]["name"]: t["function"] for t in request["tools"]}
    # the host declared olga's exposed tools, and Pi passed on exactly those
    (exposed,) = [s["facts"]["exposed"] for s in body["trace"]
                  if s["step"] == "execute" and s["facts"].get("loop") == "start"]
    assert [t["name"] for t in start["tools"]] == exposed == sorted(on_wire)
    for spec in start["tools"]:
        target = registry.target(spec["name"])
        assert on_wire[spec["name"]]["description"] == spec["description"]
        # Pydantic's schema with every non-standard keyword taken out, at every depth
        assert on_wire[spec["name"]]["parameters"] == spec["parameters"] == params_schema(
            target) == model_facing(params_model(target).model_json_schema())
        assert keywords(spec["parameters"]) <= STANDARD_KEYWORDS
    # create_po's own annotations (a label and the words for "unset") stay on the host, whose
    # params model still carries them
    supplier = on_wire["create_po"]["parameters"]["properties"]["supplier_id"]
    assert "label" not in supplier and "unset" not in supplier
    host = registry.target("create_po").params_model.model_json_schema()
    assert host["properties"]["supplier_id"]["label"] == {"en": "Supplier", "zh": "供应商"}
    # the system prompt verbatim and the question; Pi sends no temperature of its own
    assert request["messages"][0] == {"role": "system", "content": EXPLORER_SYSTEM}
    assert QUERY in json.dumps(request["messages"][1])
    assert request["model"] == "mock" and "temperature" not in request
    world.assert_planned_by_pi()


def test_with_no_model_configured_pi_talks_to_the_mock(pi_harness, make_world, wire):
    world = make_world(pi_harness, provider=MockProvider())
    body = world.ask("olga")

    assert body["answer"] == say("offline_agent", "en")
    assert body["model_errors"] == []
    facts = end_of_run(body)["facts"]
    assert (facts["runtime"], facts["model"], facts["stop_reason"]) == ("pi", "mock",
                                                                       "answered")
    (request,) = wire
    assert request["messages"][0]["content"] == EXPLORER_SYSTEM
    assert BUILTIN_MARKER not in json.dumps(request)


def test_selecting_pi_at_start_up_checks_the_install_and_uses_it(pi_harness, tmp_path,
                                                                  monkeypatch):
    monkeypatch.delenv("TWOTRACK_PROVIDER", raising=False)  # the zero-key model
    runtime = build_runtime(Settings(data_dir=tmp_path / "data", explorer="pi",
                                     pi_harness_dir=pi_harness.root),
                            registry=load_registry(extra_paths=[]), provider=MockProvider())
    try:
        explorer = runtime.engine.explorer
        assert isinstance(explorer, PiExplorer)
        assert (explorer.harness.root, explorer.model) == (pi_harness.root, None)
    finally:
        runtime.close()


# ---------------------------------------------------------------- (b) a viewer's write


def test_a_viewer_cannot_reach_create_po_through_pi(pi_harness, make_world, wire):
    world = make_world(pi_harness)
    sku = world.first_sku()
    world.script(call("create_po", sku_id=sku, qty=5), call("create_po", sku_id=sku, qty=5),
                 answer("I cannot place orders."))
    orders = world.purchase_orders()
    body = world.ask("vera")

    # Pi was never offered the tool, and nothing it was offered writes
    (start,) = world.sent("host", "start")
    declared = {t["name"] for t in start["tools"]}
    assert "create_po" not in declared
    assert {world.runtime.registry.target(n).effect.value for n in declared} == {"read"}
    assert all("create_po" not in {t["function"]["name"] for t in r["tools"]} for r in wire)
    # so Pi refused both attempts itself: neither reached the host, each is traced and audited
    assert not world.sent("worker", "tool_call")
    assert gates(body) == [{"stage": "lookup", "target": "invalid", "reason": "unknown_tool",
                            "blocked_by": "pi"}] * 2
    assert [(e["user_id"], e["target"], e["status"], e["problem"], e["route_source"])
            for e in audit_items(world)] == [
        ("vera", "create_po", "blocked", "'create_po' is not an available tool", "agent")] * 2
    # nothing was written and nothing waits for anyone's approval
    assert body["approval"] is None and world.purchase_orders() == orders
    assert world.api.get("/api/approvals", "ada").json()["items"] == []
    assert body["answer"] == "I cannot place orders."
    assert end_of_run(body)["facts"]["calls"] == [{"tool": "invalid", "outcome": "blocked"}] * 2
    world.assert_planned_by_pi()


# ---------------------------------------------------------------- (c) budgets


def test_the_turn_budget_stops_pi_with_max_turns(pi_harness, make_world):
    world = make_world(pi_harness, lambda messages, cards: call("low_stock"),
                       explore_max_turns=2)
    body = world.ask("olga")

    step = end_of_run(body)
    assert (step["status"], step["facts"]["stop_reason"]) == ("blocked", "max_turns")
    assert step["facts"]["calls"] == [{"tool": "low_stock", "outcome": "ok"},
                                      {"tool": "low_stock", "outcome": "blocked"}]
    # the last turn's call was refused in the worker, so no result went unread
    assert [m["id"] for m in world.sent("worker", "tool_call")] == ["t1"]
    assert gates(body) == [{"stage": "budget", "target": "low_stock", "reason": "max_turns",
                            "blocked_by": "pi"}]
    assert [e["status"] for e in audit_items(world)] == ["ok", "blocked"]
    assert world.sent("worker", "final")[0]["stop_reason"] == "max_turns"
    # the answer says why the run stopped first, then what the run had found
    assert body["answer"].startswith(stopped_early("max_turns") + " Last result before it")
    assert body["answer_source"] == "template" and body["model_errors"] == []
    assert len(world.model_calls()) == 2  # one per turn, none after the budget
    world.assert_planned_by_pi()


def test_the_call_budget_stops_pi_with_max_tool_calls(pi_harness, make_world):
    world = make_world(pi_harness, [calls(("low_stock", {}), ("po_status", {}),
                                          ("late_shipments", {}))], explore_max_calls=2)
    body = world.ask("olga")

    step = end_of_run(body)
    assert (step["status"], step["facts"]["stop_reason"]) == ("blocked", "max_tool_calls")
    assert step["facts"]["calls"] == [{"tool": "low_stock", "outcome": "ok"},
                                      {"tool": "po_status", "outcome": "ok"},
                                      {"tool": "late_shipments", "outcome": "blocked"}]
    assert [m["id"] for m in world.sent("worker", "tool_call")] == ["t1", "t2"]
    assert gates(body) == [{"stage": "budget", "target": "late_shipments",
                            "reason": "max_tool_calls", "blocked_by": "pi"}]
    assert world.sent("worker", "final")[0]["stop_reason"] == "max_tool_calls"
    assert body["answer"].startswith(stopped_early("max_tool_calls") + " Last result")
    assert len(world.model_calls()) == 1
    world.assert_planned_by_pi()


# ---------------------------------------------------------------- (d) an operator's write


def test_a_write_pi_plans_waits_for_approval_then_runs_exactly_once(pi_harness, make_world):
    world = make_world(pi_harness)
    sku = world.first_sku()
    world.script(call("create_po", sku_id=sku, qty=5), answer("never asked"))
    orders = world.purchase_orders()
    body = world.ask("olga")

    # Pi forwarded the call as the model made it: create_po's schema passed Pi's own check
    assert [(m["name"], m["args"]) for m in world.sent("worker", "tool_call")] == [
        ("create_po", {"sku_id": sku, "qty": 5})]
    # the pipeline issued a grant; the run stopped there, and nothing ran
    grant_id = body["approval"]["grant_id"]
    step = end_of_run(body)
    assert (step["status"], step["facts"]["stop_reason"]) == ("blocked", "needs_confirmation")
    assert step["facts"]["calls"] == [{"tool": "create_po", "outcome": "needs_confirmation"}]
    (result,) = world.sent("host", "tool_result")
    assert (result["code"], result["terminate"]) == ("needs_confirmation", True)
    assert world.sent("host", "abort") == [{"type": "abort", "reason": "needs_confirmation"}]
    assert len(world.model_calls()) == 1 and world.purchase_orders() == orders

    done = approve(world.api, grant_id)
    assert done.status_code == 200
    assert (done.json()["status"], done.json()["target"]) == ("ok", "create_po")
    assert world.purchase_orders() == orders + 1

    again = approve(world.api, grant_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"
    assert world.purchase_orders() == orders + 1
    trail = records(world.api, grant_id)
    assert [(e["status"], e["problem"]) for e in trail] == [
        ("needs_confirmation", None), ("started", None), ("ok", None),
        ("blocked", "grant_used")]
    assert trail[0]["route_source"] == "agent" and trail[0]["user_id"] == "olga"
    world.assert_planned_by_pi()


def test_an_approval_whose_params_changed_after_pi_asked_is_refused(pi_harness, make_world):
    world = make_world(pi_harness)
    sku = world.first_sku()
    world.script(call("create_po", sku_id=sku, qty=5))
    orders = world.purchase_orders()
    grant_id = world.ask("olga")["approval"]["grant_id"]

    with world.runtime.database.transaction() as conn:
        conn.execute("UPDATE pending_request SET params = ? WHERE grant_id = ?",
                     (json.dumps({"sku_id": sku, "qty": 500}), grant_id))
    refused = approve(world.api, grant_id)
    assert refused.status_code == 200 and refused.json()["status"] == "blocked"
    assert world.purchase_orders() == orders
    (_, refusal) = records(world.api, grant_id)
    assert (refusal["status"], refusal["user_id"]) == ("blocked", "olga")
    world.assert_planned_by_pi()


# ---------------------------------------------------------------- (e) failures, no fallback


def test_a_worker_that_dies_mid_run_is_reported_and_nothing_falls_back(pi_harness,
                                                                       make_world):
    world = make_world(pi_harness)

    def crash(messages, cards):  # the worker dies while its model call is in flight
        world.launcher.kill()
        return call("low_stock")

    world.provider.steps = crash
    body = world.ask("olga")

    step = end_of_run(body)
    assert step["status"] == "error"
    assert (step["facts"]["error"], step["facts"]["stop_reason"]) == ("ExplorerCrashed",
                                                                      "worker_crashed")
    assert body["model_errors"] == [{"call": "agent_step", "error": "ExplorerCrashed",
                                     "http_status": None}]
    assert body["answer"] == say("explorer_failed", "en")
    assert not world.sent("worker", "final")
    assert len(world.model_calls()) == 1  # the built-in loop never asked the model
    world.assert_planned_by_pi()
    world.assert_reaped()
    assert world.launcher.procs[0].returncode == -signal.SIGKILL


def test_a_run_past_its_wall_clock_is_a_timeout_and_nothing_falls_back(pi_harness,
                                                                       make_world):
    def slow(messages, cards):
        time.sleep(SLOW_MODEL_SECONDS)
        return call("low_stock")

    world = make_world(pi_harness, slow, explore_timeout_seconds=1)
    began = time.monotonic()
    body = world.ask("olga")

    assert time.monotonic() - began < SLOW_MODEL_SECONDS / 2  # nothing waited for the model
    step = end_of_run(body)
    assert step["status"] == "error"
    assert (step["facts"]["error"], step["facts"]["stop_reason"]) == ("ExplorerTimeout",
                                                                      "timeout")
    assert body["model_errors"] == [{"call": "agent_step", "error": "ExplorerTimeout",
                                     "http_status": None}]
    assert body["answer"] == say("explorer_timeout", "en")
    # Both clocks start once the worker is up (the host's at its hello, the worker's at the
    # task), so however slowly Node started, the worker always got the task. Normally the
    # worker's runs out first (it aborts its model call and says so); the host's, a grace
    # later, is the backstop (it aborts the worker). Never both.
    assert len(world.sent("host", "start")) == 1
    finals = [m["stop_reason"] for m in world.sent("worker", "final")]
    aborts = [m["reason"] for m in world.sent("host", "abort")]
    assert finals in ([], ["wall_clock"]) and aborts in ([], ["timeout"])
    assert not (finals and aborts)
    # the model is never asked twice
    payloads = world.model_calls()
    assert len(payloads) <= 1 and all(PI_MARKER in p for p in payloads)
    assert not any(BUILTIN_MARKER in p for p in payloads)
    world.assert_reaped()


def test_a_failed_model_call_is_reported_once_and_nothing_falls_back(pi_harness, make_world):
    world = make_world(pi_harness, provider=ScriptedProvider(fail={"agent_step"}))
    body = world.ask("olga")

    step = end_of_run(body)
    assert (step["status"], step["facts"]["stop_reason"]) == ("error", "model_error")
    # recorded once, by the request's provider behind the endpoint; Pi did not retry
    assert body["model_errors"] == [{"call": "agent_step", "error": "ProviderError",
                                     "http_status": None}]
    assert body["answer"] == say("model_down", "en")
    final = world.sent("worker", "final")[0]
    assert (final["stop_reason"], final["http_status"]) == ("error", 502)
    assert len(world.model_calls()) == 1
    world.assert_planned_by_pi()


# ---------------------------------------------------------------- (f) the key


SENTINEL = "sk-twotrack-sentinel-7f3c9a1e5b2d4c6e8a0b"
# changes under JSON-escaping (the quotes, the backslash, é) and under URL-quoting (the space,
# / + ! ( ) and é), so that every form of it (pi.secret_forms) differs from the others; Latin-1
# only, as an HTTP header must be
HARD_KEY = 'sk-test "q"\\é/+!(x)~-0123456789'
JS_URL_SAFE = "!'()*"  # what encodeURIComponent leaves as it is and Python's quote does not


def echoed(key: str) -> str:
    """A 401 message that repeats ``key`` in every form an endpoint could: as it is, inside JSON
    (with and without non-ASCII escaped) and in a URL (quoted strictly and the JavaScript way)."""
    as_json = f"{json.dumps({'key': key})} {json.dumps({'key': key}, ensure_ascii=False)}"
    strict, js = urllib.parse.quote(key, safe=""), urllib.parse.quote(key, safe=JS_URL_SAFE)
    return (f"Incorrect API key provided: {key}. As JSON: {as_json}. In a URL: "
            f"https://example.invalid/?key={strict}&js={js}")


class EchoingModel:
    """An OpenAI-compatible endpoint that answers the first request with a tool call, then
    refuses every later one with a 401 whose message repeats the key it was sent, in every form
    (``echoed``)."""

    def __init__(self) -> None:
        self.authorizations: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                key = self.headers.get("Authorization", "")
                owner.authorizations.append(key)
                if len(owner.authorizations) == 1:
                    status, kind = 200, "text/event-stream"
                    body = sse_body(call("low_stock"), model="sentinel-model", request_number=1)
                else:
                    status, kind = 401, "application/json"
                    body = json.dumps({"error": {
                        "message": echoed(key.removeprefix("Bearer ")),
                        "type": "invalid_request_error", "code": "invalid_api_key"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def echoing_model():
    model = EchoingModel()
    yield model
    model.close()


@pytest.mark.parametrize("key", [SENTINEL, HARD_KEY], ids=["plain", "escaped-forms"])
def test_the_key_never_leaves_the_worker_environment(pi_harness, make_world, echoing_model,
                                                     caplog, key):
    caplog.set_level(logging.DEBUG)
    model = PiModel(model="sentinel-model", base_url=echoing_model.base_url, api_key=key)
    world = make_world(pi_harness, model=model)
    body = world.ask("olga")

    # the key was really used: the endpoint got it twice, and echoed it back on the 401
    assert echoing_model.authorizations == [f"Bearer {key}"] * 2
    step = end_of_run(body)
    assert (step["status"], step["facts"]["stop_reason"]) == ("error", "model_error")
    assert step["facts"]["model"] == "sentinel-model"
    assert body["model_errors"] == [{"call": "agent_step", "error": "ModelError",
                                     "http_status": 401}]
    # the echo came back redacted, in the worker's own line and in the host's log
    final = world.sent("worker", "final")[0]
    assert final["http_status"] == 401 and "[redacted]" in final["error"]
    assert "[redacted]" in caplog.text
    # the start line names the variable that holds the key, never the key
    (start,) = world.sent("host", "start")
    assert start["model"]["api_key_env"] == KEY_ENV
    # and the key is nowhere: protocol lines, trace, answer, audit, logs, reprs
    audit = audit_items(world)
    details = [world.api.get(f"/api/audit/{e['id']}", "ada").json() for e in audit]
    seen = [raw.decode() for _, raw in world.lines] + [
        json.dumps(body), json.dumps(audit), json.dumps(details), caplog.text,
        repr(model), repr(world.runtime.engine.explorer.model)]
    forms = secret_forms(key)
    assert key in forms and not [form for form in forms for text in seen if form in text]
    world.assert_reaped()


# ---------------------------------------------------------------- (g) Pi's own schema check


def test_a_call_pi_refuses_for_its_schema_is_audited_and_never_reaches_the_host(
        pi_harness, make_world):
    world = make_world(pi_harness, [call("create_po", qty="lots", bogus=1),
                                    answer("I need a SKU first.")])
    orders = world.purchase_orders()
    body = world.ask("olga")

    assert not world.sent("worker", "tool_call")
    (event,) = [e for e in world.sent("worker", "event") if e["name"] == "tool_blocked"]
    assert (event["tool"], event["reason"]) == ("create_po", "invalid_args")
    assert gates(body) == [{"stage": "params", "target": "create_po", "reason": "invalid_args",
                            "blocked_by": "pi"}]
    (entry,) = audit_items(world)
    assert (entry["user_id"], entry["target"], entry["status"], entry["route_source"]) == (
        "olga", "create_po", "invalid", "agent")
    assert entry["problem"].startswith('Validation failed for tool "create_po"')
    assert "Received arguments" not in entry["problem"]
    assert entry["params"] == {"qty": "lots", "bogus": 1}
    # the model was told why and answered; the run ended normally, and nothing was written
    assert "Validation failed" in world.model_calls()[1]
    assert (body["answer"], body["approval"]) == ("I need a SKU first.", None)
    facts = end_of_run(body)["facts"]
    assert (facts["stop_reason"], facts["calls"]) == (
        "answered", [{"tool": "create_po", "outcome": "invalid"}])
    assert world.purchase_orders() == orders
    world.assert_planned_by_pi()
