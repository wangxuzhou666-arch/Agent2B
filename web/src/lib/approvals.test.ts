// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { deferred } from "../../test/deferred";
import { ApiError, toFailure, type ApprovalOutcome, type AuditEntryOut } from "./api";
import {
  approvalReducer,
  AUDIT_UNREADABLE,
  canAct,
  createApprovalStore,
  deadlineIn,
  isResolved,
  listedDeadline,
  PENDING,
  verdictFromAudit,
  windowOf,
  type ApprovalClient,
  type ApprovalState,
} from "./approvals";

const OUTCOME: ApprovalOutcome = {
  grant_id: "g1",
  kind: "query",
  target: "create_po",
  status: "ok",
  problem: null,
  answer: "Draft PO-1 created.",
  answer_source: "template",
  narrative_stripped: 0,
  columns: [],
  rows: [],
  aggregates: {},
  provenance: null,
  audit_ids: [7],
  import_result: null,
  trace: [],
};

const httpError = (status: number, code: string) => new ApiError({ status, code, message: code, kind: "http" });
const lost = () => new ApiError({ status: 0, code: "network_error", message: "The server could not be reached.", kind: "network" });

const record = (over: Partial<AuditEntryOut> & Pick<AuditEntryOut, "id" | "status">): AuditEntryOut => ({
  ts: "2026-10-26T08:00:00Z",
  user_id: "demo-operator",
  roles: ["operator"],
  target: "create_po",
  effect: "write",
  route_source: "rule",
  params_digest: "d1",
  params: {},
  row_count: null,
  grant_id: "g1",
  problem: null,
  request_id: "r1",
  parent_id: null,
  duration_ms: null,
  ...over,
});

function client(over: Partial<ApprovalClient> = {}) {
  return {
    approve: vi.fn<ApprovalClient["approve"]>().mockResolvedValue(OUTCOME),
    reject: vi.fn<ApprovalClient["reject"]>().mockResolvedValue({ grant_id: "g1", status: "rejected" }),
    records: vi.fn<ApprovalClient["records"]>().mockResolvedValue([]),
    ...over,
  };
}

const NOW = Date.parse("2026-10-26T08:00:00Z");
const store = (calls: ApprovalClient, onSettled = vi.fn()) => createApprovalStore({ client: calls, onSettled, now: () => NOW, clock: () => 0 });

