"""A confirmation says how many seconds it has left, counted on the server's clock.

The browser counts down from the moment it receives the number, so a browser whose clock is
minutes off (which would make ``expires_at`` look past, or far away) still shows a confirmation
that is valid and hides one that is not.
"""

from datetime import datetime, timedelta

import pytest

from tests.fakes import NOW
from tests.server.conftest import Api
from twotrack.server.app import create_app
from twotrack.server.approval_views import seconds_left

WRITE = {"query": "set it", "explicit_target": "set_item_qty",
         "explicit_params": {"sku": "A-1", "qty": 12}}
CSV = b"Item Code,Name,Qty On Hand\nN-1,Nut,10\nN-2,Cap,3\n"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def timed(make_runtime, clock):
    return Api(make_runtime(clock=clock))


def parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def test_the_time_left_counts_down_on_the_servers_clock(timed, clock):
    # the clock is fixed at a date long before the machine's real time: nothing below can
    # depend on what time a client, or this process, believes it is
    ref = timed.post("/api/query?stream=false", "olga", json=WRITE).json()["approval"]
    assert ref["expires_in_s"] == 900.0
    assert parse(ref["expires_at"]) - NOW == timedelta(minutes=15)

    def listed():
        return timed.get("/api/approvals", "olga").json()["items"]

    assert [i["expires_in_s"] for i in listed()] == [900.0]
    clock.now = NOW + timedelta(minutes=10)
    assert [i["expires_in_s"] for i in listed()] == [300.0]
    clock.now = NOW + timedelta(seconds=899, milliseconds=500)
    assert [i["expires_in_s"] for i in listed()] == [0.5]
    clock.now = NOW + timedelta(minutes=15)  # gone: the list shows nothing that cannot run
    assert listed() == []


def test_the_number_is_the_difference_between_the_two_stamps(timed, clock):
    timed.post("/api/query?stream=false", "olga", json=WRITE)
    clock.now = NOW + timedelta(minutes=3, seconds=7)
    (item,) = timed.get("/api/approvals", "olga").json()["items"]
    assert item["expires_in_s"] == (parse(item["expires_at"]) - clock.now).total_seconds() == 713


def test_an_import_confirmation_is_timed_too(timed, clock):
    preview = timed.client.post(
        "/api/entities/item/import/preview", headers=timed.headers("olga"),
        files={"file": ("items.csv", CSV, "text/csv")}).json()
    assert preview["grant"]["expires_in_s"] == 900.0
    clock.now = NOW + timedelta(minutes=1)
    waiting = timed.post("/api/entities/item/import/commit", "olga",
                         json={"digest": preview["digest"]}).json()
    assert waiting["approval"]["expires_in_s"] == 840.0
    (item,) = timed.get("/api/approvals", "olga").json()["items"]
    assert (item["kind"], item["expires_in_s"]) == ("import", 840.0)


def test_the_time_left_is_never_below_zero_and_is_rounded_to_the_millisecond():
    expires = NOW + timedelta(minutes=15)
    assert seconds_left(expires, NOW) == 900.0
    assert seconds_left(expires, expires) == 0.0
    assert seconds_left(expires, expires + timedelta(hours=2)) == 0.0  # long past: clamped
    assert seconds_left(expires, expires - timedelta(microseconds=1600)) == 0.002
    assert seconds_left(expires, expires + timedelta(microseconds=1)) == 0.0


def test_both_shapes_declare_the_number_as_a_required_float():
    schemas = create_app(None).openapi()["components"]["schemas"]
    for name in ("ApprovalRef", "ApprovalItem"):
        assert schemas[name]["properties"]["expires_in_s"]["type"] == "number", name
        assert "expires_in_s" in schemas[name]["required"], name
        assert "expires_at" in schemas[name]["required"], name  # the stamp stays
