import type { components } from "./api-types";

type Role = components["schemas"]["Role"];

/**
 * The signed-in session. The bearer token lives in memory and, so a reload keeps the tab
 * signed in, in `sessionStorage` (tab-scoped, gone when the tab closes). Never in
 * `localStorage`, never in a cookie, never logged.
 */
export interface Session {
  token: string;
  expiresAt: string;
  userId: string;
  roles: Role[];
  demoMode: boolean;
}

const STORAGE_KEY = "twotrack.session";
const ROLES: readonly Role[] = ["admin", "operator", "viewer"];

let current: Session | null = null;
let loaded = false;
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null; // storage disabled (privacy mode, sandboxed frame)
  }
}

function isSession(value: unknown): value is Session {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.token === "string" &&
    v.token.length > 0 &&
    typeof v.expiresAt === "string" &&
    !Number.isNaN(Date.parse(v.expiresAt)) &&
    typeof v.userId === "string" &&
    Array.isArray(v.roles) &&
    v.roles.every((r) => ROLES.includes(r as Role)) &&
    typeof v.demoMode === "boolean"
  );
}

function load(): Session | null {
  try {
    const raw = storage()?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isSession(parsed) || isExpired(parsed)) {
      storage()?.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** The stored session as-is (stable reference until it changes). Safe as a store snapshot. */
export function peekSession(): Session | null {
  if (!loaded && typeof window !== "undefined") {
    loaded = true;
    current = load();
  }
  return current;
}

export function isExpired(session: Session, now: number = Date.now()): boolean {
  return Date.parse(session.expiresAt) <= now;
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function writeSession(session: Session): void {
  current = session;
  loaded = true;
  try {
    storage()?.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // memory still holds it; the tab just will not survive a reload
  }
  notify();
}

export function clearSession(): void {
  const had = current !== null;
  current = null;
  loaded = true;
  try {
    storage()?.removeItem(STORAGE_KEY);
  } catch {
    // nothing stored
  }
  if (had) notify();
}

/** Bearer token for API calls; null when signed out or expired. */
export function accessToken(now: number = Date.now()): string | null {
  const session = peekSession();
  return session && !isExpired(session, now) ? session.token : null;
}

/** `admin` passes every role check, as on the server. */
export function hasRole(roles: readonly Role[] | undefined, ...allowed: Role[]): boolean {
  if (!roles) return false;
  return roles.includes("admin") || roles.some((r) => allowed.includes(r));
}
