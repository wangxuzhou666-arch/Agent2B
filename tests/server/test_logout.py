"""Signing out revokes the token it is called with: the token is refused from then on, every
other token is untouched, and the deny-list does not grow without bound."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from tests.server.conftest import PASSWORD, Api
from twotrack.core.database import Database
from twotrack.runtime import build_runtime
from twotrack.server.app import create_app
from twotrack.server.auth import RevokedTokens, TokenError


def login(api, username="olga") -> str:
    response = api.client.post("/api/auth/login",
                               json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def me(api, token: str):
    return api.client.get("/api/auth/me", headers=bearer(token))


def logout(api, token: str):
    return api.client.post("/api/auth/logout", headers=bearer(token))


def denied(api) -> list[str]:
    with api.runtime.database.read() as conn:
        return [r[0] for r in conn.execute("SELECT token_id FROM revoked_token")]


def claims(api, token: str) -> dict:
    return jwt.decode(token, api.runtime.tokens._secret, algorithms=["HS256"])


# ---------------------------------------------------------------- the token is refused after


def test_after_logout_the_old_token_gets_401(api):
    token = login(api)
    assert me(api, token).status_code == 200
    done = logout(api, token)
    assert done.status_code == 200 and done.json() == {"status": "signed_out"}
    for response in (me(api, token),
                     api.client.get("/api/approvals", headers=bearer(token)),
                     api.client.get("/api/entities", headers=bearer(token)),
                     api.client.post("/api/query?stream=false", headers=bearer(token),
                                     json={"query": "how many items do we have"})):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        error = response.json()["error"]
        assert error["code"] == "token_revoked" and "signed out" in error["message"]


def test_other_tokens_are_unaffected(api):
    this_device, other_device = login(api), login(api)  # the same user, signed in twice
    someone_else = login(api, "vera")
    assert claims(api, this_device)["jti"] != claims(api, other_device)["jti"]
    assert logout(api, this_device).status_code == 200
    assert me(api, this_device).status_code == 401
    assert me(api, other_device).status_code == 200
    assert me(api, someone_else).json()["user_id"] == "vera"
    issued_directly, _ = api.runtime.tokens.issue("olga")
    assert me(api, issued_directly).status_code == 200


def test_signing_in_again_after_signing_out_works(api):
    first = login(api)
    logout(api, first)
    second = login(api)
    assert me(api, second).status_code == 200 and me(api, first).status_code == 401


def test_a_repeated_logout_is_harmless(api):
    token, other = login(api), login(api)
    assert logout(api, token).status_code == 200
    for _ in range(3):  # the token is already dead: refused, no error, nothing changes
        again = logout(api, token)
        assert again.status_code == 401 and again.json()["error"]["code"] == "token_revoked"
    assert denied(api) == [claims(api, token)["jti"]]
    assert me(api, other).status_code == 200


def test_two_logouts_at_once_never_fail_with_a_server_error(api):
    token = login(api)
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: logout(api, token), range(2)))
    assert {r.status_code for r in replies} <= {200, 401}  # whichever got there first wins
    assert 200 in {r.status_code for r in replies}
    assert denied(api) == [claims(api, token)["jti"]]


def test_a_demo_token_can_sign_out_too(make_runtime):
    demo = Api(make_runtime(demo_mode=True))
    token = demo.client.post("/api/auth/demo", json={"role": "operator"}).json()["access_token"]
    assert me(demo, token).status_code == 200
    assert logout(demo, token).status_code == 200
    assert me(demo, token).json()["error"]["code"] == "token_revoked"


def test_logout_needs_a_token_that_is_valid(api):
    assert api.client.post("/api/auth/logout").status_code == 401
    assert api.client.post("/api/auth/logout").json()["error"]["code"] == "not_authenticated"
    forged = jwt.encode({"sub": "ada", "iat": int(time.time()), "exp": int(time.time()) + 600,
                         "jti": "1" * 32}, "x" * 48, algorithm="HS256")
    assert logout(api, forged).json()["error"]["code"] == "invalid_token"
    expired, _ = api.runtime.tokens.issue("olga", ttl_seconds=-5)
    assert logout(api, expired).json()["error"]["code"] == "token_expired"
    assert denied(api) == []  # none of those left anything on the list


def test_the_revocation_survives_a_restart(api):
    token = login(api)
    logout(api, token)
    again = build_runtime(api.runtime.settings, registry=api.runtime.registry)
    try:
        with pytest.raises(TokenError) as caught:
            again.tokens.verify(token)
        assert caught.value.code == "token_revoked"
        assert me(Api(again), token).status_code == 401
        fresh, _ = again.tokens.issue("olga")
        assert again.tokens.verify(fresh).user_id == "olga"
    finally:
        again.close()


# ---------------------------------------------------------------- every token has an id


def test_every_token_carries_a_random_id(api):
    ids = {claims(api, login(api))["jti"] for _ in range(3)}
    demo, _ = api.runtime.tokens.issue("demo-viewer", demo=True)
    ids.add(claims(api, demo)["jti"])
    assert len(ids) == 4 and all(len(i) == 32 and int(i, 16) >= 0 for i in ids)
    token = login(api)
    assert api.runtime.tokens.verify(token).token_id == claims(api, token)["jti"]


def test_a_token_without_an_id_is_refused(api):
    """It could never be signed out, so it is not accepted at all (a token issued before ids
    existed signs in again once)."""
    payload = {"sub": "olga", "iat": int(time.time()), "exp": int(time.time()) + 600}
    token = jwt.encode(payload, api.runtime.tokens._secret, algorithm="HS256")
    response = me(api, token)
    assert response.status_code == 401 and response.json()["error"]["code"] == "invalid_token"


# ---------------------------------------------------------------- the list does not grow


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def count(db: Database) -> int:
    with db.read() as conn:
        return conn.execute("SELECT COUNT(*) FROM revoked_token").fetchone()[0]


T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def test_entries_of_expired_tokens_are_pruned():
    db, clock = Database(), Clock(T0)
    revoked = RevokedTokens(db, clock)
    revoked.revoke("short", T0 + timedelta(minutes=10))
    revoked.revoke("long", T0 + timedelta(hours=1))
    assert (revoked.is_revoked("short"), revoked.is_revoked("long")) == (True, True)
    assert revoked.prune() == 0 and count(db) == 2  # nothing has expired yet
    clock.now = T0 + timedelta(minutes=20)
    assert revoked.prune() == 1  # the short one expired by itself: its entry has no job left
    assert (revoked.is_revoked("short"), revoked.is_revoked("long")) == (False, True)
    clock.now = T0 + timedelta(hours=1)  # an entry goes at the very moment its token does
    assert revoked.prune() == 1 and count(db) == 0
    assert revoked.prune() == 0


def test_revoking_prunes_and_starting_prunes_and_a_repeat_adds_nothing():
    db, clock = Database(), Clock(T0)
    revoked = RevokedTokens(db, clock)
    for name in ("a", "b", "c"):
        revoked.revoke(name, T0 + timedelta(minutes=5))
    revoked.revoke("a", T0 + timedelta(minutes=5))  # signing out twice
    assert count(db) == 3
    clock.now = T0 + timedelta(hours=2)
    revoked.revoke("d", T0 + timedelta(hours=9))  # the write clears the dead entries first
    assert count(db) == 1 and revoked.is_revoked("d") and not revoked.is_revoked("a")
    revoked.revoke("e", T0 + timedelta(hours=1))  # already expired when it arrives
    clock.now = T0 + timedelta(hours=10)
    restarted = RevokedTokens(db, clock)  # a fresh service clears what expired while it was off
    assert count(restarted.db) == 0


def test_logging_out_through_the_api_prunes_the_tokens_that_expired(api):
    first, second = login(api), login(api)
    logout(api, first)
    assert len(denied(api)) == 1
    # eight and a half hours on: the first token has expired by itself
    api.runtime.tokens.revoked.clock = lambda: datetime.now(UTC) + timedelta(hours=8, minutes=30)
    logout(api, second)  # still valid: the real clock decides the token, the list's clock prunes
    assert denied(api) == [claims(api, second)["jti"]]


def test_the_deny_list_lives_in_the_shared_database(api):
    assert api.runtime.tokens.revoked.db is api.runtime.database
    schema = create_app(None).openapi()
    operation = schema["paths"]["/api/auth/logout"]["post"]
    assert operation["tags"] == ["auth"] and operation["security"]
    assert "401" in operation["responses"]
    status = schema["components"]["schemas"]["LogoutOut"]["properties"]["status"]
    assert status["const"] == "signed_out"
