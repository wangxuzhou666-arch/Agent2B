// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { auditEntry } from "../../test/fixtures";
import { ApiError } from "./api";
import {
  auditQueryFor,
  chainQueries,
  formatShare,
  isMasked,
  loadRequestChain,
  mergeChain,
  readAuditFilters,
  shareRows,
  shortId,
  statusesFor,
  windowSince,
  withExtras,
  AUDIT_STATUSES,
} from "./audit";

describe("shareRows", () => {
  it("orders by count, keeps unknown keys verbatim and computes shares", () => {
    const rows = shareRows({ rule: 6, llm: 2, teleport: 2, unknown: 0 });
    expect(rows.map((r) => r.key)).toEqual(["rule", "llm", "teleport"]);
    expect(rows[0]).toMatchObject({ count: 6, share: 0.6 });
    expect(rows.reduce((sum, r) => sum + r.share, 0)).toBeCloseTo(1);
  });

  it("is empty for no decisions", () => {
    expect(shareRows({})).toEqual([]);
  });
});

describe("statusesFor", () => {
  it("collects the outcomes of one route source, null source as 'unknown'", () => {
    const buckets = [
      { route_source: "rule", status: "ok" as const, count: 3 },
      { route_source: "rule", status: "needs_confirmation" as const, count: 1 },
      { route_source: null, status: "blocked" as const, count: 2 },
    ];
    expect(statusesFor(buckets, "rule").map((r) => [r.key, r.count])).toEqual([
      ["ok", 3],
      ["needs_confirmation", 1],
    ]);
    expect(statusesFor(buckets, "unknown").map((r) => r.key)).toEqual(["blocked"]);
  });
});

describe("formatting helpers", () => {
  it("formats shares as whole percents and keeps tiny shares visible", () => {
    expect(formatShare(0.5, "en")).toBe("50%");
    expect(formatShare(0.001, "en")).toBe("<1%");
    expect(formatShare(0, "en")).toBe("0%");
  });

  it("recognises the audit mask and shortens ids", () => {
    expect(isMasked("***")).toBe(true);
    expect(isMasked("WH-EAST")).toBe(false);
    expect(shortId("0123456789abcdef")).toBe("01234567");
    expect(shortId(null)).toBeNull();
  });

  it("computes window starts from now", () => {
    const now = Date.parse("2026-10-26T12:00:00Z");
    expect(windowSince("24h", now)).toBe("2026-10-25T12:00:00.000Z");
    expect(windowSince("7d", now)).toBe("2026-10-19T12:00:00.000Z");
  });

  it("puts known values first and appends unseen ones", () => {
    expect(withExtras(AUDIT_STATUSES, ["ok", "quarantined", "chat"])).toEqual([...AUDIT_STATUSES, "quarantined"]);
  });
});

describe("audit filters", () => {
  it("reads filters from the URL and drops an unknown time preset", () => {
    const filters = readAuditFilters(new URLSearchParams("user=ops&status=ok&since=forever&grant_id=g1&page=3"));
    expect(filters).toEqual({ user: "ops", status: "ok", grant_id: "g1" });
  });

  it("turns filters into an API query with the preset resolved at call time", () => {
    const now = Date.parse("2026-10-26T12:00:00Z");
    expect(auditQueryFor({ route_source: "llm", since: "24h" }, 2, 25, now)).toMatchObject({
      route_source: "llm",
      since: "2026-10-25T12:00:00.000Z",
      page: 2,
      page_size: 25,
      user: undefined,
    });
  });
});

describe("request chain", () => {
  it("lists by request id and by grant id", () => {
    expect(chainQueries({ request_id: "r1", grant_id: "g1" })).toEqual([
      { request_id: "r1", page_size: 200 },
      { grant_id: "g1", page_size: 200 },
    ]);
    expect(chainQueries({ request_id: null, grant_id: null })).toEqual([]);
  });

  it("merges siblings oldest first without duplicates", () => {
    const a = auditEntry({ id: 3, ts: "2026-10-26T08:00:00Z" });
    const b = auditEntry({ id: 5, ts: "2026-10-26T08:00:05Z" });
    const c = auditEntry({ id: 4, ts: "2026-10-26T08:00:05Z" });
    expect(mergeChain([b, a, c, a]).map((e) => e.id)).toEqual([3, 4, 5]);
  });

  it("fetches siblings and a parent that neither listing returned", async () => {
    const preview = auditEntry({ id: 9, status: "needs_confirmation", grant_id: "g1", ts: "2026-10-26T08:00:00Z" });
    const started = auditEntry({ id: 10, status: "started", grant_id: "g1", parent_id: 9, ts: "2026-10-26T08:00:02Z" });
    const done = auditEntry({ id: 11, status: "ok", grant_id: "g1", parent_id: 9, ts: "2026-10-26T08:00:02Z" });
    const list = vi.fn(async () => ({ items: [done, started] }));
    const get = vi.fn(async () => preview);
    const chain = await loadRequestChain(done, { list, get });
    expect(list).toHaveBeenCalledWith({ grant_id: "g1", page_size: 200 }, undefined);
    expect(get).toHaveBeenCalledWith(9, undefined);
    expect(chain.map((e) => e.status)).toEqual(["needs_confirmation", "started", "ok"]);
  });

  const failingParent = (error: unknown) => {
    const child = auditEntry({ id: 2, parent_id: 1, request_id: "r9" });
    return loadRequestChain(child, {
      list: async () => ({ items: [child] }),
      get: async () => {
        throw error;
      },
    });
  };
  const httpError = (status: number, code: string) => new ApiError({ status, code, message: code, kind: "http" });

  it("keeps the chain when the parent is gone (404) or not readable by this caller (403)", async () => {
    expect((await failingParent(httpError(404, "not_found"))).map((e) => e.id)).toEqual([2]);
    expect((await failingParent(httpError(403, "forbidden"))).map((e) => e.id)).toEqual([2]);
  });

  it("rejects on any other failure instead of showing a chain that silently lacks its parent", async () => {
    const offline = new ApiError({ status: 0, code: "network_error", message: "unreachable", kind: "network" });
    await expect(failingParent(offline)).rejects.toBe(offline);
    const crash = httpError(500, "internal_error");
    await expect(failingParent(crash)).rejects.toBe(crash);
    const bug = new TypeError("x is not a function");
    await expect(failingParent(bug)).rejects.toBe(bug);
  });
});
