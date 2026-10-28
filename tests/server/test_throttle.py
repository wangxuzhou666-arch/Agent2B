"""The sign-in throttle: 5 failed attempts per 5 minutes for one (address, user name), counted
before the password is hashed, and a table that inventing user names cannot use to unlock a key."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from tests.server.conftest import PASSWORD
from twotrack.server.app import create_app
from twotrack.server.auth import LoginThrottle


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def login(client, password="wrong-wrong-1", username="olga"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------- the limit


def test_five_attempts_are_booked_and_the_sixth_waits_for_the_window():
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    assert [throttle.reserve("k") for _ in range(5)] == [0.0] * 5
    assert throttle.reserve("k") == 300.0
    clock.now += 299
    assert throttle.reserve("k") == 1.0
    clock.now += 1  # the first attempt leaves the window and takes the lock with it
    assert throttle.reserve("k") == 0.0


def test_the_window_slides_per_attempt():
    clock = Clock()
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=clock)
    assert throttle.reserve("k") == 0.0
    clock.now += 30
    assert throttle.reserve("k") == 0.0
    assert throttle.reserve("k") == 30.0  # until the first of the two leaves
    clock.now += 31
    assert throttle.reserve("k") == 0.0  # one is booked again, the second still counts
    assert throttle.reserve("k") > 0


def test_a_refused_attempt_books_nothing_and_does_not_extend_the_lock():
    clock = Clock()
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=clock)
    throttle.reserve("k"), throttle.reserve("k")
    clock.now += 40
    assert [throttle.reserve("k") for _ in range(10)] == [20.0] * 10
    clock.now += 20
    assert throttle.reserve("k") == 0.0


def test_a_right_password_clears_the_key_and_other_keys_are_independent():
    throttle = LoginThrottle(max_failures=3)
    for _ in range(2):
        throttle.reserve("a")
    for _ in range(3):
        throttle.reserve("b")
    throttle.clear("a")
    assert [throttle.reserve("a") for _ in range(3)] == [0.0] * 3  # a fresh budget
    assert throttle.reserve("b") > 0
    throttle.clear("nobody")  # a key it never saw is not an error


# ---------------------------------------------------------------- parallel attempts


class SlowCount(LoginThrottle):
    """Gives the other threads time to run between counting a key's attempts and booking one:
    the gap that a check without the lock leaves open."""

    def _live(self, key, now):
        live = super()._live(key, now)
        time.sleep(0.001)
        return live


def test_parallel_attempts_never_get_past_the_limit():
    throttle = SlowCount(clock=lambda: 1000.0)
    start = threading.Barrier(40)

    def attempt(_: int) -> float:
        start.wait()
        return throttle.reserve("k")

    with ThreadPoolExecutor(40) as pool:
        waits = list(pool.map(attempt, range(40)))
    assert sum(w == 0.0 for w in waits) == 5
    assert all(w > 0 for w in waits if w != 0.0)


def test_a_burst_of_wrong_passwords_is_hashed_five_times_and_not_forty(api, monkeypatch):
    hashed = []
    real = api.runtime.users.authenticate

    def counting(user_id, password):
        hashed.append(user_id)
        time.sleep(0.1)  # a slow hash keeps every request of the burst inside the old gap
        return real(user_id, password)

    monkeypatch.setattr(api.runtime.users, "authenticate", counting)
    start = threading.Barrier(40)

    def attempt(_: int):
        start.wait()
        return login(api.client)

    with ThreadPoolExecutor(40) as pool:
        replies = list(pool.map(attempt, range(40)))
    statuses = sorted(r.status_code for r in replies)
    assert statuses == [401] * 5 + [429] * 35
    assert len(hashed) == 5
    assert all(int(r.headers["Retry-After"]) >= 1 for r in replies if r.status_code == 429)
    assert login(api.client, PASSWORD).status_code == 429  # the right password waits too


def test_a_sign_in_that_dies_in_the_server_is_still_counted(runtime, monkeypatch):
    def crash(user_id, password):
        raise RuntimeError("database is down")

    monkeypatch.setattr(runtime.users, "authenticate", crash)
    client = TestClient(create_app(runtime), base_url="http://localhost",
                        raise_server_exceptions=False)
    assert [login(client).status_code for _ in range(6)] == [500] * 5 + [429]


def test_the_key_is_the_peer_address_and_the_user_name(runtime):
    app = create_app(runtime)  # one app, so one throttle, reached from two addresses
    here = TestClient(app, base_url="http://localhost", client=("198.51.100.7", 50000))
    elsewhere = TestClient(app, base_url="http://localhost", client=("198.51.100.8", 50000))
    assert [login(here).status_code for _ in range(5)] == [401] * 5
    assert login(here, PASSWORD).status_code == 429  # locked from this address ...
    assert login(here, PASSWORD, "vera").status_code == 200  # ... for this user name only
    assert login(elsewhere, PASSWORD).status_code == 200  # and not from another address


# ---------------------------------------------------------------- the table


def test_the_table_is_bounded():
    throttle = LoginThrottle(max_keys=3)
    for i in range(10):
        assert throttle.reserve(f"k{i}") == 0.0
    assert list(throttle._failures) == ["k7", "k8", "k9"]  # the most recent three


def test_a_known_key_is_not_refused_because_the_table_is_full():
    throttle = LoginThrottle(max_keys=2)
    throttle.reserve("a"), throttle.reserve("b")
    assert throttle.reserve("a") == 0.0
    assert set(throttle._failures) == {"a", "b"}


def test_ten_thousand_invented_user_names_do_not_unlock_a_locked_key():
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.reserve("203.0.113.9|victim")
    assert throttle.reserve("203.0.113.9|victim") > 0
    for i in range(10_000):
        throttle.reserve(f"203.0.113.9|invented{i}")
    assert len(throttle._failures) == throttle.max_keys
    assert throttle.reserve("203.0.113.9|victim") > 0


def test_a_locked_key_is_never_forgotten_even_when_it_is_the_oldest():
    throttle = LoginThrottle(max_keys=3, max_failures=2)
    throttle.reserve("victim"), throttle.reserve("victim")
    for i in range(50):
        throttle.reserve(f"other{i}")
        assert "victim" in throttle._failures
    assert throttle.reserve("victim") > 0


def test_an_expired_key_is_forgotten_before_a_live_one():
    clock = Clock(0.0)
    throttle = LoginThrottle(max_keys=3, window_seconds=300, clock=clock)
    throttle.reserve("old")
    clock.now = 250
    for _ in range(3):
        throttle.reserve("busy")
    throttle.reserve("fresh")
    clock.now = 310  # "old" has expired, the others have not
    throttle.reserve("new")
    assert list(throttle._failures) == ["busy", "fresh", "new"]
    assert len(throttle._failures["busy"]) == 3  # its count was not touched


def test_the_unlocked_key_with_the_fewest_failures_is_forgotten_first():
    throttle = LoginThrottle(max_keys=3)
    for _ in range(4):
        throttle.reserve("heavy")  # the least recently used, one failure short of a lock
    throttle.reserve("light1"), throttle.reserve("light2")
    throttle.reserve("new")
    assert list(throttle._failures) == ["heavy", "light2", "new"]  # light1: fewest, oldest
    assert throttle.reserve("heavy") == 0.0 and throttle.reserve("heavy") > 0  # still 5, not reset


def test_a_table_full_of_live_locks_refuses_a_new_key_until_a_lock_expires():
    clock = Clock(0.0)
    throttle = LoginThrottle(max_keys=2, max_failures=2, window_seconds=60, clock=clock)
    throttle.reserve("a"), throttle.reserve("a")
    clock.now = 10
    throttle.reserve("b"), throttle.reserve("b")
    assert throttle.reserve("c") == 50.0  # when "a" unlocks, the soonest of the two
    assert set(throttle._failures) == {"a", "b"}  # nothing was forgotten to make room
    assert throttle.reserve("a") > 0 and throttle.reserve("b") > 0
    clock.now = 60
    assert throttle.reserve("c") == 0.0  # "a" is unlocked now, and gives its slot
    assert set(throttle._failures) == {"b", "c"}
