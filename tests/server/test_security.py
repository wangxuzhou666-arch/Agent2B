"""Transport-level protections: CORS, headers, body caps, envelopes, secrets, the contract."""

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from tests.server.conftest import PASSWORD, Api
from twotrack import cli
from twotrack.core.secrets import SecretFileError
from twotrack.runtime import build_runtime
from twotrack.server.app import create_app
from twotrack.settings import Settings

ORIGIN = "https://console.example.test"
REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC = {("get", "/api/health"), ("post", "/api/auth/login"), ("post", "/api/auth/demo")}


# ---------------------------------------------------------------- CORS


def test_only_configured_origins_get_cors_headers(make_runtime):
    api = Api(make_runtime(cors_origins=(ORIGIN,)))
    allowed = api.client.get("/api/health", headers={"Origin": ORIGIN})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers  # tokens, not cookies
    other = api.client.get("/api/health", headers={"Origin": "https://evil.example.test"})
    assert other.status_code == 200 and "access-control-allow-origin" not in other.headers


def test_preflight_from_a_foreign_origin_is_refused(make_runtime):
    api = Api(make_runtime(cors_origins=(ORIGIN,)))
    ask = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers":
           "authorization,content-type"}
    ok = api.client.options("/api/query", headers={"Origin": ORIGIN, **ask})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    bad = api.client.options("/api/query", headers={"Origin": "https://evil.example.test", **ask})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers


def test_the_console_may_send_the_language_it_wants_its_labels_in(make_runtime):
    api = Api(make_runtime(cors_origins=(ORIGIN,)))
    ask = {"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
           "Access-Control-Request-Headers": "authorization,accept-language"}
    allowed = api.client.options("/api/entities", headers=ask)
    assert allowed.status_code == 200
    assert "accept-language" in allowed.headers["access-control-allow-headers"].lower()


