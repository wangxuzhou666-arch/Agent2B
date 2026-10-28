/**
 * Typed client for the twotrack API (contract: openapi.json → api-types.ts).
 *
 * - Every call goes to `API_BASE` with the bearer token from the session store and the
 *   console's language as `Accept-Language` (see `setRequestLocale`).
 * - Failures become `ApiError` (the server's `{error: {code, message}}` envelope, or a local
 *   code for network / timeout / stream problems).
 * - A 401 on an authenticated call runs the registered handler (sign out → /login).
 * - `streamQuery` reads the SSE answer stream with fetch + ReadableStream (see sse.ts).
 */
import { defaultLocale, htmlLang, type Locale } from "@/i18n/config";

import type { components, paths } from "./api-types";
import { API_BASE, STREAM_IDLE_TIMEOUT_MS } from "./config";
import { accessToken } from "./session";
import { readSseStream, SseIdleTimeoutError, SseReadError } from "./sse";

type Schemas = components["schemas"];

export type Role = Schemas["Role"];
export type HealthOut = Schemas["HealthOut"];
export type ProviderInfo = Schemas["ProviderInfo"];
export type TokenOut = Schemas["TokenOut"];
export type MeOut = Schemas["MeOut"];
export type LogoutOut = Schemas["LogoutOut"];
export type QueryRequest = Schemas["QueryRequest"];
/** Params a clarify option carries for the re-sent question (e.g. the scope guard's `_scope_ack`). */
export type ExplicitParams = NonNullable<QueryRequest["explicit_params"]>;
export type QueryOut = Schemas["QueryOut"];
export type TraceStep = Schemas["TraceStep"];
export type TraceStatus = Schemas["TraceStatus"];
export type RouteSource = Schemas["RouteSource"];
export type RouteDecision = Schemas["RouteDecision"];
export type ClarifyOption = Schemas["ClarifyOption"];
export type Provenance = Schemas["Provenance"];
export type ApprovalRef = Schemas["ApprovalRef"];
export type ApprovalItem = Schemas["ApprovalItem"];
export type ApprovalList = Schemas["ApprovalList"];
export type ApprovalOutcome = Schemas["ApprovalOutcome"];
export type RejectOut = Schemas["RejectOut"];
export type AuditStatus = Schemas["AuditStatus"];
export type AuditEntryOut = Schemas["AuditEntryOut"];
export type AuditDetailOut = Schemas["AuditDetailOut"];
export type AuditPageOut = Schemas["AuditPageOut"];
export type AuditStatsOut = Schemas["AuditStatsOut"];
export type EntityList = Schemas["EntityList"];
export type EntityOut = Schemas["EntityOut"];
export type FieldOut = Schemas["FieldOut"];
export type RecordsPage = Schemas["RecordsPage"];
export type ImportPreviewOut = Schemas["ImportPreviewOut"];
export type ImportCommitRequest = Schemas["ImportCommitRequest"];
export type ImportCommitOut = Schemas["ImportCommitOut"];
export type ImportResultOut = Schemas["ImportResultOut"];
export type PreviewRow = Schemas["PreviewRow"];
export type ColumnMapping = Schemas["ColumnMapping"];
export type CategoryOut = Schemas["CategoryOut"];
export type FieldType = Schemas["FieldType"];
export type Sensitivity = Schemas["Sensitivity"];
export type StatBucket = Schemas["StatBucket"];
export type ErrorEnvelope = Schemas["ErrorEnvelope"];

export type AuditQuery = NonNullable<paths["/api/audit"]["get"]["parameters"]["query"]>;
export type AuditStatsQuery = NonNullable<paths["/api/audit/stats"]["get"]["parameters"]["query"]>;
export type RecordsQuery = NonNullable<
  paths["/api/entities/{entity_id}/records"]["get"]["parameters"]["query"]
>;

/** Where a failure came from: the server answered (`http`), or it never got that far. */
export type ApiErrorKind = "http" | "network" | "timeout" | "stream" | "parse";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly kind: ApiErrorKind;

  /** `cause`: the lower-level error this one translates (a failed fetch or stream read). */
  constructor(init: { status: number; code: string; message: string; kind: ApiErrorKind; cause?: unknown }) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.kind = init.kind;
  }

  /** Lost in transit, so worth another try; a server verdict is not (see `isTransportFailure`). */
  get retryable(): boolean {
    return isTransportFailure(this);
  }
}

