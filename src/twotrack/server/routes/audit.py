"""Read access to the audit log (admin and operator)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from twotrack.core.audit import NO_ROUTE_SOURCE, AuditEntry, AuditQuery, AuditStatus
from twotrack.runtime import Runtime

from ..deps import Session, get_runtime, require_roles
from ..errors import errors, not_found
from ..schemas import (
    AuditDetailOut,
    AuditEntryOut,
    AuditPageOut,
    AuditStatsOut,
    StatBucket,
)

router = APIRouter(tags=["audit"])
STAFF = require_roles("operator")  # admin passes every role list
STATS_DEFAULT_WINDOW = timedelta(days=7)


def _utc(value: datetime | None) -> datetime | None:
    """A timestamp without a zone is read as UTC (the log stores UTC)."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def duration_ms(entry: AuditEntry) -> float | None:
    """How long the record's steps took, summed; None for a record without steps."""
    if not entry.trace:
        return None
    return round(sum(step.duration_ms for step in entry.trace), 3)


def _summary(entry: AuditEntry) -> dict:
    return {**entry.model_dump(exclude={"trace"}), "duration_ms": duration_ms(entry)}


@router.get("/audit", response_model=AuditPageOut, responses=errors(401, 403, 422))
def list_audit(
    user: str | None = Query(default=None, description="Acting user id"),
    target: str | None = None,
    status: AuditStatus | None = None,
    route_source: str | None = Query(
        default=None, description=f"'{NO_ROUTE_SOURCE}': the records written without a route "
                                  "source, the stats bucket of the same name"),
    since: datetime | None = None,
    until: datetime | None = None,
    request_id: str | None = Query(default=None, max_length=64,
                                   description="Every record of one request, e.g. a write's "
                                               "confirmation and its approved execution"),
    grant_id: str | None = Query(default=None, max_length=64,
                                 description="Records of one confirmation"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: Session = Depends(STAFF),
    runtime: Runtime = Depends(get_runtime),
) -> AuditPageOut:
    """Newest first. Parameters are masked at write time; the trace is on the detail call."""
    found = runtime.audit.list(
        AuditQuery(user_id=user, target=target, status=status, route_source=route_source,
                   since=_utc(since), until=_utc(until), request_id=request_id,
                   grant_id=grant_id),
        limit=page_size, offset=(page - 1) * page_size)
    return AuditPageOut(items=[AuditEntryOut(**_summary(e)) for e in found.items],
                        total=found.total, page=page, page_size=page_size)


@router.get("/audit/stats", response_model=AuditStatsOut, responses=errors(401, 403, 422))
def audit_stats(
    since: datetime | None = Query(default=None, description="Default: the last 7 days"),
    until: datetime | None = None,
    _: Session = Depends(STAFF),
    runtime: Runtime = Depends(get_runtime),
) -> AuditStatsOut:
    """Routing decisions and pipeline runs per route source and outcome, including queries
    answered without a run (``clarify``, ``chat``). Feeds the rule-versus-model share chart."""
    start = _utc(since) or runtime.clock() - STATS_DEFAULT_WINDOW
    end = _utc(until)
    clauses, args = ["status != ?", "ts >= ?"], [AuditStatus.STARTED.value, _stamp(start)]
    if end is not None:
        clauses.append("ts < ?")
        args.append(_stamp(end))
    with runtime.database.read() as conn:
        rows = conn.execute(
            "SELECT route_source, status, COUNT(*) AS n FROM audit_log WHERE "
            + " AND ".join(clauses) + " GROUP BY route_source, status "
            "ORDER BY route_source, status", args).fetchall()
    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    buckets: dict[tuple[str, str], int] = {}
    for row in rows:
        key = row["route_source"] or NO_ROUTE_SOURCE  # the value the list filter takes
        by_source[key] = by_source.get(key, 0) + row["n"]
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]
        buckets[key, row["status"]] = buckets.get((key, row["status"]), 0) + row["n"]
    return AuditStatsOut(
        since=start, until=end, total=sum(by_source.values()), by_route_source=by_source,
        by_status=by_status,
        buckets=[StatBucket(route_source=source, status=AuditStatus(status), count=n)
                 for (source, status), n in sorted(buckets.items())])


def _stamp(value: datetime) -> str:
    # same text form the audit sink stores, so string comparison orders by time
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@router.get("/audit/{entry_id}", response_model=AuditDetailOut,
            responses=errors(401, 403, 404, 422))
def get_audit(entry_id: int, _: Session = Depends(STAFF),
              runtime: Runtime = Depends(get_runtime)) -> AuditDetailOut:
    """One entry with its full trace, enough to replay how the request was handled."""
    entry = runtime.audit.get(entry_id)
    if entry is None:
        raise not_found("This audit entry")
    return AuditDetailOut(**entry.model_dump(), duration_ms=duration_ms(entry))
