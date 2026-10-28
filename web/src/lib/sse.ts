/**
 * Server-sent events over `fetch` + `ReadableStream`.
 *
 * `EventSource` cannot POST a body or send an `Authorization` header, so the query stream is
 * read by hand. The parser follows the WHATWG event-stream rules (HTML §9.2.6): lines end in
 * CRLF, LF or CR; `:` starts a comment (the server's heartbeat); a blank line dispatches the
 * pending event; an event cut off by the end of the stream is discarded.
 */

export interface SseMessage {
  /** `event:` field; `"message"` when the frame had none. */
  event: string;
  /** `data:` lines joined with `\n`. */
  data: string;
  /** Last `id:` seen on the stream, or null. */
  id: string | null;
}

export class SseParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];
  private hasData = false;
  private lastId: string | null = null;
  /** The previous chunk ended in CR: a leading LF in the next chunk belongs to that line end. */
  private pendingCR = false;

  /** Feed decoded text; returns every event completed by it, in order. */
  push(text: string): SseMessage[] {
    const out: SseMessage[] = [];
    let chunk = text;
    if (this.pendingCR) {
      this.pendingCR = false;
      if (chunk.startsWith("\n")) chunk = chunk.slice(1);
    }
    const buf = this.buffer + chunk;
    let start = 0;
    for (let i = 0; i < buf.length; i++) {
      const ch = buf[i];
      if (ch !== "\n" && ch !== "\r") continue;
      const line = buf.slice(start, i);
      if (ch === "\r") {
        if (i + 1 < buf.length) {
          if (buf[i + 1] === "\n") i++;
        } else {
          this.pendingCR = true;
        }
      }
      start = i + 1;
      this.processLine(line, out);
    }
    this.buffer = buf.slice(start);
    return out;
  }

  private processLine(line: string, out: SseMessage[]): void {
    if (line === "") {
      this.dispatch(out);
      return;
    }
    if (line.startsWith(":")) return; // comment, e.g. ": heartbeat"
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.eventName = value;
        break;
      case "data":
        this.dataLines.push(value);
        this.hasData = true;
        break;
      case "id":
        if (!value.includes("\u0000")) this.lastId = value;
        break;
      default:
        break; // "retry" and unknown fields are ignored
    }
  }

  private dispatch(out: SseMessage[]): void {
    if (this.hasData) {
      out.push({ event: this.eventName || "message", data: this.dataLines.join("\n"), id: this.lastId });
    }
    this.eventName = "";
    this.dataLines = [];
    this.hasData = false;
  }
}

export class SseIdleTimeoutError extends Error {
  constructor(readonly idleMs: number) {
    super(`The stream sent nothing for ${idleMs} ms.`);
    this.name = "SseIdleTimeoutError";
  }
}

/**
 * Reading the body failed (the connection dropped mid-stream); `cause` is what the read threw.
 * Only the read is wrapped: an error thrown by the message handler reaches the caller as it is.
 */
export class SseReadError extends Error {
  constructor(cause: unknown) {
    super("The event stream could not be read.", { cause });
    this.name = "SseReadError";
  }
}

export interface ReadSseOptions {
  signal?: AbortSignal;
  /** Abort when no bytes (heartbeats included) arrive for this long. */
  idleTimeoutMs?: number;
}

/** Return `"stop"` from the callback to cancel the stream early. */
export type SseHandler = (message: SseMessage) => void | "stop";

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

/**
 * Read an event stream to its end, calling `onMessage` for each event. Rejects with
 * `SseIdleTimeoutError` after `idleTimeoutMs` of silence, with the abort reason when `signal`
 * fires, with `SseReadError` when reading the body fails, and with whatever `onMessage` throws;
 * in every case the underlying body is cancelled.
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onMessage: SseHandler,
  { signal, idleTimeoutMs = 45_000 }: ReadSseOptions = {},
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SseParser();
  let finished = false;

  let rejectAbort: ((reason: unknown) => void) | undefined;
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = reject;
  });
  aborted.catch(() => undefined); // observed through Promise.race below
  const onAbort = () => rejectAbort?.(signal ? abortReason(signal) : undefined);
  if (signal?.aborted) onAbort();
  signal?.addEventListener("abort", onAbort, { once: true });

  const deliver = (text: string): boolean => {
    for (const message of parser.push(text)) {
      if (onMessage(message) === "stop") return true;
    }
    return false;
  };

  // an abort errors the body as well: that stays the abort it is, not a failed read
  const read = () =>
    reader.read().catch((error: unknown) => {
      throw signal?.aborted ? error : new SseReadError(error);
    });

  try {
    for (;;) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const waits: Promise<never>[] = [aborted];
      if (Number.isFinite(idleTimeoutMs) && idleTimeoutMs > 0) {
        waits.push(
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => reject(new SseIdleTimeoutError(idleTimeoutMs)), idleTimeoutMs);
          }),
        );
      }
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await Promise.race([read(), ...waits]);
      } finally {
        clearTimeout(timer);
      }
      if (result.done) {
        finished = true;
        deliver(decoder.decode());
        return;
      }
      if (deliver(decoder.decode(result.value, { stream: true }))) return;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    if (!finished) await reader.cancel().catch(() => undefined);
    try {
      reader.releaseLock();
    } catch {
      // a read still pending on an exotic stream; the cancel above already ended it
    }
  }
}
