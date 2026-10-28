import { afterEach, describe, expect, it, vi } from "vitest";

import { accessToken, clearSession, hasRole, peekSession, subscribeSession, writeSession, type Session } from "./session";

const future = () => new Date(Date.now() + 60_000).toISOString();
const session = (over: Partial<Session> = {}): Session => ({
  token: "t1",
  expiresAt: future(),
  userId: "viewer",
  roles: ["viewer"],
  demoMode: true,
  ...over,
});

afterEach(() => clearSession());

describe("session store", () => {
  it("keeps the token in memory and sessionStorage, never localStorage", () => {
    const local = vi.spyOn(Storage.prototype, "setItem");
    writeSession(session());
    expect(accessToken()).toBe("t1");
    expect(JSON.parse(window.sessionStorage.getItem("twotrack.session") ?? "{}")).toMatchObject({ token: "t1" });
    expect(window.localStorage.length).toBe(0);
    expect(local.mock.contexts.every((ctx) => ctx === window.sessionStorage)).toBe(true);
  });

  it("returns no token once the session has expired", () => {
    writeSession(session({ expiresAt: new Date(Date.now() - 1000).toISOString() }));
    expect(accessToken()).toBeNull();
  });

  it("notifies subscribers on write and clear", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSession(listener);
    writeSession(session());
    clearSession();
    expect(listener).toHaveBeenCalledTimes(2);
    expect(peekSession()).toBeNull();
    expect(window.sessionStorage.getItem("twotrack.session")).toBeNull();
    unsubscribe();
  });

  it("treats admin as passing every role check", () => {
    expect(hasRole(["admin"], "operator")).toBe(true);
    expect(hasRole(["viewer"], "operator")).toBe(false);
    expect(hasRole(["operator"], "operator")).toBe(true);
    expect(hasRole(undefined, "viewer")).toBe(false);
  });
});
