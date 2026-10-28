import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type QueryOut, type QueryRequest } from "@/lib/api";

import { ConversationProvider, toQueryError, useConversation } from "./conversation-provider";

const streamQuery = vi.hoisted(() => vi.fn());
const refresh = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, streamQuery };
});
vi.mock("@/components/providers/approvals-provider", () => ({ useApprovals: () => ({ refresh }) }));

const answer = (over: Partial<QueryOut> = {}): QueryOut => ({
  conversation_id: "c1",
  query: "q",
  decision: null,
  answer: "19 rows matched.",
  answer_source: "template",
  columns: [],
  rows: [],
  aggregates: {},
  provenance: null,
  narrative_stripped: 0,
  approval: null,
  clarify_options: [],
  trace: [],
  error: null,
  audit_ids: [],
  ...over,
});

const wrapper = ({ children }: { children: ReactNode }) => <ConversationProvider>{children}</ConversationProvider>;
const body = (call: number): QueryRequest => streamQuery.mock.calls[call]![0] as QueryRequest;

beforeEach(() => {
  streamQuery.mockReset();
  refresh.mockReset();
});

describe("ConversationProvider", () => {
  it("sends a picked option's explicit_params with its target, again on retry, and never after", async () => {
    const params = { _scope_ack: ["category:sku=packaging"] };
    streamQuery.mockRejectedValueOnce(
      new ApiError({ status: 0, code: "network_error", message: "unreachable", kind: "network" }),
    );
    streamQuery.mockResolvedValue(answer());
    const { result } = renderHook(() => useConversation(), { wrapper });

    act(() =>
      result.current.send("low stock items except packaging", { explicitTarget: "low_stock", explicitParams: params }),
    );
    await waitFor(() => expect(result.current.turns[0]?.status).toBe("error"));
    expect(body(0)).toEqual({
      query: "low stock items except packaging",
      explicit_target: "low_stock",
      explicit_params: params,
      conversation_id: null,
    });
    expect(body(0).explicit_params).toBe(params); // passed through untouched

    act(() => result.current.retry(result.current.turns[0]!.id));
    await waitFor(() => expect(result.current.turns[0]?.status).toBe("done"));
    expect(body(1)).toMatchObject({ explicit_target: "low_stock", explicit_params: params });

    // the next question is routed afresh: the one-shot acknowledgement is not carried over
    act(() => result.current.send("late shipments"));
    await waitFor(() => expect(result.current.turns[1]?.status).toBe("done"));
    expect(body(2)).toEqual({
      query: "late shipments",
      explicit_target: null,
      explicit_params: null,
      conversation_id: "c1",
    });
  });

  it("fixes an approval's deadline at receipt from the server's time left, never from its expires_at", async () => {
    streamQuery.mockResolvedValue(
      answer({
        answer_source: "system",
        // long past on any computer's clock: the server's 42 s are what count
        approval: { grant_id: "g1", target: "create_po", summary: "Create PO", label: "Create purchase order", expires_at: "2000-01-01T00:00:00Z", expires_in_s: 42 },
      }),
    );
    const { result } = renderHook(() => useConversation(), { wrapper });
    act(() => result.current.send("create a purchase order"));
    await waitFor(() => expect(result.current.turns[0]?.status).toBe("done"));
    const deadline = result.current.turns[0]!.approvalDeadline!;
    expect(deadline.windowMs).toBe(42_000);
    const left = deadline.endsAt - performance.now();
    expect(left).toBeGreaterThan(40_000);
    expect(left).toBeLessThanOrEqual(42_000);
    expect(refresh).toHaveBeenCalled(); // the pending list learns about it
  });

  it("drops params that come without a target", async () => {
    streamQuery.mockResolvedValue(answer());
    const { result } = renderHook(() => useConversation(), { wrapper });
    act(() => result.current.send("low stock", { explicitParams: { _scope_ack: ["x"] } }));
    await waitFor(() => expect(result.current.turns[0]?.status).toBe("done"));
    expect(body(0)).toMatchObject({ explicit_target: null, explicit_params: null });
    expect(result.current.turns[0]?.explicitParams).toBeNull();
  });

  it("does not re-send a question the server failed with an error frame", async () => {
    streamQuery.mockRejectedValueOnce(
      new ApiError({ status: 200, code: "query_failed", message: "The request failed while running.", kind: "stream" }),
    );
    streamQuery.mockResolvedValue(answer());
    const { result } = renderHook(() => useConversation(), { wrapper });

    act(() => result.current.send("Create a purchase order for 200 units of SKU-10042"));
    await waitFor(() => expect(result.current.turns[0]?.status).toBe("error"));
    expect(result.current.turns[0]?.error).toMatchObject({ code: "query_failed", retryable: false });

    // a verdict stands: asking again could mint a second grant for the same write
    act(() => result.current.retry(result.current.turns[0]!.id));
    expect(streamQuery).toHaveBeenCalledTimes(1);
    expect(result.current.turns[0]?.status).toBe("error");
  });

  it("does not re-send after a failure of the console's own code", async () => {
    streamQuery.mockRejectedValueOnce(new TypeError("dispatch failed"));
    const { result } = renderHook(() => useConversation(), { wrapper });
    act(() => result.current.send("late shipments"));
    await waitFor(() => expect(result.current.turns[0]?.status).toBe("error"));
    expect(result.current.turns[0]?.error).toMatchObject({ code: "unexpected", retryable: false });
    act(() => result.current.retry(result.current.turns[0]!.id));
    expect(streamQuery).toHaveBeenCalledTimes(1);
  });
});

const failure = (kind: ApiError["kind"], code: string, status = 0) => new ApiError({ status, code, message: code, kind });

describe("toQueryError", () => {
  it.each([
    ["the network", failure("network", "network_error")],
    ["a timeout", failure("timeout", "stream_timeout")],
    ["a stream cut short", failure("stream", "stream_incomplete", 200)],
    ["an unreadable stream", failure("stream", "stream_unavailable", 200)],
    ["a gateway that could not reach the API", failure("http", "http_502", 502)],
  ])("lets a question lost to %s be asked again", (_, error) => {
    expect(toQueryError(error).retryable).toBe(true);
  });

  it.each([
    ["a server error frame", failure("stream", "query_failed", 200)],
    ["an HTTP error", failure("http", "validation_error", 422)],
    ["a server crash", failure("http", "internal_error", 500)],
    ["a malformed frame", failure("parse", "invalid_frame", 200)],
    ["a client bug", new TypeError("x is not a function")],
  ])("treats %s as a verdict", (_, error) => {
    expect(toQueryError(error).retryable).toBe(false);
  });
});
