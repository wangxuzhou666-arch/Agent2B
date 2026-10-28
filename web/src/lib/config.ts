/** Where the twotrack API lives. Inlined at build time; must match the CSP `connect-src`. */
export const API_BASE = normalizeApiBase(process.env.NEXT_PUBLIC_API_BASE);

export function normalizeApiBase(raw: string | undefined): string {
  const value = (raw ?? "").trim();
  return (value === "" ? "http://localhost:8000" : value).replace(/\/+$/, "");
}

/** Abort a query stream when nothing (not even a heartbeat) arrived for this long. */
export const STREAM_IDLE_TIMEOUT_MS = 45_000;

/** How often the sidebar refreshes the pending-approvals count. */
export const APPROVALS_POLL_MS = 30_000;

/**
 * Upload size the browser checks before sending. It mirrors the server's default
 * (`TWOTRACK_MAX_UPLOAD_BYTES`, 2 MiB) so an obviously oversized file fails fast; the server
 * stays the authority and its 413 is mapped to the same message.
 */
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

/** Extensions the file picker offers; the server detects the real type from the bytes. */
export const UPLOAD_EXTENSIONS = [".csv", ".xlsx"] as const;

export const AUDIT_PAGE_SIZE = 25;
export const RECORDS_PAGE_SIZE = 50;
