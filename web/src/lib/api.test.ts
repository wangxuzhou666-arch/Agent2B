// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  ApiError,
  apiUrl,
  isAbortError,
  isTransportFailure,
  requestLanguage,
  setRequestLocale,
  setUnauthorizedHandler,
  STREAM_INCOMPLETE,
  streamQuery,
  toFailure,
  type TraceStep,
} from "./api";
import { clearSession, writeSession } from "./session";

const fetchMock = vi.fn<typeof fetch>();

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function sse(frames: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "content-type": "text/event-stream" } });
}

const RESPONSE = {
  conversation_id: "c1",
  query: "How many shipments are late?",
  decision: null,
  answer: "3 shipments are late.",
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
};

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  writeSession({
    token: "test-token",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    userId: "operator",
    roles: ["operator"],
    demoMode: true,
  });
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  clearSession();
  setRequestLocale("en");
});

const headersOf = (call: number) => fetchMock.mock.calls[call]![1]?.headers as Record<string, string>;

describe("request", () => {
  it("sends the bearer token and returns typed JSON", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [] }));
    await expect(api.listApprovals()).resolves.toEqual({ items: [] });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://localhost:8000/api/approvals");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer test-token");
    expect(init?.credentials).toBe("omit");
  });

  it("does not send the token to the public endpoints", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { status: "ok", version: "0.1.0", demo_mode: true }));
    await api.health();
    expect((fetchMock.mock.calls[0]![1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("turns the error envelope into an ApiError", async () => {
    fetchMock.mockResolvedValueOnce(json(410, { error: { code: "approval_expired", message: "Expired." } }));
    const error = await api.approve("g1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 410, code: "approval_expired", message: "Expired.", kind: "http" });
  });

  it("runs the unauthorized handler on a 401 from an authenticated call only", async () => {
    const onUnauthorized = vi.fn();
    const unregister = setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(json(401, { error: { code: "invalid_token", message: "No." } }));
    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenLastCalledWith("test-token"); // the token that was refused

    fetchMock.mockResolvedValueOnce(json(401, { error: { code: "bad_credentials", message: "No." } }));
    await expect(api.login("a", "b")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1); // a wrong password is not a lost session
    unregister();
  });

  it("reports an unreachable server as a network error", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api.health()).rejects.toMatchObject({ kind: "network", code: "network_error", status: 0 });
  });

  it("calls an answer that is not JSON an invalid response, with the parser's error as cause", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>proxy page</html>", { status: 200 }));
    const error = await api.listApprovals().catch((e: unknown) => e);
    expect(error).toMatchObject({ kind: "parse", code: "invalid_response", status: 200 });
    expect((error as ApiError).cause).toBeInstanceOf(SyntaxError);
  });

  it("reports a connection dropped while the answer was read as a network error, keeping the cause", async () => {
    const dropped = new TypeError("terminated");
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"items": ['));
      },
      pull(controller) {
        controller.error(dropped);
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { status: 200 }));
    const error = await api.listApprovals().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "network", code: "network_error", status: 0 });
    expect((error as ApiError).cause).toBe(dropped);
  });

  it("lets an abort during the read of the answer through as an abort", async () => {
    const aborted = new DOMException("The operation was aborted.", "AbortError");
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(aborted);
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { status: 200 }));
    const error = await api.listApprovals().catch((e: unknown) => e);
    expect(isAbortError(error)).toBe(true);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it("builds query strings without empty values", () => {
    expect(apiUrl("/api/audit", { page: 2, status: null, user: "", target: undefined })).toBe(
      "http://localhost:8000/api/audit?page=2",
    );
  });
});

