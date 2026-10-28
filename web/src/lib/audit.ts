/**
 * Audit-log helpers: stats windows, share rows for the route-source chart, masked parameter
 * values and the request chain. Pure functions over API payloads; every number shown on the
 * Audit window is a count the server computed, these only order and divide them.
 */
import { ApiError, type AuditEntryOut, type AuditQuery, type AuditStatus, type RouteSource, type StatBucket } from "./api";

/** Statuses the contract names today; filters also offer any other value the stats report. */
export const AUDIT_STATUSES = [
  "ok",
  "needs_confirmation",
  "started",
  "clarify",
  "chat",
  "blocked",
  "invalid",
  "error",
] as const satisfies readonly AuditStatus[];

/** Route sources the contract names today; unknown ones still render (verbatim) and filter. */
export const ROUTE_SOURCES = [
  "rule",
  "explicit",
  "llm",
  "llm_confirm",
  "semantic",
  "agent",
  "mcp",
  "fallback",
] as const satisfies readonly RouteSource[];

/** Known values first (in their order), then any extra ones seen in the data, sorted. */
export function withExtras(known: readonly string[], seen: Iterable<string>): string[] {
  const extras = [...new Set(seen)].filter((v) => v && !known.includes(v)).sort();
  return [...known, ...extras];
}

export const STATS_WINDOWS = ["24h", "7d", "30d"] as const;
export type StatsWindow = (typeof STATS_WINDOWS)[number];
export const DEFAULT_STATS_WINDOW: StatsWindow = "7d";

const HOUR_MS = 3_600_000;
const WINDOW_MS: Record<StatsWindow, number> = {
  "24h": 24 * HOUR_MS,
  "7d": 7 * 24 * HOUR_MS,
  "30d": 30 * 24 * HOUR_MS,
};

export function isStatsWindow(value: unknown): value is StatsWindow {
  return typeof value === "string" && (STATS_WINDOWS as readonly string[]).includes(value);
}

/** Start of a trailing window as an ISO timestamp (the API reads it as UTC). */
export function windowSince(preset: StatsWindow, now: number = Date.now()): string {
  return new Date(now - WINDOW_MS[preset]).toISOString();
}

export interface ShareRow {
  key: string;
  count: number;
  /** count / total, 0 when the total is 0. */
  share: number;
}

/**
 * Counts → rows ordered by count (largest first, ties by name), each with its share of the
 * total. Keys are kept verbatim, so a route source or status this build has never heard of is
 * a row like any other.
 */
