"""Audit sink: durable records, filters, trace replay, and errors that are never swallowed."""

from datetime import timedelta

import pytest

from execution_world import OPERATOR, VIEWER
from twotrack.core import TraceStatus, TraceStep
from twotrack.core.audit import (
    AuditEntry,
    AuditError,
    AuditQuery,
    AuditRecorder,
    AuditStatus,
    SqliteAuditSink,
)
from twotrack.core.database import Database


def entry(world, **fields):
    base = dict(ts=world.clock(), user_id="olga", roles=["operator"], target="set_qty",
                effect="write", status=AuditStatus.OK, params={"sku": "F-1"},
                trace=[TraceStep(step="gate", status=TraceStatus.OK, facts={"stage": "x"})])
    return AuditEntry(**{**base, **fields})


def test_round_trip_and_trace_replay(world):
    entry_id = world.audit.record(entry(world, row_count=3, grant_id="abc", problem=None))
    stored = world.audit.get(entry_id)
    assert stored.id == entry_id and stored.row_count == 3 and stored.grant_id == "abc"
    assert stored.trace[0].facts == {"stage": "x"} and stored.params == {"sku": "F-1"}
    assert world.audit.get(entry_id + 100) is None


def test_list_filters_and_newest_first(world):
    world.audit.record(entry(world, user_id="vera", target="part_count"))
    world.clock.advance(minutes=5)
    world.audit.record(entry(world, status=AuditStatus.BLOCKED))
    world.audit.record(entry(world, route_source="llm"))
    page = world.audit.list(AuditQuery(user_id="olga"))
    assert page.total == 2 and page.items[0].route_source == "llm"
    assert world.audit.list(AuditQuery(status=AuditStatus.BLOCKED)).total == 1
    since = world.clock() - timedelta(minutes=1)
    assert world.audit.list(AuditQuery(since=since)).total == 2
    assert world.audit.list(limit=1, offset=1).items[0].status is AuditStatus.BLOCKED


def test_write_failure_raises_instead_of_failing_open():
    db = Database()
    sink = SqliteAuditSink(db)
    db.close()
    with pytest.raises(AuditError):
        sink.record(AuditEntry(ts="2026-10-01T00:00Z", user_id="u", target="t",
                               status=AuditStatus.OK))


def test_recorder_turns_any_sink_failure_into_audit_error(world):
    class Broken:
        def record(self, entry):
            raise OSError("disk gone")

    record = AuditRecorder(Broken(), world.clock, user_id="u", roles=[], target="t",
                           effect="read", route_source=None)
    with pytest.raises(AuditError, match="disk gone"):
        record(AuditStatus.OK)


def test_audit_and_data_commit_or_roll_back_together(world):
    with pytest.raises(RuntimeError):
        with world.store.transaction():
            world.store.upsert_many("part", [{"sku": "T-1"}])
            world.audit.record(entry(world))
            raise RuntimeError("late failure")
    assert world.store.get("part", "T-1") is None and world.audit.list().total == 0


def test_append_trace_extends_one_record_and_refuses_unknown_ids(world):
    entry_id = world.audit.record(entry(world))
    other = world.audit.record(entry(world))
    world.audit.append_trace(entry_id, [TraceStep(step="narrate", status=TraceStatus.OK)])
    assert [s.step for s in world.audit.get(entry_id).trace] == ["gate", "narrate"]
    assert [s.step for s in world.audit.get(other).trace] == ["gate"]
    with pytest.raises(AuditError, match="does not exist"):
        world.audit.append_trace(other + 99, [TraceStep(step="x", status=TraceStatus.OK)])


def test_an_older_log_gains_the_new_columns_in_place():
    db = Database()
    with db.transaction() as conn:
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
                     " user_id TEXT, roles TEXT, target TEXT, effect TEXT, status TEXT,"
                     " route_source TEXT, params_digest TEXT, params TEXT, row_count INTEGER,"
                     " grant_id TEXT, problem TEXT, trace TEXT)")
        conn.execute("INSERT INTO audit_log (ts, user_id, roles, target, status, params, trace)"
                     " VALUES ('2026-10-01T00:00:00.000000+00:00', 'u', '[]', 't', 'ok', '{}',"
                     " '[]')")
    sink = SqliteAuditSink(db)
    old = sink.get(1)
    assert old.target == "t" and old.request_id is None and old.parent_id is None
    sink.record(AuditEntry(ts="2026-10-01T00:01Z", user_id="u", status=AuditStatus.CHAT,
                           request_id="r1", parent_id=1))
    found = sink.list(AuditQuery(request_id="r1")).items
    assert [(e.target, e.status, e.parent_id) for e in found] == [(None, AuditStatus.CHAT, 1)]


def test_pipeline_records_every_outcome(world):
    world.pipeline.run(world.ctx(VIEWER), "set_qty", {"sku": "F-1", "qty": 1})
    world.pipeline.run(world.ctx(OPERATOR), "set_qty", {"sku": "F-1"})
    world.pipeline.run(world.ctx(OPERATOR), "set_qty", {"sku": "F-1", "qty": 1})
    world.pipeline.run(world.ctx(VIEWER), "part_count", {})
    assert world.audit_statuses() == [AuditStatus.BLOCKED, AuditStatus.INVALID,
                                      AuditStatus.NEEDS_CONFIRMATION, AuditStatus.OK]
    assert world.audit.list(AuditQuery(user_id="vera")).items[0].roles == ["viewer"]
