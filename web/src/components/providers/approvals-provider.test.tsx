import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ApprovalItem } from "@/lib/api";

import { IntlProviders, type TestLocale } from "../../../test/render";
import { ApprovalsProvider, useApprovals } from "./approvals-provider";

const listApprovals = vi.hoisted(() => vi.fn());
const approve = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, listApprovals, approve } };
});

/** The console's language; a test switches it, then `rerender()`s. */
const lang = { current: "en" as TestLocale };
const wrapper = ({ children }: { children: ReactNode }) => (
  <IntlProviders locale={lang.current}>
    <ApprovalsProvider>{children}</ApprovalsProvider>
  </IntlProviders>
);

const pending: ApprovalItem = {
  grant_id: "g1",
  kind: "query",
  user_id: "demo-operator",
  target: "make_order",
  label: "Make order",
  summary: "Create a draft order (item_id=I-1, qty=200)",
  route_source: "rule",
  created_at: "2026-10-26T08:00:00Z",
  expires_at: "2026-10-26T08:15:00Z",
  expires_in_s: 900,
  mine: true,
};

beforeEach(() => {
  listApprovals.mockReset();
  approve.mockReset();
  lang.current = "en";
});

describe("ApprovalsProvider", () => {
  it("keeps a failure that is not an ApiError as the error, instead of dropping it", async () => {
    const bug = new TypeError("Cannot read properties of undefined (reading 'items')");
    listApprovals.mockRejectedValueOnce(bug);
    const { result } = renderHook(() => useApprovals(), { wrapper });
    await waitFor(() => expect(result.current.error).toBe(bug));
    expect(result.current.items).toBeNull(); // no list yet: the window shows the error, not a spinner
  });

  it("wraps a thrown non-Error so it still reads as a failure", async () => {
    listApprovals.mockRejectedValueOnce("boom");
    const { result } = renderHook(() => useApprovals(), { wrapper });
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    expect(result.current.error?.message).toBe("boom");
  });

  it("reports an ApiError as it is and clears it once a refresh succeeds", async () => {
    const unavailable = new ApiError({ status: 503, code: "unavailable", message: "busy", kind: "http" });
    listApprovals.mockRejectedValueOnce(unavailable);
    listApprovals.mockResolvedValue({ items: [pending] });
    const { result } = renderHook(() => useApprovals(), { wrapper });
    await waitFor(() => expect(result.current.error).toBe(unavailable));

    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(result.current.items).toEqual([pending]);
    expect(result.current.count).toBe(1);
  });

  it("marks a confirmation that left the list untouched: expired when its time was up, else resolved elsewhere", async () => {
    // the time left is the server's: a browser clock that says otherwise changes nothing
    const later = new Date(Date.now() + 3_600_000).toISOString();
    listApprovals.mockResolvedValueOnce({
      items: [
        { ...pending, grant_id: "g1", expires_at: later, expires_in_s: 60 },
        { ...pending, grant_id: "g2", expires_at: later, expires_in_s: 0 },
        { ...pending, grant_id: "g3", expires_at: later, expires_in_s: 60 },
      ],
    });
    listApprovals.mockResolvedValue({ items: [{ ...pending, grant_id: "g3", expires_at: later, expires_in_s: 59 }] });
    const { result } = renderHook(() => useApprovals(), { wrapper });
    await waitFor(() => expect(result.current.count).toBe(3));
    expect(result.current.arrival).toEqual({ mono: expect.any(Number) });

    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.count).toBe(1));
    const { store } = result.current;
    expect(store.get("g1")).toMatchObject({ phase: "elsewhere" });
    expect(store.get("g2")).toMatchObject({ phase: "expired", source: "list" });
    expect(store.get("g3")).toEqual({ phase: "pending" });
  });

  it("fetches the list again when the console switches language, its words being the reader's", async () => {
    listApprovals.mockResolvedValueOnce({ items: [pending] });
    listApprovals.mockResolvedValueOnce({ items: [{ ...pending, label: "新建订单" }] });
    const { result, rerender } = renderHook(() => useApprovals(), { wrapper });
    await waitFor(() => expect(result.current.items?.[0]?.label).toBe("Make order"));
    expect(listApprovals).toHaveBeenCalledTimes(1);

    lang.current = "zh";
    rerender();
    await waitFor(() => expect(result.current.items?.[0]?.label).toBe("新建订单"));
    expect(listApprovals).toHaveBeenCalledTimes(2);
    // still the same confirmation, not one that left and came back
    expect(result.current.store.get("g1")).toEqual({ phase: "pending" });
  });

  it("refreshes the list after an approval settles, without taking its leaving as resolved elsewhere", async () => {
    listApprovals.mockResolvedValueOnce({ items: [{ ...pending, grant_id: "g1" }] });
    listApprovals.mockResolvedValue({ items: [] });
    approve.mockResolvedValue({ grant_id: "g1", kind: "query", target: "make_order", status: "ok", audit_ids: [3] });
    const { result } = renderHook(() => useApprovals(), { wrapper });
    await waitFor(() => expect(result.current.count).toBe(1));

    await act(async () => {
      await result.current.store.approve("g1");
    });
    await waitFor(() => expect(result.current.count).toBe(0));
    expect(listApprovals).toHaveBeenCalledTimes(2);
    expect(result.current.store.get("g1")).toMatchObject({ phase: "approved", source: "reply" });
  });
});