def test_no_origins_configured_means_no_cors_at_all(api):
    response = api.client.get("/api/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------- headers


@pytest.mark.parametrize("request_", [
    ("get", "/api/health", {}),
    ("get", "/api/auth/me", {}),  # 401
    ("get", "/api/nope", {}),  # 404
    ("post", "/api/auth/login", {"json": {}}),  # 422
    ("post", "/api/auth/login", {"content": b"x" * 20_000}),  # 413 from the body cap
])
def test_security_headers_on_every_kind_of_response(api, request_):
    method, path, kw = request_
    response = getattr(api.client, method)(path, **kw)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_security_headers_on_the_event_stream(api):
    response = api.post("/api/query", "vera", json={"query": "how many items"})
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-frame-options"] == "DENY"


def test_interactive_docs_and_schema_routes_are_not_served(api):
    for path in ("/docs", "/redoc", "/openapi.json", "/api/openapi.json", "/api/docs"):
        assert api.client.get(path).status_code == 404, path


# ---------------------------------------------------------------- body cap and envelopes


def test_oversize_json_body_is_rejected_by_content_length(api):
    response = api.post("/api/query?stream=false", "vera", json={"query": "x" * 9000})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_oversize_chunked_json_body_is_cut_off(api):
    def chunks():
        yield b'{"query": "'
        for _ in range(10):
            yield b"x" * 1000
        yield b'"}'

    response = api.client.post("/api/query?stream=false", content=chunks(),
                               headers={**api.headers("vera"), "Content-Type": "application/json"})
    assert response.status_code == 413


def test_small_bodies_and_reads_are_not_affected(api):
    assert api.post("/api/query?stream=false", "vera",
                    json={"query": "how many items"}).is_success
    assert api.get("/api/entities", "vera").is_success


@pytest.mark.parametrize(("method", "path", "kw", "status", "code"), [
    ("get", "/api/nope", {}, 404, "not_found"),
    ("get", "/api/health", {"__method": "delete"}, 405, "method_not_allowed"),
    ("post", "/api/auth/login", {"json": {"username": ""}}, 422, "validation_error"),
    ("get", "/api/approvals", {}, 401, "not_authenticated"),
])
def test_every_error_uses_the_envelope(api, method, path, kw, status, code):
    if kw.pop("__method", None):
        response = api.client.delete(path)
    else:
        response = getattr(api.client, method)(path, **kw)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code and error["message"]
    assert set(response.json()) == {"error"} and set(error) == {"code", "message"}


def test_unexpected_exceptions_hide_their_details(runtime, monkeypatch):
    from fastapi.testclient import TestClient

    def explode(self, user_id):
        raise RuntimeError("SELECT * FROM app_user -- secret detail")

    monkeypatch.setattr(type(runtime.users), "get", explode)
    client = TestClient(create_app(runtime), raise_server_exceptions=False)
    token, _ = runtime.tokens.issue("olga")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500 and "secret detail" not in response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert response.headers["x-frame-options"] == "DENY"


def test_secrets_never_reach_the_logs(api, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    login = api.client.post("/api/auth/login", json={"username": "olga", "password": PASSWORD})
    token = login.json()["access_token"]
    wrong_login = {"username": "olga", "password": "wrong-pass-123"}  # gitleaks:allow
    api.client.post("/api/auth/login", json=wrong_login)
    api.client.get("/api/auth/me", headers={"Authorization": "Bearer forged.token.value"})
    monkeypatch.setattr("twotrack.server.routes.query.stream_query",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    api.client.post("/api/query", headers={"Authorization": f"Bearer {token}"},
                    json={"query": "how many items"})
    for secret in (PASSWORD, "wrong-pass-123", token, "forged.token.value"):  # gitleaks:allow
        assert secret not in caplog.text


# ---------------------------------------------------------------- secrets on disk


def test_secret_and_database_are_created_private(runtime):
    settings = runtime.settings
    assert stat.S_IMODE(settings.secret_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.data_dir.stat().st_mode) == 0o700
    assert len(settings.secret_path.read_text().strip()) >= 32


def test_the_secret_is_reused_across_restarts(runtime, make_runtime):
    token, _ = runtime.tokens.issue("olga")
    before = runtime.settings.secret_path.read_text()
    again = build_runtime(runtime.settings, registry=runtime.registry)
    try:
        assert runtime.settings.secret_path.read_text() == before
        assert again.tokens.verify(token).user_id == "olga"
    finally:
        again.close()


def test_a_world_readable_secret_stops_the_start(runtime):
    path = runtime.settings.secret_path
    os.chmod(path, 0o644)
    with pytest.raises(SecretFileError, match="accessible by other users"):
        build_runtime(runtime.settings, registry=runtime.registry)


def test_fresh_install_has_no_accounts_and_no_demo(tmp_path):
    from tests.server.fixtures.stock import PLUGIN
    from twotrack.core import build_registry

    runtime = build_runtime(Settings(data_dir=tmp_path / "fresh"),
                            registry=build_registry([PLUGIN]))
    try:
        assert runtime.users.list() == []
        client = Api(runtime).client
        assert client.post("/api/auth/login",
                           json={"username": "admin", "password": "admin"}).status_code == 401
        assert client.post("/api/auth/demo", json={"role": "admin"}).status_code == 404
    finally:
        runtime.close()


# ---------------------------------------------------------------- the frozen contract


def test_openapi_export_matches_the_app_and_the_committed_file(api, tmp_path, capsys):
    out = tmp_path / "openapi.json"
    assert cli.main(["export-openapi", "--out", str(out)]) == 0
    exported = json.loads(out.read_text())
    assert exported == api.client.app.openapi()  # a live app and the schema-only app agree
    committed = json.loads((REPO_ROOT / "openapi.json").read_text())
    assert committed == exported, "openapi.json is stale: run `uv run twotrack export-openapi`"


def test_openapi_is_complete_enough_to_generate_a_client_from(api):
    spec = api.client.app.openapi()
    operation_ids = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            operation_ids.append(op["operationId"])
            assert op["summary"] and op["tags"], (method, path)
            ok = op["responses"]["200"]["content"]
            assert ok, (method, path)
            guarded = (method, path) not in PUBLIC
            assert bool(op.get("security")) == guarded, (method, path)
            if guarded:
                assert "401" in op["responses"], (method, path)
            for status, response in op["responses"].items():
                if status.startswith(("4", "5")):
                    schema = response["content"]["application/json"]["schema"]
                    assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}, (path, status)
    assert len(operation_ids) == len(set(operation_ids))
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "text/event-stream" in spec["paths"]["/api/query"]["post"]["responses"]["200"]["content"]


def test_no_response_model_can_carry_a_grant_token(api):
    schemas = api.client.app.openapi()["components"]["schemas"]
    for name, schema in schemas.items():
        assert "token" not in schema.get("properties", {}), name


def test_exported_contract_has_no_local_paths_or_secrets():
    text = (REPO_ROOT / "openapi.json").read_text()
    assert "/Users/" not in text and "/home/" not in text