describe("approvalReducer", () => {
  const approving: ApprovalState = { phase: "approving", attempt: 1, inDoubt: false };

  it("starts from pending, and again only after a request lost in transit", () => {
    expect(approvalReducer(PENDING, { type: "start", action: "approve", attempt: 1 })).toEqual(approving);
    const lostInTransit = approvalReducer(approving, { type: "failed", attempt: 1, error: toFailure(lost()), at: NOW });
    expect(lostInTransit).toMatchObject({ phase: "failed", inDoubt: true, error: { retryable: true } });
    expect(approvalReducer(lostInTransit, { type: "start", action: "approve", attempt: 2 })).toEqual({
      phase: "approving",
      attempt: 2,
      inDoubt: true,
    });

    const refused = approvalReducer(approving, { type: "failed", attempt: 1, error: toFailure(httpError(403, "not_requester")), at: NOW });
    expect(refused).toMatchObject({ phase: "failed", inDoubt: false, error: { retryable: false } });
    expect(approvalReducer(refused, { type: "start", action: "approve", attempt: 2 })).toBe(refused);
  });

  it("ignores re-entry while a request is out, and replies to an older attempt", () => {
    expect(approvalReducer(approving, { type: "start", action: "approve", attempt: 2 })).toBe(approving);
    expect(approvalReducer(approving, { type: "start", action: "reject", attempt: 2 })).toBe(approving);
    expect(approvalReducer(approving, { type: "approved", attempt: 0, outcome: OUTCOME, at: NOW, elapsedMs: 5 })).toBe(approving);
  });

  it("never downgrades a resolved confirmation, whatever arrives later", () => {
    const approved = approvalReducer(approving, { type: "approved", attempt: 1, outcome: OUTCOME, at: NOW, elapsedMs: 12 });
    expect(approved).toMatchObject({ phase: "approved", source: "reply", by: null, elapsedMs: 12 });
    const later = [
      { type: "failed", attempt: 1, error: toFailure(httpError(409, "grant_used")), at: NOW + 1 },
      { type: "failed", attempt: 1, error: toFailure(httpError(410, "approval_expired")), at: NOW + 1 },
      { type: "start", action: "reject", attempt: 2 },
      { type: "departed", overdue: true, at: NOW + 1 },
      { type: "verdict", attempt: 1, verdict: { kind: "unrecorded" }, at: NOW + 1 },
    ] as const;
    for (const event of later) expect(approvalReducer(approved, event)).toBe(approved);

    for (const resolved of [
      { phase: "rejected", at: NOW, by: null, source: "reply" },
      { phase: "expired", at: NOW, source: "reply" },
      { phase: "elsewhere", at: NOW },
      { phase: "unconfirmed", started: false, at: NOW },
    ] satisfies ApprovalState[]) {
      expect(isResolved(resolved)).toBe(true);
      expect(canAct(resolved)).toBe(false);
      for (const event of later) expect(approvalReducer(resolved, event)).toBe(resolved);
    }
  });

  it("lets the server's 410 expire an approval, never the local clock", () => {
    expect(approvalReducer(approving, { type: "failed", attempt: 1, error: toFailure(httpError(410, "approval_expired")), at: NOW })).toEqual({
      phase: "expired",
      at: NOW,
      source: "reply",
    });
  });

  it("reads a 404 or 409 as the server's verdict, unless an earlier reply was lost", () => {
    for (const status of [404, 409]) {
      const plain = approvalReducer(approving, { type: "failed", attempt: 1, error: toFailure(httpError(status, "x")), at: NOW });
      expect(plain).toMatchObject({ phase: "failed", inDoubt: false, error: { status, retryable: false } });

      const retry: ApprovalState = { phase: "approving", attempt: 2, inDoubt: true };
      expect(approvalReducer(retry, { type: "failed", attempt: 2, error: toFailure(httpError(status, "x")), at: NOW })).toEqual({
        phase: "checking",
        attempt: 2,
        action: "approve",
      });
    }
  });

  it("takes the audit log's verdict, and stays in doubt when the log can't be read", () => {
    const checking: ApprovalState = { phase: "checking", attempt: 2, action: "approve" };
    expect(
      approvalReducer(checking, { type: "verdict", attempt: 2, verdict: { kind: "approved", outcome: OUTCOME, by: "demo-operator", at: NOW }, at: NOW + 5 }),
    ).toEqual({ phase: "approved", outcome: OUTCOME, at: NOW, by: "demo-operator", elapsedMs: null, source: "audit" });
    expect(approvalReducer(checking, { type: "verdict", attempt: 1, verdict: { kind: "started" }, at: NOW })).toBe(checking);
    expect(approvalReducer(checking, { type: "verdict", attempt: 2, verdict: { kind: "started" }, at: NOW })).toEqual({
      phase: "unconfirmed",
      started: true,
      at: NOW,
    });
    expect(approvalReducer(checking, { type: "failed", attempt: 2, error: toFailure(lost()), at: NOW })).toMatchObject({
      phase: "failed",
      action: "approve",
      inDoubt: true,
      error: { retryable: true },
    });
  });

  it("marks an untouched confirmation that left the server's list as expired or resolved elsewhere", () => {
    expect(approvalReducer(PENDING, { type: "departed", overdue: true, at: NOW })).toEqual({ phase: "expired", at: NOW, source: "list" });
    expect(approvalReducer(PENDING, { type: "departed", overdue: false, at: NOW })).toEqual({ phase: "elsewhere", at: NOW });
    // a request of ours may be why it left: that request's reply decides
    expect(approvalReducer(approving, { type: "departed", overdue: false, at: NOW })).toBe(approving);
  });
});

