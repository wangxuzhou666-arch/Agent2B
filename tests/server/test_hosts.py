"""The Host check (server.hosts): DNS rebinding.

A page that makes its own name resolve to 127.0.0.1 reaches the API as same-origin, so CORS does
not apply and the only thing that differs from a legitimate caller is the Host header. In demo
mode (passwordless admin sign-in), and in any mode once TWOTRACK_ALLOWED_HOSTS is set, a request
with a Host the server was not set up for is answered 400 before anything else runs.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.server.conftest import PASSWORD
from twotrack.server.app import create_app
from twotrack.server.hosts import HostCheckMiddleware, host_of

REBOUND = "rebind.attacker.example:8000"


def client_for(runtime, host: str) -> TestClient:
    return TestClient(create_app(runtime), base_url=f"http://{host}")


def sign_in_as_admin(client: TestClient):
    return client.post("/api/auth/demo", json={"role": "admin"})


# ---------------------------------------------------------------- demo mode


def test_a_rebound_host_cannot_sign_in_as_admin_in_demo_mode(make_runtime):
    client = client_for(make_runtime(demo_mode=True), REBOUND)
    refused = sign_in_as_admin(client)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "invalid_host"
    assert "access_token" not in refused.text
    assert "attacker" not in refused.text  # the message does not echo the host it refused
    assert "TWOTRACK_ALLOWED_HOSTS" in refused.json()["error"]["message"]  # it says what to do
    assert refused.headers["x-frame-options"] == "DENY"  # still the shared envelope and headers
    assert set(refused.json()) == {"error"}


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/api/health"), ("get", "/docs"), ("get", "/openapi.json"),
    ("post", "/api/auth/login"), ("get", "/api/auth/me"), ("post", "/api/query"),
])
def test_every_path_is_checked_not_only_sign_in(make_runtime, method, path):
    client = client_for(make_runtime(demo_mode=True), REBOUND)
    response = getattr(client, method)(path)
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_host"


def test_a_preflight_from_a_foreign_host_is_refused_before_cors_answers_it(make_runtime):
    origin = "http://localhost:3000"  # an origin CORS would accept: only the Host differs
    runtime = make_runtime(demo_mode=True, cors_origins=(origin,))
    ask = {"Origin": origin, "Access-Control-Request-Method": "POST"}
    allowed = client_for(runtime, "localhost:8000").options("/api/auth/demo", headers=ask)
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == origin
    refused = client_for(runtime, REBOUND).options("/api/auth/demo", headers=ask)
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_host"
    assert "access-control-allow-origin" not in refused.headers


@pytest.mark.parametrize("host", [
    "localhost", "localhost:8000", "LOCALHOST:3000", "127.0.0.1", "127.0.0.1:8000",
    "[::1]", "[::1]:8000"])
def test_demo_mode_answers_to_the_loopback_names_on_any_port(make_runtime, host):
    client = client_for(make_runtime(demo_mode=True), host)
    assert client.get("/api/health").status_code == 200
    assert sign_in_as_admin(client).status_code == 200


def test_demo_mode_adds_the_names_in_the_setting_to_the_loopback_ones(make_runtime):
    runtime = make_runtime(demo_mode=True, allowed_hosts=(
        "demo.example.test", "10.0.0.5", "[fd00::5]"))
    for host in ("demo.example.test", "demo.example.test:8443", "DEMO.example.test",
                 "10.0.0.5:8000", "[fd00::5]:8000", "localhost:8000"):
        assert client_for(runtime, host).get("/api/health").status_code == 200, host
    for host in ("other.example.test", "demo.example.test.attacker.example",
                 "evil-demo.example.test", "10.0.0.6", "10.0.0.5.attacker.example"):
        assert client_for(runtime, host).get("/api/health").status_code == 400, host


# ---------------------------------------------------------------- outside demo mode


def test_outside_demo_mode_nothing_is_checked_while_the_setting_is_unset(make_runtime):
    client = client_for(make_runtime(), "twotrack.proxy.example")
    assert client.get("/api/health").status_code == 200
    signed_in = client.post("/api/auth/login", json={"username": "olga", "password": PASSWORD})
    assert signed_in.status_code == 200


def test_the_setting_is_enforced_outside_demo_mode(make_runtime):
    runtime = make_runtime(allowed_hosts=("app.example.test",))
    assert runtime.settings.demo_mode is False
    listed = client_for(runtime, "app.example.test:8443")
    assert listed.get("/api/health").status_code == 200
    login = {"username": "olga", "password": PASSWORD}
    assert listed.post("/api/auth/login", json=login).status_code == 200
    foreign = client_for(runtime, "twotrack.proxy.example")
    assert foreign.get("/api/health").status_code == 400
    refused = foreign.post("/api/auth/login", json=login)  # a right password changes nothing
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_host"
    # a health check or curl on the machine itself still reaches it
    assert client_for(runtime, "127.0.0.1:8000").get("/api/health").status_code == 200


# ---------------------------------------------------------------- malformed headers


@pytest.mark.parametrize("header", [
    "localhost:abc", "localhost:", "localhost:80:90", "localhost:123456",
    "localhost@attacker.example", "attacker.example#localhost", "attacker.example/localhost",
    "localhost attacker.example", "localhost,attacker.example", "localhost.", "[::1", "::1",
    "[::1]:", "[::1]x", "*", "."])
def test_a_malformed_host_header_is_refused(make_runtime, header):
    client = client_for(make_runtime(demo_mode=True), "localhost")
    refused = client.get("/api/health", headers={"Host": header})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_host"


def test_an_empty_host_header_is_refused(make_runtime):
    client = client_for(make_runtime(demo_mode=True), "localhost")
    assert client.get("/api/health", headers={"Host": ""}).status_code == 400


def test_two_host_headers_are_refused_whichever_comes_first(make_runtime):
    client = client_for(make_runtime(demo_mode=True), "localhost")
    for pair in (("localhost", "attacker.example"), ("attacker.example", "localhost")):
        refused = client.get("/api/health", headers=[("Host", pair[0]), ("Host", pair[1])])
        assert refused.status_code == 400, pair


def test_a_request_without_a_host_header_is_refused():
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope)

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    async def call(headers):
        scope = {"type": "http", "method": "GET", "path": "/api/health", "headers": headers}
        await HostCheckMiddleware(inner)(scope, receive, send)

    asyncio.run(call([]))
    assert reached == [] and sent[0]["status"] == 400
    asyncio.run(call([(b"host", b"localhost")]))  # the same call with a Host reaches the app
    assert len(reached) == 1


@pytest.mark.parametrize(("header", "host"), [
    ("localhost", "localhost"), ("LocalHost:8000", "localhost"), ("127.0.0.1:1", "127.0.0.1"),
    ("[::1]:65535", "[::1]"), ("a.b-c_d.example:80", "a.b-c_d.example"),
    ("localhost:", None), ("", None), ("a b", None), ("-a.example", None), ("a.example-", None),
    ("[::1", None), ("a.example:123456", None), ("a.example\n", None), ("é.example", None),
])
def test_host_of_reads_exactly_host_or_host_and_port(header, host):
    assert host_of(header) == host