/** Codes this client gives an answer stream it could not read to the end (see `streamQuery`). */
export const STREAM_INCOMPLETE = "stream_incomplete";
export const STREAM_UNAVAILABLE = "stream_unavailable";

/**
 * What a gateway in front of the API answers when it could not get an answer from it (bad
 * gateway, unavailable, gateway timeout). A 500 is the API's own verdict and is not here.
 */
const GATEWAY_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

/**
 * The request or its answer was lost in transit: the network, a timeout, a gateway that could not
 * reach the API, or an answer stream that ended early or could not be read. Anything else is a
 * verdict: an HTTP error (a 500 included), an `error` frame from the server, a malformed answer,
 * or a failure of this client's own code.
 */
export function isTransportFailure(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.kind === "network" || error.kind === "timeout") return true;
  if (error.kind === "http") return GATEWAY_STATUSES.has(error.status);
  return error.kind === "stream" && (error.code === STREAM_INCOMPLETE || error.code === STREAM_UNAVAILABLE);
}

/** A failed call in the plain, serializable shape views keep in state and render. */
export interface Failure {
  code: string;
  message: string;
  kind: ApiErrorKind;
  status: number;
  /** Lost in transit (`isTransportFailure`): trying again is the way forward. */
  retryable: boolean;
}

/** Any thrown value → a `Failure`. Something that is not an `ApiError` is this client's own fault. */
export function toFailure(error: unknown): Failure {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message, kind: error.kind, status: error.status, retryable: isTransportFailure(error) };
  }
  return { code: "unexpected", message: "Unexpected error.", kind: "parse", status: 0, retryable: false };
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

/**
 * What a 401 on an authenticated call does. `refused`: the bearer token that call carried (null:
 * it carried none), so the handler can tell the session it holds now from one already over.
 */
export type UnauthorizedHandler = (refused: string | null) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/** Register what a 401 does (the auth provider signs out). Returns an unregister function. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): () => void {
  unauthorizedHandler = handler;
  return () => {
    if (unauthorizedHandler === handler) unauthorizedHandler = null;
  };
}

let requestLocale: Locale = defaultLocale;

/**
 * The console's language, sent as `Accept-Language` on every call: REST endpoints word their
 * labels and messages in it (a query's answer follows the language of the question instead).
 * `<RequestLocale>` keeps it equal to the active locale.
 */
export function setRequestLocale(locale: Locale): void {
  requestLocale = locale;
}

/** The `Accept-Language` value every call sends now (`en`, `zh-CN`). */
export function requestLanguage(): string {
  return htmlLang[requestLocale];
}

type QueryScalar = string | number | boolean | null | undefined;
/** An array repeats the parameter (`?status=a&status=b`), which record filters read as "any of". */
type QueryValue = QueryScalar | readonly (string | number)[];

/** Equality filters on a records listing: field id → accepted values. */
export type RecordFilters = Readonly<Record<string, readonly string[]>>;