describe("verdictFromAudit", () => {
  it("finds the newest outcome, skipping the request and other users' refused attempts", () => {
    const entries = [
      record({ id: 3, status: "blocked", problem: "not_requester", user_id: "intruder" }),
      record({ id: 5, status: "ok", row_count: 1, ts: "2026-10-26T08:01:00Z" }),
      record({ id: 4, status: "started" }),
      record({ id: 2, status: "needs_confirmation" }),
    ];
    expect(verdictFromAudit("g1", entries)).toEqual({
      kind: "approved",
      by: "demo-operator",
      at: Date.parse("2026-10-26T08:01:00Z"),
      outcome: expect.objectContaining({ status: "ok", audit_ids: [5], kind: "query", target: "create_po" }),
    });
  });

  it("rebuilds an import's result from its record", () => {
    const verdict = verdictFromAudit("g1", [
      record({ id: 9, status: "ok", target: "import:packaging", row_count: 20, params: { errors: 2, rows: 22 }, params_digest: "abc" }),
    ]);
    expect(verdict).toMatchObject({
      kind: "approved",
      outcome: { kind: "import", import_result: { entity: "packaging", written: 20, skipped: 2, digest: "abc", audit_id: 9 } },
    });
  });

  it("keeps a success that a later grant_used refusal follows", () => {
    const verdict = verdictFromAudit("g1", [
      record({ id: 9, status: "blocked", problem: "grant_used", route_source: null }),
      record({ id: 7, status: "ok", row_count: 1, ts: "2026-10-26T08:01:00Z" }),
      record({ id: 6, status: "started" }),
      record({ id: 5, status: "needs_confirmation" }),
    ]);
    expect(verdict).toEqual({
      kind: "approved",
      by: "demo-operator",
      at: Date.parse("2026-10-26T08:01:00Z"),
      outcome: expect.objectContaining({ status: "ok", problem: null, audit_ids: [7] }),
    });
  });

  it("reads an approval_expired refusal as an expiry", () => {
    const at = "2026-10-26T08:15:00Z";
    const verdict = verdictFromAudit("g1", [
      record({ id: 6, status: "blocked", problem: "approval_expired", route_source: null, ts: at }),
      record({ id: 5, status: "needs_confirmation" }),
    ]);
    expect(verdict).toEqual({ kind: "expired", at: Date.parse(at) });
    const checking: ApprovalState = { phase: "checking", attempt: 2, action: "approve" };
    expect(approvalReducer(checking, { type: "verdict", attempt: 2, verdict, at: NOW })).toEqual({
      phase: "expired",
      at: Date.parse(at),
      source: "audit",
    });
  });

  it("finds no outcome among refusals alone", () => {
    expect(
      verdictFromAudit("g1", [
        record({ id: 8, status: "blocked", problem: "grant_used", route_source: null }),
        record({ id: 7, status: "blocked", problem: "not_submitted", route_source: null }),
        record({ id: 6, status: "blocked", problem: "not_requester", route_source: null, user_id: "intruder" }),
        record({ id: 5, status: "needs_confirmation" }),
      ]),
    ).toEqual({ kind: "unrecorded" });
  });

  it("tells a rejection, a write without a result yet and no record at all apart", () => {
    expect(verdictFromAudit("g1", [record({ id: 4, status: "blocked", problem: "rejected_for:demo-viewer" })])).toMatchObject({
      kind: "rejected",
    });
    expect(verdictFromAudit("g1", [record({ id: 4, status: "started" }), record({ id: 2, status: "needs_confirmation" })])).toEqual({
      kind: "started",
    });
    expect(verdictFromAudit("g1", [record({ id: 2, status: "needs_confirmation" }), record({ id: 8, status: "ok", grant_id: "g2" })])).toEqual({
      kind: "unrecorded",
    });
    expect(verdictFromAudit("g1", [record({ id: 6, status: "error", problem: "OperationalError" })])).toMatchObject({
      kind: "approved",
      outcome: { status: "error", problem: "OperationalError" },
    });
  });
});

