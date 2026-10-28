"""Sign-in: passwords, tokens, demo mode. (The throttle is in test_throttle.py.)"""

import time
from dataclasses import replace

import jwt
import pytest

from tests.server.conftest import PASSWORD, Api
from twotrack.core import Role
from twotrack.server.auth import (
    DEMO_USERS,
    UserExistsError,
    UserNotFoundError,
    hash_password,
    verify_password,
)


def login(api, username="olga", password=PASSWORD):
    return api.client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_returns_a_token_that_me_accepts(api):
    response = login(api)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["user"] == {"user_id": "olga",
                                                              "roles": ["operator"]}
    me = api.client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["user_id"] == "olga" and me.json()["demo_mode"] is False


def test_wrong_password_and_unknown_user_look_identical(api):
    wrong = login(api, "olga", "not-the-password")
    unknown = login(api, "nobody", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_repeated_failures_are_throttled_then_a_right_password_is_still_refused(api):
    for _ in range(5):
        assert login(api, "vera", "wrong-wrong-1").status_code == 401
    blocked = login(api, "vera", PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"
    assert int(blocked.headers["Retry-After"]) >= 1
    assert login(api, "olga").status_code == 200  # other accounts are not locked out


@pytest.mark.parametrize("header", [None, "Bearer not-a-jwt", "Basic abc", "Bearer "])
def test_protected_endpoints_refuse_missing_or_malformed_tokens(api, header):
    headers = {"Authorization": header} if header else {}
    response = api.client.get("/api/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] in {"not_authenticated", "invalid_token"}


def test_expired_token_is_refused_with_its_own_code(api):
    token, _ = api.runtime.tokens.issue("olga", ttl_seconds=-5)
    response = api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "token_expired"


def test_token_signed_with_another_secret_is_refused(api):
    forged = jwt.encode({"sub": "ada", "iat": int(time.time()), "exp": int(time.time()) + 600},
                        "x" * 48, algorithm="HS256")
    response = api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "invalid_token"


def test_unsigned_alg_none_token_is_refused(api):
    now = int(time.time())
    unsigned = jwt.encode({"sub": "ada", "iat": now, "exp": now + 600}, None, algorithm="none")
    response = api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {unsigned}"})
    assert response.status_code == 401


def test_token_without_expiry_is_refused(api):
    secret = api.runtime.tokens._secret
    no_exp = jwt.encode({"sub": "ada", "iat": int(time.time())}, secret, algorithm="HS256")
    response = api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {no_exp}"})
    assert response.status_code == 401


def test_token_for_a_deleted_user_is_refused(api):
    token, _ = api.runtime.tokens.issue("ghost")
    response = api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_disabled_user_loses_access_at_once_and_gets_it_back_when_enabled(api):
    headers = api.headers("olga")
    api.runtime.users.set_disabled("olga", True)
    assert api.client.get("/api/auth/me", headers=headers).status_code == 401
    refused = login(api, "olga")  # the right password
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "invalid_credentials"
    api.runtime.users.set_disabled("olga", False)
    assert api.client.get("/api/auth/me", headers=headers).status_code == 200  # the same token
    assert login(api, "olga").status_code == 200


def test_health_and_login_need_no_token_but_everything_else_does(api):
    assert api.client.get("/api/health").status_code == 200
    for path in ("/api/approvals", "/api/audit", "/api/entities", "/api/audit/stats"):
        assert api.client.get(path).status_code == 401, path
    assert api.client.post("/api/query", json={"query": "x"}).status_code == 401


def test_health_reports_version_and_demo_flag(api):
    body = api.client.get("/api/health").json()
    assert body["status"] == "ok" and body["demo_mode"] is False and body["version"]


def test_health_says_the_zero_key_provider_is_the_mock(api):
    assert api.client.get("/api/health").json()["provider"] == {
        "name": "mock", "model": None, "mock": True}


def test_health_reports_the_upload_guards_limits(api):
    limits = api.client.get("/api/health").json()["limits"]
    guard = api.runtime.importer.limits
    assert limits == {"max_upload_bytes": guard.max_bytes, "max_rows": guard.max_rows}
    assert limits["max_upload_bytes"] == api.runtime.settings.max_upload_bytes == 4096


def test_health_names_a_real_model_but_never_its_endpoint_or_key(make_runtime):
    from twotrack.providers import OpenAICompatProvider

    real = OpenAICompatProvider(model="m-1", base_url="https://llm.internal.example/v1",
                                api_key="test-key-never-shown")  # no call is made
    response = Api(make_runtime(provider=real)).client.get("/api/health")
    assert response.json()["provider"] == {"name": "openai_compat", "model": "m-1",
                                           "mock": False}
    assert "internal.example" not in response.text and "test-key-never-shown" not in response.text


# ---------------------------------------------------------------- demo mode


def test_demo_endpoint_is_404_when_demo_mode_is_off(api):
    response = api.client.post("/api/auth/demo", json={"role": "admin"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def test_demo_endpoint_issues_a_role_token_when_on(make_runtime):
    demo = Api(make_runtime(demo_mode=True))
    for role in ("admin", "operator", "viewer"):
        issued = demo.client.post("/api/auth/demo", json={"role": role})
        assert issued.status_code == 200 and issued.json()["demo_mode"] is True
        token = issued.json()["access_token"]
        me = demo.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json() == {"user_id": f"demo-{role}", "roles": [role], "demo_mode": True,
                             "expires_at": me.json()["expires_at"]}
    assert demo.client.get("/api/health").json()["demo_mode"] is True


def test_demo_rejects_unknown_roles(make_runtime):
    demo = Api(make_runtime(demo_mode=True))
    assert demo.client.post("/api/auth/demo", json={"role": "root"}).status_code == 422


def test_demo_tokens_stop_working_when_demo_mode_is_turned_off(make_runtime):
    demo = Api(make_runtime(demo_mode=True))
    token = demo.client.post("/api/auth/demo", json={"role": "admin"}).json()["access_token"]
    demo.runtime.settings = replace(demo.runtime.settings, demo_mode=False)
    response = demo.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_real_user_cannot_claim_a_demo_identity(api):
    token, _ = api.runtime.tokens.issue("demo-admin", demo=True)  # signed, but demo mode is off
    assert api.client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_demo_user_names_are_reserved(runtime):
    with pytest.raises(ValueError, match="reserved"):
        runtime.users.add("demo-admin", PASSWORD, ["admin"])
    assert set(DEMO_USERS) == {"demo-admin", "demo-operator", "demo-viewer"}


# ---------------------------------------------------------------- hashing and users


def test_password_hash_is_salted_and_verifies():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b and a.startswith("scrypt$")
    assert verify_password(PASSWORD, a) and not verify_password("other-password", a)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$1$2$3$x", "scrypt$99$8$1$AA$AA",
                                    "md5$16384$8$1$AA$AA"])
def test_unparseable_or_foreign_hashes_never_verify(stored):
    assert verify_password(PASSWORD, stored) is False


def test_user_store_rules(runtime):
    with pytest.raises(UserExistsError):
        runtime.users.add("olga", PASSWORD, ["operator"])
    with pytest.raises(ValueError, match="at least"):
        runtime.users.add("newbie", "short", ["viewer"])
    with pytest.raises(ValueError, match="roles"):
        runtime.users.add("newbie", PASSWORD, ["superuser"])
    with pytest.raises(ValueError, match="username"):
        runtime.users.add("Bad Name", PASSWORD, ["viewer"])
    assert [u.user_id for u in runtime.users.list()] == ["ada", "olga", "omar", "vera"]


def test_a_change_returns_the_user_as_it_is_now_and_is_stored(runtime):
    users = runtime.users
    changed = users.set_roles("vera", ["viewer", "operator", "viewer"])
    assert changed.roles == (Role.OPERATOR, Role.VIEWER)  # each once, in a fixed order
    assert users.get("vera") == changed
    assert users.set_disabled("vera", True).disabled is True
    assert users.get("vera").disabled and users.authenticate("vera", PASSWORD) is None
    assert users.set_disabled("vera", False).disabled is False
    assert users.authenticate("vera", PASSWORD).user_id == "vera"
    users.set_password("omar", "another-secret-1")
    assert users.authenticate("omar", PASSWORD) is None
    assert users.authenticate("omar", "another-secret-1").user_id == "omar"


def test_a_change_to_an_unknown_user_or_against_the_rules_changes_nothing(runtime):
    users = runtime.users
    for change in (lambda: users.set_disabled("ghost", True),
                   lambda: users.set_roles("ghost", ["viewer"]),
                   lambda: users.set_password("ghost", PASSWORD)):
        with pytest.raises(UserNotFoundError, match="no user 'ghost'"):
            change()
    assert users.get("ghost") is None  # an update never creates a user
    # the argparse choices keep these from the command line; the store holds the line itself
    for roles in ([], ["viewer", "superuser"]):
        with pytest.raises(ValueError, match="roles"):
            users.set_roles("olga", roles)
    with pytest.raises(ValueError, match="at least"):
        users.set_password("olga", "short")
    assert users.get("olga").roles == (Role.OPERATOR,)
    assert users.authenticate("olga", PASSWORD).user_id == "olga"


def test_stored_hash_is_not_the_password(runtime):
    with runtime.database.read() as conn:
        stored = conn.execute("SELECT password_hash FROM app_user").fetchall()
    assert all(PASSWORD not in row[0] for row in stored)