export function apiUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  const append = (key: string, value: QueryScalar) => {
    if (value !== undefined && value !== null && value !== "") params.append(key, String(value));
  };
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) for (const item of value) append(key, item as string | number);
    else append(key, value as QueryScalar);
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const error = (value as { error?: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

/**
 * `session`: the call was made on behalf of the session, with the bearer token `sent` (null: none
 * was available), so its 401 is reported to the unauthorized handler.
 */
async function errorFromResponse(res: Response, session: { sent: string | null } | null): Promise<ApiError> {
  let code = `http_${res.status}`;
  let message = res.statusText || `HTTP ${res.status}`;
  try {
    const body: unknown = await res.json();
    if (isEnvelope(body)) {
      code = body.error.code;
      message = body.error.message;
    }
  } catch {
    // not JSON: keep the status line
  }
  if (res.status === 401 && session) unauthorizedHandler?.(session.sent);
  return new ApiError({ status: res.status, code, message, kind: "http" });
}

/** `cause`: what fetch, or the read of a response body, threw. */
function networkError(cause: unknown): ApiError {
  return new ApiError({
    status: 0,
    code: "network_error",
    message: "The server could not be reached.",
    kind: "network",
    cause,
  });
}

interface RequestOptions {
  method?: "GET" | "POST";
  json?: unknown;
  form?: FormData;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  /** Send the session's bearer token and treat 401 as "session over" (default true). */
  auth?: boolean;
  /**
   * Send this bearer token instead, with `auth: false`: a call about a token the session no
   * longer holds (signing it out), whose 401 says nothing about the session.
   */
  token?: string;
}

/** The one place every call's headers are made: its token, and the console's language. */
function headersFor(token: string | null, accept: string, json: boolean): Record<string, string> {
  const headers: Record<string, string> = { Accept: accept, "Accept-Language": requestLanguage() };
  if (json) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const auth = options.auth ?? true;
  const token = auth ? accessToken() : (options.token ?? null);
  const hasJson = options.json !== undefined;
  let res: Response;
  try {
    res = await fetch(apiUrl(path, options.query), {
      method: options.method ?? "GET",
      headers: headersFor(token, "application/json", hasJson),
      body: options.form ?? (hasJson ? JSON.stringify(options.json) : undefined),
      signal: options.signal,
      cache: "no-store",
      credentials: "omit",
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw networkError(error);
  }
  if (!res.ok) throw await errorFromResponse(res, auth ? { sent: token } : null);
  if (res.status === 204) return undefined as T;
  // read, then parse: each step fails in its own way, and only the second is the server's answer
  let text: string;
  try {
    text = await res.text();
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw networkError(error); // the connection dropped while the answer was read
  }
  try {
    return JSON.parse(text) as T;
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new ApiError({
      status: res.status,
      code: "invalid_response",
      message: "The server sent a response that is not JSON.",
      kind: "parse",
      cause: error,
    });
  }
}

function parseFrame<T>(data: string, check: (value: unknown) => boolean): T {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    value = undefined;
  }
  if (!check(value)) {
    throw new ApiError({
      status: 200,
      code: "invalid_frame",
      message: "The answer stream contained a malformed frame.",
      kind: "parse",
    });
  }
  return value as T;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const isTraceStep = (v: unknown) =>
  isObject(v) && typeof v.step === "string" && typeof v.status === "string";
const isQueryOut = (v: unknown) =>
  isObject(v) && typeof v.conversation_id === "string" && typeof v.query === "string";

export interface StreamQueryOptions {
  /** Called for every `trace` frame, as it arrives. */
  onTrace?: (step: TraceStep) => void;
  signal?: AbortSignal;
  idleTimeoutMs?: number;
}

/**
 * POST /api/query and follow its event stream: `trace` frames go to `onTrace`, the `response`
 * frame resolves the promise, an `error` frame rejects it with that envelope. Aborting
 * `signal` rejects with an AbortError (check with `isAbortError`).
 */
export async function streamQuery(
  body: QueryRequest,
  { onTrace, signal, idleTimeoutMs = STREAM_IDLE_TIMEOUT_MS }: StreamQueryOptions = {},
): Promise<QueryOut> {
  const token = accessToken();
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/query"), {
      method: "POST",
      headers: headersFor(token, "text/event-stream", true),
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
      credentials: "omit",
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw networkError(error);
  }
  if (!res.ok) throw await errorFromResponse(res, { sent: token });

  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("text/event-stream")) {
    // Something between us and the server buffered the stream into one JSON answer.
    let text: string;
    try {
      text = await res.text();
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw networkError(error); // the connection dropped while the answer was read
    }
    return parseFrame<QueryOut>(text, isQueryOut);
  }
  if (!res.body) {
    throw new ApiError({
      status: res.status,
      code: STREAM_UNAVAILABLE,
      message: "The browser could not read the answer stream.",
      kind: "stream",
    });
  }

  // a holder object, so the assignments made inside the callback stay visible to the checker
  const outcome: { answer: QueryOut | null; failure: ApiError | null } = {
    answer: null,
    failure: null,
  };
  try {
    await readSseStream(
      res.body,
      (message) => {
        if (message.event === "trace") {
          const step = parseFrame<TraceStep>(message.data, isTraceStep); // validate even unheard
          onTrace?.(step);
        } else if (message.event === "response") {
          outcome.answer = parseFrame<QueryOut>(message.data, isQueryOut);
          return "stop";
        } else if (message.event === "error") {
          const envelope = parseFrame<ErrorEnvelope>(message.data, isEnvelope);
          outcome.failure = new ApiError({
            status: res.status,
            code: envelope.error.code,
            message: envelope.error.message,
            kind: "stream",
          });
          return "stop";
        }
        return undefined;
      },
      { signal, idleTimeoutMs },
    );
  } catch (error) {
    // Only the reader's own failures are translated. Anything else (an abort, a malformed
    // frame, whatever `onTrace` threw) is passed on as it is: none of it is the network's fault.
    if (error instanceof SseReadError) throw networkError(error.cause); // the connection dropped mid-stream
    if (error instanceof SseIdleTimeoutError) {
      throw new ApiError({
        status: 0,
        code: "stream_timeout",
        message: "The server stopped responding.",
        kind: "timeout",
        cause: error,
      });
    }
    throw error;
  }
  if (outcome.failure) throw outcome.failure;
  if (!outcome.answer) {
    throw new ApiError({
      status: res.status,
      code: STREAM_INCOMPLETE,
      message: "The answer stream ended before the answer arrived.",
      kind: "stream",
    });
  }
  return outcome.answer;
}

const enc = encodeURIComponent;

export const api = {
  health: (signal?: AbortSignal) => request<HealthOut>("/api/health", { auth: false, signal }),
  login: (username: string, password: string) =>
    request<TokenOut>("/api/auth/login", {
      method: "POST",
      json: { username, password },
      auth: false,
    }),
  demoLogin: (role: Role) =>
    request<TokenOut>("/api/auth/demo", { method: "POST", json: { role }, auth: false }),
  me: (signal?: AbortSignal) => request<MeOut>("/api/auth/me", { signal }),
  /**
   * Revoke `token` on the server (sign-out): it is refused from then on (`401 token_revoked`).
   * The session is cleared without waiting for this, so its 401 (already revoked) means nothing.
   */
  logout: (token: string) => request<LogoutOut>("/api/auth/logout", { method: "POST", auth: false, token }),

  streamQuery,
  /** The same query as one JSON answer (no live trace). */
  query: (body: QueryRequest, signal?: AbortSignal) =>
    request<QueryOut>("/api/query", { method: "POST", json: body, query: { stream: false }, signal }),

  listApprovals: (signal?: AbortSignal) => request<ApprovalList>("/api/approvals", { signal }),
  approve: (grantId: string) =>
    request<ApprovalOutcome>(`/api/approvals/${enc(grantId)}/approve`, { method: "POST" }),
  reject: (grantId: string) =>
    request<RejectOut>(`/api/approvals/${enc(grantId)}/reject`, { method: "POST" }),

  listAudit: (query: AuditQuery = {}, signal?: AbortSignal) =>
    request<AuditPageOut>("/api/audit", { query, signal }),
  auditStats: (query: AuditStatsQuery = {}, signal?: AbortSignal) =>
    request<AuditStatsOut>("/api/audit/stats", { query, signal }),
  getAudit: (entryId: number, signal?: AbortSignal) =>
    request<AuditDetailOut>(`/api/audit/${enc(String(entryId))}`, { signal }),

  listEntities: (signal?: AbortSignal) => request<EntityList>("/api/entities", { signal }),
  /**
   * A page of records. `filters` become one query parameter per field id (the API reads every
   * non-reserved parameter as an equality filter); `sort` / `page` / `page_size` always win.
   */
  listRecords: (
    entityId: string,
    query: RecordsQuery = {},
    filters: RecordFilters = {},
    signal?: AbortSignal,
  ) =>
    request<RecordsPage>(`/api/entities/${enc(entityId)}/records`, {
      query: { ...filters, ...query },
      signal,
    }),
  getRecord: (entityId: string, pk: string, signal?: AbortSignal) =>
    request<Record<string, unknown>>(`/api/entities/${enc(entityId)}/records/${enc(pk)}`, {
      signal,
    }),
  previewImport: (entityId: string, file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImportPreviewOut>(`/api/entities/${enc(entityId)}/import/preview`, {
      method: "POST",
      form,
      signal,
    });
  },
  commitImport: (entityId: string, body: ImportCommitRequest) =>
    request<ImportCommitOut>(`/api/entities/${enc(entityId)}/import/commit`, {
      method: "POST",
      json: body,
    }),
};