describe("deadlines", () => {
  const arrival = { mono: 1_000 };

  it("counts the time left as the server counts it, from arrival on the monotonic clock", () => {
    expect(deadlineIn(42_000, arrival, 900_000)).toEqual({ endsAt: 43_000, windowMs: 900_000 });
    // no window known: the bar runs over the time that was left at arrival
    expect(deadlineIn(60_000, arrival)).toEqual({ endsAt: 61_000, windowMs: 60_000 });
    expect(deadlineIn(-5_000, arrival, 900_000)).toEqual({ endsAt: 1_000, windowMs: 900_000 });
    expect(deadlineIn(Number.NaN, arrival)).toEqual({ endsAt: 1_000, windowMs: null });
  });

  it("gives a listed confirmation the server's time left, never past its own window, whatever the wall clock says", () => {
    const listed = { created_at: "2026-10-26T08:00:00Z", expires_at: "2026-10-26T08:15:00Z" };
    // `expires_at` is long past on any real clock: the server's 90 s are what count
    expect(listedDeadline({ ...listed, expires_in_s: 90 }, arrival)).toEqual({ endsAt: 91_000, windowMs: 900_000 });
    expect(listedDeadline({ ...listed, expires_in_s: 0 }, arrival)).toEqual({ endsAt: 1_000, windowMs: 900_000 });
    expect(listedDeadline({ ...listed, expires_in_s: 3_600 }, arrival)).toEqual({ endsAt: 901_000, windowMs: 900_000 });
    // a malformed window: the time left alone
    expect(listedDeadline({ created_at: "?", expires_at: "?", expires_in_s: 30 }, arrival)).toEqual({ endsAt: 31_000, windowMs: 30_000 });
  });

  it("reads a listed confirmation's window from the server's own timestamps", () => {
    expect(windowOf({ created_at: "2026-10-26T08:00:00Z", expires_at: "2026-10-26T08:15:00Z" })).toBe(900_000);
    expect(windowOf({ created_at: "2026-10-26T08:15:00Z", expires_at: "2026-10-26T08:00:00Z" })).toBeNull();
  });
});