export function shareRows(counts: Readonly<Record<string, number>>): ShareRow[] {
  const entries = Object.entries(counts).filter(([, n]) => Number.isFinite(n) && n > 0);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  return entries
    .map(([key, count]) => ({ key, count, share: total > 0 ? count / total : 0 }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}

/** Server key for records without a route source (approval-side decisions, for instance). */
export const UNKNOWN_ROUTE_SOURCE = "unknown";

/** The outcomes behind one route source's bar, from the route-source × status buckets. */
export function statusesFor(buckets: readonly StatBucket[], routeSource: string): ShareRow[] {
  const counts: Record<string, number> = {};
  for (const bucket of buckets) {
    const source = bucket.route_source ?? UNKNOWN_ROUTE_SOURCE;
    if (source !== routeSource) continue;
    counts[bucket.status] = (counts[bucket.status] ?? 0) + bucket.count;
  }
  return shareRows(counts);
}

/** Share as a whole percent, keeping a visible "<1%" for small non-zero shares. */
export function formatShare(share: number, locale: string): string {
  if (share > 0 && share < 0.005) return `<${new Intl.NumberFormat(locale, { style: "percent" }).format(0.01)}`;
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(share);
}

/** What the audit sink stores in place of a restricted field's value. */
export const MASKED_VALUE = "***";

export function isMasked(value: unknown): boolean {
  return value === MASKED_VALUE;
}

/** First characters of a long id, for dense table cells (the full id stays in the title). */
export function shortId(id: string | null | undefined, length = 8): string | null {
  if (!id) return null;
  return id.length > length ? id.slice(0, length) : id;
}

/**
 * The listings that find the other records of an entry's request: everything sharing its
 * `request_id` (a query's routing, confirmation and execution), and everything sharing its
 * `grant_id` (an import's preview and commit carry the grant but no request id).
 */
export function chainQueries(entry: Pick<AuditEntryOut, "request_id" | "grant_id">): AuditQuery[] {
  const queries: AuditQuery[] = [];
  if (entry.request_id) queries.push({ request_id: entry.request_id, page_size: 200 });
  if (entry.grant_id) queries.push({ grant_id: entry.grant_id, page_size: 200 });
  return queries;
}

/** Dedupe by id and order oldest first (time, then id: records of one request share a clock). */
export function mergeChain<T extends Pick<AuditEntryOut, "id" | "ts">>(entries: readonly T[]): T[] {
  const byId = new Map<number, T>();
  for (const entry of entries) if (!byId.has(entry.id)) byId.set(entry.id, entry);
  return [...byId.values()].sort((a, b) => {
    const ta = Date.parse(a.ts);
    const tb = Date.parse(b.ts);
    if (Number.isFinite(ta) && Number.isFinite(tb) && ta !== tb) return ta - tb;
    return a.id - b.id;
  });
}

/** The parent is gone (404) or this caller may not read it (403): the chain can do without it. */
const isUnreadable = (error: unknown) =>
  error instanceof ApiError && (error.status === 403 || error.status === 404);

/**
 * Every record of the request an entry belongs to, oldest first: its request-id and grant-id
 * siblings, plus its parent when that parent was outside both listings and is still readable.
 * Any other failure (network, server, abort) rejects, so the caller can say so and retry.
 */
export async function loadRequestChain(
  entry: AuditEntryOut,
  fetchers: {
    list: (query: AuditQuery, signal?: AbortSignal) => Promise<{ items: AuditEntryOut[] }>;
    get: (id: number, signal?: AbortSignal) => Promise<AuditEntryOut>;
  },
  signal?: AbortSignal,
): Promise<AuditEntryOut[]> {
  const pages = await Promise.all(chainQueries(entry).map((query) => fetchers.list(query, signal)));
  const entries: AuditEntryOut[] = [entry, ...pages.flatMap((page) => page.items)];
  const parentId = entry.parent_id;
  if (parentId !== null && parentId !== undefined && !entries.some((e) => e.id === parentId)) {
    try {
      entries.push(await fetchers.get(parentId, signal));
    } catch (error) {
      if (signal?.aborted || !isUnreadable(error)) throw error;
      // the chain shows what is left
    }
  }
  return mergeChain(entries);
}

/** Filters the audit table understands, in URL order. */
export const AUDIT_FILTER_KEYS = ["user", "target", "status", "route_source", "since", "request_id", "grant_id"] as const;
export type AuditFilterKey = (typeof AUDIT_FILTER_KEYS)[number];
export type AuditFilters = Partial<Record<AuditFilterKey, string>>;

/** `since` in the table is a preset, not a free timestamp: it is resolved when the query runs. */
export function auditQueryFor(filters: AuditFilters, page: number, pageSize: number, now = Date.now()): AuditQuery {
  const since = filters.since && isStatsWindow(filters.since) ? windowSince(filters.since, now) : undefined;
  return {
    user: filters.user || undefined,
    target: filters.target || undefined,
    status: (filters.status || undefined) as AuditQuery["status"],
    route_source: filters.route_source || undefined,
    since,
    request_id: filters.request_id || undefined,
    grant_id: filters.grant_id || undefined,
    page,
    page_size: pageSize,
  };
}

export function readAuditFilters(params: URLSearchParams): AuditFilters {
  const filters: AuditFilters = {};
  for (const key of AUDIT_FILTER_KEYS) {
    const value = params.get(key)?.trim();
    if (value) filters[key] = value;
  }
  if (filters.since && !isStatsWindow(filters.since)) delete filters.since;
  return filters;
}

export function activeFilterCount(filters: AuditFilters): number {
  return AUDIT_FILTER_KEYS.filter((key) => Boolean(filters[key])).length;
}
