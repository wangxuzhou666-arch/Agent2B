// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { readSseStream, SseIdleTimeoutError, SseParser, SseReadError, type SseMessage } from "./sse";

const encoder = new TextEncoder();

function streamOf(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
}

/** Feed `text` to a fresh parser split at every position in `cuts`. */
function parseInPieces(text: string, cuts: number[]): SseMessage[] {
  const parser = new SseParser();
  const out: SseMessage[] = [];
  let last = 0;
  for (const cut of [...cuts, text.length]) {
    out.push(...parser.push(text.slice(last, cut)));
    last = cut;
  }
  return out;
}

const STREAM =
  'event: trace\ndata: {"step":"candidates","status":"ok"}\n\n' +
  ": heartbeat\n\n" +
  'event: trace\ndata: {"step":"understand","status":"ok","source":"rule"}\n\n' +
  'event: response\ndata: {"conversation_id":"c1","query":"库存不足的物料有哪些"}\n\n';

const EXPECTED: SseMessage[] = [
  { event: "trace", data: '{"step":"candidates","status":"ok"}', id: null },
  { event: "trace", data: '{"step":"understand","status":"ok","source":"rule"}', id: null },
  { event: "response", data: '{"conversation_id":"c1","query":"库存不足的物料有哪些"}', id: null },
];

describe("SseParser", () => {
  it("parses whole frames and ignores heartbeat comments", () => {
    expect(parseInPieces(STREAM, [])).toEqual(EXPECTED);
  });

  it("gives the same events for every single split point (mid-field, mid-data, mid-blank-line)", () => {
    for (let cut = 1; cut < STREAM.length; cut++) {
      expect(parseInPieces(STREAM, [cut])).toEqual(EXPECTED);
    }
  });

  it("survives one character per chunk", () => {
    const cuts = Array.from({ length: STREAM.length - 1 }, (_, i) => i + 1);
    expect(parseInPieces(STREAM, cuts)).toEqual(EXPECTED);
  });

  it("treats CRLF split across chunks as one line ending", () => {
    const crlf = "event: trace\r\ndata: a\r\n\r\nevent: response\r\ndata: b\r\n\r\n";
    for (let cut = 1; cut < crlf.length; cut++) {
      expect(parseInPieces(crlf, [cut]).map((m) => [m.event, m.data])).toEqual([
        ["trace", "a"],
        ["response", "b"],
      ]);
    }
  });

  it("accepts bare CR line endings", () => {
    expect(parseInPieces("data: x\r\r", []).map((m) => m.data)).toEqual(["x"]);
  });

  it("joins multiple data lines with a newline and defaults the event name", () => {
    expect(parseInPieces("data: one\ndata: two\n\n", [])).toEqual([{ event: "message", data: "one\ntwo", id: null }]);
  });

  it("strips exactly one leading space and keeps a value without one", () => {
    expect(parseInPieces("data:  two spaces\ndata:none\n\n", [])[0]?.data).toBe(" two spaces\nnone");
  });

  it("does not dispatch an event without data, and drops an unterminated final event", () => {
    expect(parseInPieces("event: trace\n\n", [])).toEqual([]);
    expect(parseInPieces('event: response\ndata: {"partial":', [])).toEqual([]);
  });

  it("remembers the last event id", () => {
    expect(parseInPieces("id: 7\ndata: a\n\n", [])[0]?.id).toBe("7");
  });
});

describe("readSseStream", () => {
  it("decodes multi-byte UTF-8 split across network chunks", async () => {
    const bytes = encoder.encode('event: response\ndata: {"q":"库存不足"}\n\n');
    const split = bytes.indexOf(0xe5) + 1; // inside the first CJK character
    const seen: SseMessage[] = [];
    await readSseStream(streamOf([bytes.slice(0, split), bytes.slice(split)]), (m) => {
      seen.push(m);
    });
    expect(seen).toEqual([{ event: "response", data: '{"q":"库存不足"}', id: null }]);
  });

  it("stops early when the handler returns 'stop'", async () => {
    const seen: string[] = [];
    await readSseStream(streamOf(["data: 1\n\ndata: 2\n\n"]), (m) => {
      seen.push(m.data);
      return "stop";
    });
    expect(seen).toEqual(["1"]);
  });

  it("rejects with SseIdleTimeoutError when nothing arrives in time", async () => {
    vi.useFakeTimers();
    try {
      const silent = new ReadableStream<Uint8Array>({ start() {} });
      const done = readSseStream(silent, () => undefined, { idleTimeoutMs: 45_000 });
      const assertion = expect(done).rejects.toBeInstanceOf(SseIdleTimeoutError);
      await vi.advanceTimersByTimeAsync(45_000);
      await assertion;
    } finally {
      vi.useRealTimers();
    }
  });

  it("heartbeats reset the idle timer", async () => {
    vi.useFakeTimers();
    try {
      let push: (text: string) => void = () => undefined;
      let close: () => void = () => undefined;
      const live = new ReadableStream<Uint8Array>({
        start(controller) {
          push = (text) => controller.enqueue(encoder.encode(text));
          close = () => controller.close();
        },
      });
      const seen: string[] = [];
      const done = readSseStream(live, (m) => void seen.push(m.data), { idleTimeoutMs: 45_000 });
      for (let i = 0; i < 4; i++) {
        await vi.advanceTimersByTimeAsync(15_000);
        push(": heartbeat\n\n");
      }
      push("event: response\ndata: ok\n\n");
      close();
      await expect(done).resolves.toBeUndefined();
      expect(seen).toEqual(["ok"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("wraps a failed read in SseReadError (cause kept) and passes a handler's error through", async () => {
    const dropped = new TypeError("network error");
    const failing = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(dropped);
      },
    });
    const readError = await readSseStream(failing, () => undefined).catch((e: unknown) => e);
    expect(readError).toBeInstanceOf(SseReadError);
    expect((readError as SseReadError).cause).toBe(dropped);

    const bug = new RangeError("handler broke");
    const handlerError = await readSseStream(streamOf(["data: 1\n\n"]), () => {
      throw bug;
    }).catch((e: unknown) => e);
    expect(handlerError).toBe(bug);
  });

  it("rejects with the abort reason when the signal fires", async () => {
    const controller = new AbortController();
    const silent = new ReadableStream<Uint8Array>({ start() {} });
    const done = readSseStream(silent, () => undefined, { signal: controller.signal });
    controller.abort();
    await expect(done).rejects.toMatchObject({ name: "AbortError" });
  });
});