describe("createApprovalStore", () => {
  it("sends one request for a double click, and none once resolved", async () => {
    const reply = deferred<ApprovalOutcome>();
    const calls = client({ approve: vi.fn<ApprovalClient["approve"]>().mockReturnValue(reply.promise) });
    const onSettled = vi.fn();
    const approvals = store(calls, onSettled);

    const first = approvals.approve("g1");
    expect(first).not.toBeNull();
    expect(approvals.approve("g1")).toBeNull();
    expect(approvals.reject("g1")).toBeNull();
    expect(calls.approve).toHaveBeenCalledTimes(1);
    expect(approvals.get("g1")).toMatchObject({ phase: "approving" });

    reply.resolve(OUTCOME);
    await expect(first).resolves.toMatchObject({ phase: "approved", outcome: OUTCOME });
    expect(onSettled).toHaveBeenCalledWith("g1", expect.objectContaining({ phase: "approved" }));
    expect(approvals.approve("g1")).toBeNull();
    expect(calls.approve).toHaveBeenCalledTimes(1);
    expect(approvals.get("g2")).toBe(PENDING); // one grant's state is its own
  });

  it("expires an approval on the server's 410", async () => {
    const calls = client({ approve: vi.fn<ApprovalClient["approve"]>().mockRejectedValue(httpError(410, "approval_expired")) });
    const approvals = store(calls);
    await expect(approvals.approve("g1")).resolves.toEqual({ phase: "expired", at: NOW, source: "reply" });
    expect(calls.records).not.toHaveBeenCalled();
  });

  it("asks the audit log when a retry after a lost reply finds the confirmation used (409)", async () => {
    const calls = client({
      approve: vi.fn<ApprovalClient["approve"]>().mockRejectedValueOnce(lost()).mockRejectedValueOnce(httpError(409, "grant_used")),
      records: vi.fn<ApprovalClient["records"]>().mockResolvedValue([
        // the retry's own refusal, newer than the outcome the lost reply carried
        record({ id: 8, status: "blocked", problem: "grant_used", route_source: null }),
        record({ id: 7, status: "ok", row_count: 1 }),
        record({ id: 6, status: "started" }),
        record({ id: 5, status: "needs_confirmation" }),
      ]),
    });
    const approvals = store(calls);

    await expect(approvals.approve("g1")).resolves.toMatchObject({ phase: "failed", inDoubt: true, error: { retryable: true } });
    expect(calls.records).not.toHaveBeenCalled();

    await expect(approvals.approve("g1")).resolves.toMatchObject({
      phase: "approved",
      source: "audit",
      by: "demo-operator",
      outcome: { status: "ok", audit_ids: [7] },
    });
    expect(calls.records).toHaveBeenCalledWith("g1");
    expect(calls.approve).toHaveBeenCalledTimes(2);
  });

  it("takes a 409 without a lost reply before it as the server's verdict", async () => {
    const calls = client({ approve: vi.fn<ApprovalClient["approve"]>().mockRejectedValue(httpError(409, "digest_mismatch")) });
    const approvals = store(calls);
    await expect(approvals.approve("g1")).resolves.toMatchObject({ phase: "failed", inDoubt: false, error: { status: 409, code: "digest_mismatch" } });
    expect(calls.records).not.toHaveBeenCalled();
  });

  it("finds an expiry in the audit log when a retry after a lost reply finds the confirmation gone", async () => {
    const calls = client({
      approve: vi.fn<ApprovalClient["approve"]>().mockRejectedValueOnce(lost()).mockRejectedValueOnce(httpError(404, "not_found")),
      records: vi.fn<ApprovalClient["records"]>().mockResolvedValue([
        record({ id: 6, status: "blocked", problem: "approval_expired", route_source: null, ts: "2026-10-26T08:15:00Z" }),
        record({ id: 5, status: "needs_confirmation" }),
      ]),
    });
    const approvals = store(calls);
    await approvals.approve("g1");
    await expect(approvals.approve("g1")).resolves.toEqual({ phase: "expired", at: Date.parse("2026-10-26T08:15:00Z"), source: "audit" });
  });

  it("finds a lost rejection in the audit log, and says when the log can't be read", async () => {
    const calls = client({
      reject: vi.fn<ApprovalClient["reject"]>().mockRejectedValueOnce(lost()).mockRejectedValue(httpError(404, "not_found")),
      records: vi.fn<ApprovalClient["records"]>().mockResolvedValue([record({ id: 9, status: "blocked", problem: "rejected" })]),
    });
    const approvals = store(calls);
    await approvals.reject("g1");
    await expect(approvals.reject("g1")).resolves.toMatchObject({ phase: "rejected", source: "audit" });

    const unreadable = store(
      client({
        approve: vi.fn<ApprovalClient["approve"]>().mockRejectedValueOnce(lost()).mockRejectedValue(httpError(404, "not_found")),
        records: vi.fn<ApprovalClient["records"]>().mockRejectedValue(httpError(403, "forbidden")),
      }),
    );
    await unreadable.approve("g1");
    await expect(unreadable.approve("g1")).resolves.toMatchObject({
      phase: "failed",
      inDoubt: true,
      error: { code: AUDIT_UNREADABLE, status: 403, retryable: false },
    });
  });

  it("tells subscribers about every change, and lets a departure resolve only an untouched one", () => {
    const approvals = store(client());
    const listener = vi.fn();
    const unsubscribe = approvals.subscribe(listener);
    approvals.depart("g1", false);
    expect(approvals.get("g1")).toEqual({ phase: "elsewhere", at: NOW });
    expect(listener).toHaveBeenCalledTimes(1);
    const snapshot = approvals.all();
    approvals.depart("g1", true); // already resolved: nothing changes, nobody is told
    expect(approvals.all()).toBe(snapshot);
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    approvals.depart("g2", true);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(approvals.get("g2")).toMatchObject({ phase: "expired", source: "list" });
  });
});