describe("Accept-Language", () => {
  it("asks for the console's language on every call: authenticated or public, plain or streamed", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [] }));
    await api.listApprovals();
    expect(headersOf(0)["Accept-Language"]).toBe("en"); // before any switch: the default locale

    setRequestLocale("zh");
    expect(requestLanguage()).toBe("zh-CN");
    fetchMock.mockResolvedValueOnce(json(200, { items: [] }));
    await api.listEntities();
    fetchMock.mockResolvedValueOnce(json(200, { status: "ok", version: "0.1.0", demo_mode: true }));
    await api.health();
    fetchMock.mockResolvedValueOnce(json(200, { status: "signed_out" }));
    await api.logout("old-token");
    fetchMock.mockResolvedValueOnce(sse([`event: response\ndata: ${JSON.stringify(RESPONSE)}\n\n`]));
    await streamQuery({ query: "x" });
    expect([1, 2, 3, 4].map((call) => headersOf(call)["Accept-Language"])).toEqual(["zh-CN", "zh-CN", "zh-CN", "zh-CN"]);

    setRequestLocale("en");
    fetchMock.mockResolvedValueOnce(json(200, { items: [] }));
    await api.listApprovals();
    expect(headersOf(5)["Accept-Language"]).toBe("en");
  });
});

describe("logout", () => {
  it("revokes the token it is given, not the session's, and its 401 ends nothing", async () => {
    const onUnauthorized = vi.fn();
    const unregister = setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(json(200, { status: "signed_out" }));
    await expect(api.logout("old-token")).resolves.toEqual({ status: "signed_out" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://localhost:8000/api/auth/logout");
    expect(init?.method).toBe("POST");
    expect(headersOf(0).Authorization).toBe("Bearer old-token");

    // signed out twice, or already expired: the token is dead either way, the session is not touched
    fetchMock.mockResolvedValueOnce(json(401, { error: { code: "token_revoked", message: "The session was signed out." } }));
    await expect(api.logout("old-token")).rejects.toMatchObject({ status: 401, code: "token_revoked" });
    expect(onUnauthorized).not.toHaveBeenCalled();
    unregister();
  });

  it("tells the unauthorized handler a call carried no token at all", async () => {
    const onUnauthorized = vi.fn();
    const unregister = setUnauthorizedHandler(onUnauthorized);
    clearSession();
    fetchMock.mockResolvedValueOnce(json(401, { error: { code: "not_authenticated", message: "Sign in." } }));
    await expect(api.listApprovals()).rejects.toMatchObject({ status: 401 });
    expect(headersOf(0).Authorization).toBeUndefined();
    expect(onUnauthorized).toHaveBeenCalledWith(null);
    unregister();
  });
});

describe("isTransportFailure", () => {
  const failure = (kind: ApiError["kind"], status: number, code = `http_${status}`) =>
    new ApiError({ status, code, message: code, kind });

  it.each([
    ["the network", failure("network", 0, "network_error")],
    ["a timeout", failure("timeout", 0, "stream_timeout")],
    ["a bad gateway", failure("http", 502)],
    ["an unavailable gateway", failure("http", 503)],
    ["a gateway timeout", failure("http", 504)],
    ["a stream cut short", failure("stream", 200, STREAM_INCOMPLETE)],
  ])("counts %s as lost in transit", (_, error) => {
    expect(isTransportFailure(error)).toBe(true);
    expect(error.retryable).toBe(true);
    expect(toFailure(error)).toMatchObject({ retryable: true, status: error.status, code: error.code });
  });

  it.each([
    ["a server crash", failure("http", 500, "internal_error")],
    ["a conflict", failure("http", 409, "conflict")],
    ["an expired confirmation", failure("http", 410, "approval_expired")],
    ["an error frame", failure("stream", 200, "query_failed")],
    ["an unreadable answer", failure("parse", 200, "invalid_response")],
  ])("counts %s as a verdict", (_, error) => {
    expect(isTransportFailure(error)).toBe(false);
    expect(error.retryable).toBe(false);
  });

  it("treats a failure of this client's own code as a verdict", () => {
    const bug = new TypeError("x is not a function");
    expect(isTransportFailure(bug)).toBe(false);
    expect(toFailure(bug)).toEqual({ code: "unexpected", message: "Unexpected error.", kind: "parse", status: 0, retryable: false });
  });
});

describe("streamQuery", () => {
  it("delivers trace frames live and resolves with the response frame", async () => {
    fetchMock.mockResolvedValueOnce(
      sse([
        'event: trace\ndata: {"step":"candidates","status":"ok","facts":{"count":7}}\n\n',
        ": heartbeat\n\n",
        'event: trace\ndata: {"step":"understand","status":"ok","source":"rule"}\n\n',
        `event: response\ndata: ${JSON.stringify(RESPONSE)}\n\n`,
      ]),
    );
    const steps: TraceStep[] = [];
    const out = await streamQuery({ query: "How many shipments are late?" }, { onTrace: (s) => steps.push(s) });
    expect(steps.map((s) => s.step)).toEqual(["candidates", "understand"]);
    expect(out.answer).toBe("3 shipments are late.");
    const init = fetchMock.mock.calls[0]![1];
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>).Accept).toBe("text/event-stream");
    expect(JSON.parse(init?.body as string)).toEqual({ query: "How many shipments are late?" });
  });

  it("rejects with the envelope of an error frame", async () => {
    fetchMock.mockResolvedValueOnce(
      sse(['event: error\ndata: {"error":{"code":"query_failed","message":"The request failed while running."}}\n\n']),
    );
    await expect(streamQuery({ query: "x" })).rejects.toMatchObject({ code: "query_failed", kind: "stream" });
  });

  it("rejects when the stream ends without an answer", async () => {
    fetchMock.mockResolvedValueOnce(sse(['event: trace\ndata: {"step":"candidates","status":"ok"}\n\n']));
    await expect(streamQuery({ query: "x" })).rejects.toMatchObject({ code: "stream_incomplete" });
  });

  it("rejects a malformed frame instead of guessing", async () => {
    fetchMock.mockResolvedValueOnce(sse(["event: trace\ndata: not json\n\n"]));
    await expect(streamQuery({ query: "x" })).rejects.toMatchObject({ code: "invalid_frame", kind: "parse" });
  });

  it("maps HTTP errors before the stream starts", async () => {
    fetchMock.mockResolvedValueOnce(json(422, { error: { code: "validation_error", message: "query: too long" } }));
    await expect(streamQuery({ query: "x" })).rejects.toMatchObject({ status: 422, code: "validation_error" });
  });

  it("accepts a buffered JSON answer", async () => {
    fetchMock.mockResolvedValueOnce(json(200, RESPONSE));
    await expect(streamQuery({ query: "x" })).resolves.toMatchObject({ conversation_id: "c1" });
  });

  it("reports a connection dropped mid-stream as a network error, with the read's error as cause", async () => {
    const dropped = new TypeError("network error");
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: trace\ndata: {"step":"candidates","status":"ok"}\n\n'));
      },
      pull(controller) {
        controller.error(dropped);
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { headers: { "content-type": "text/event-stream" } }));
    const steps: TraceStep[] = [];
    const error = await streamQuery({ query: "x" }, { onTrace: (s) => steps.push(s) }).catch((e: unknown) => e);
    expect(steps).toHaveLength(1);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "network", code: "network_error", status: 0 });
    expect((error as ApiError).cause).toBe(dropped);
  });

  it("passes an error thrown by onTrace through as it is, not as an unreachable server", async () => {
    fetchMock.mockResolvedValueOnce(
      sse([
        'event: trace\ndata: {"step":"candidates","status":"ok"}\n\n',
        `event: response\ndata: ${JSON.stringify(RESPONSE)}\n\n`,
      ]),
    );
    const bug = new RangeError("the trace view broke");
    const error = await streamQuery(
      { query: "x" },
      {
        onTrace: () => {
          throw bug;
        },
      },
    ).catch((e: unknown) => e);
    expect(error).toBe(bug);
  });

  it("keeps the cause of a failed request", async () => {
    const refused = new TypeError("Failed to fetch");
    fetchMock.mockRejectedValueOnce(refused);
    const error = await streamQuery({ query: "x" }).catch((e: unknown) => e);
    expect(error).toMatchObject({ kind: "network", code: "network_error" });
    expect((error as ApiError).cause).toBe(refused);
  });

  it("surfaces Stop as an AbortError", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementationOnce(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }),
    );
    const pending = streamQuery({ query: "x" }, { signal: controller.signal });
    controller.abort();
    const error = await pending.catch((e: unknown) => e);
    expect(isAbortError(error)).toBe(true);
  });
});
