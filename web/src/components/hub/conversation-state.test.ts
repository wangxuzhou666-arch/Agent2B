import { describe, expect, it } from "vitest";

import type { QueryOut, TraceStep } from "@/lib/api";

import { conversationReducer, initialConversation, type ConversationState } from "./conversation-state";

const step: TraceStep = { step: "candidates", status: "ok", source: null, facts: { count: 7 }, duration_ms: 0.5 };

const response = (over: Partial<QueryOut> = {}): QueryOut => ({
  conversation_id: "c1",
  query: "q",
  decision: null,
  answer: "a",
  answer_source: "template",
  columns: [],
  rows: [],
  aggregates: {},
  provenance: null,
  narrative_stripped: 0,
  approval: null,
  clarify_options: [],
  trace: [step],
  error: null,
  audit_ids: [],
  ...over,
});

function started(): ConversationState {
  return conversationReducer(initialConversation, {
    type: "start",
    id: "t1",
    query: "q",
    explicitTarget: null,
    explicitParams: null,
    startedAt: 10,
  });
}

describe("conversationReducer", () => {
  it("appends live trace steps to the streaming turn", () => {
    const state = conversationReducer(started(), { type: "trace", id: "t1", step });
    expect(state.turns[0]).toMatchObject({ status: "streaming", steps: [step] });
  });

  it("finishes a turn with the response and its full trace", () => {
    const state = conversationReducer(started(), {
      type: "respond",
      id: "t1",
      response: response(),
      elapsedMs: 412,
      approvalDeadline: null,
    });
    expect(state.turns[0]).toMatchObject({ status: "done", elapsedMs: 412, steps: [step], approvalDeadline: null });
  });

  it("keeps the deadline of an approval as fixed at receipt, and only with an approval", () => {
    const deadline = { endsAt: 900_050, windowMs: 900_000 };
    const held = conversationReducer(started(), {
      type: "respond",
      id: "t1",
      response: response({
        approval: { grant_id: "g1", target: "create_po", summary: "Create PO", label: "Create purchase order", expires_at: "2026-10-26T10:15:00Z", expires_in_s: 900 },
      }),
      elapsedMs: 50,
      approvalDeadline: deadline,
    });
    expect(held.turns[0]?.approvalDeadline).toBe(deadline);
    const plain = conversationReducer(started(), { type: "respond", id: "t1", response: response(), elapsedMs: 50, approvalDeadline: deadline });
    expect(plain.turns[0]?.approvalDeadline).toBeNull();
  });

  it("ignores frames for a turn that is no longer streaming", () => {
    const cancelled = conversationReducer(started(), { type: "cancel", id: "t1", elapsedMs: 5 });
    expect(conversationReducer(cancelled, { type: "trace", id: "t1", step })).toBe(cancelled);
    const late = conversationReducer(cancelled, {
      type: "respond",
      id: "t1",
      response: response(),
      elapsedMs: 9,
      approvalDeadline: null,
    });
    expect(late.turns[0]?.status).toBe("cancelled");
  });

  it("records failures and restarts a turn for retry", () => {
    const failed = conversationReducer(started(), {
      type: "fail",
      id: "t1",
      error: { code: "network_error", message: "x", kind: "network", status: 0, retryable: true },
      elapsedMs: 7,
    });
    expect(failed.turns[0]?.status).toBe("error");
    const again = conversationReducer(failed, { type: "restart", id: "t1", startedAt: 99 });
    expect(again.turns[0]).toMatchObject({ status: "streaming", error: null, steps: [], startedAt: 99 });
  });

  it("keeps a picked option's target and params through a retry", () => {
    const params = { _scope_ack: ["category:sku=packaging"] };
    const picked = conversationReducer(initialConversation, {
      type: "start",
      id: "t2",
      query: "low stock items except packaging",
      explicitTarget: "low_stock",
      explicitParams: params,
      startedAt: 1,
    });
    expect(picked.turns[0]).toMatchObject({ explicitTarget: "low_stock", explicitParams: params });
    const again = conversationReducer(picked, { type: "restart", id: "t2", startedAt: 2 });
    expect(again.turns[0]?.explicitParams).toBe(params);
    expect(again.turns[0]?.explicitTarget).toBe("low_stock");
  });

  it("resets to an empty conversation", () => {
    expect(conversationReducer(started(), { type: "reset" })).toEqual(initialConversation);
  });
});
