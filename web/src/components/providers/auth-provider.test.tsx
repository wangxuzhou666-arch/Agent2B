import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type MeOut, type UnauthorizedHandler } from "@/lib/api";
import { clearSession, peekSession, writeSession, type Session } from "@/lib/session";

import { IntlProviders } from "../../../test/render";
import { AuthProvider, useAuth } from "./auth-provider";

const apiMock = vi.hoisted(() => ({ me: vi.fn(), logout: vi.fn() }));
/** What the provider registered for a 401, so a test can play one. */
const registered = vi.hoisted(() => ({ handler: null as UnauthorizedHandler | null }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, ...apiMock },
    setUnauthorizedHandler: (handler: UnauthorizedHandler) => {
      registered.handler = handler;
      return () => {
        if (registered.handler === handler) registered.handler = null;
      };
    },
  };
});
vi.mock("sonner", () => ({ toast: { info: vi.fn() } }));

const session = (token: string): Session => ({
  token,
  expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
  userId: "demo-operator",
  roles: ["operator"],
  demoMode: true,
});
const me = (expiresAt: string): MeOut => ({ user_id: "demo-operator", roles: ["operator"], demo_mode: true, expires_at: expiresAt });

const wrapper = ({ children }: { children: ReactNode }) => (
  <IntlProviders locale="en">
    <AuthProvider>{children}</AuthProvider>
  </IntlProviders>
);

/** Signed in with `token`, checked against /me. */
async function signedIn(token: string) {
  const stored = session(token);
  writeSession(stored);
  apiMock.me.mockResolvedValue(me(stored.expiresAt));
  const view = renderHook(() => useAuth(), { wrapper });
  await waitFor(() => expect(view.result.current.status).toBe("signed-in"));
  return view;
}

beforeEach(() => {
  clearSession();
  apiMock.me.mockReset();
  apiMock.logout.mockReset();
  vi.mocked(toast.info).mockReset();
});

describe("AuthProvider sign-out", () => {
  it("signs out at once and asks the server to revoke the token on the side, without waiting for it", async () => {
    apiMock.logout.mockReturnValue(new Promise(() => undefined)); // the server never answers
    const { result } = await signedIn("token-a");

    act(() => result.current.signOut());
    expect(result.current.status).toBe("signed-out");
    expect(result.current.session).toBeNull();
    expect(peekSession()).toBeNull();
    expect(apiMock.logout).toHaveBeenCalledExactlyOnceWith("token-a");
    expect(toast.info).not.toHaveBeenCalled(); // a sign-out the user asked for is no "session ended"
  });

  it.each([
    ["a 401 for a token that is already dead", new ApiError({ status: 401, code: "token_revoked", message: "Signed out.", kind: "http" })],
    ["a network error", new ApiError({ status: 0, code: "network_error", message: "Unreachable.", kind: "network" })],
  ])("stays signed out whatever the revoke answers: %s", async (_case, failure) => {
    apiMock.logout.mockRejectedValue(failure);
    const { result } = await signedIn("token-a");

    await act(async () => result.current.signOut());
    expect(result.current.status).toBe("signed-out");
    expect(peekSession()).toBeNull();
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("ignores a 401 for a token this tab no longer holds, and ends the session on one for its own", async () => {
    const { result } = await signedIn("token-b");

    // still in flight from before a sign-out (its token revoked) or a sign-in since: says nothing now
    act(() => registered.handler?.("token-a"));
    expect(result.current.status).toBe("signed-in");
    expect(toast.info).not.toHaveBeenCalled();

    act(() => registered.handler?.("token-b"));
    expect(result.current.status).toBe("signed-out");
    expect(toast.info).toHaveBeenCalledTimes(1);

    // a straggler after that: nothing left to end, nothing more to say
    act(() => registered.handler?.("token-b"));
    act(() => registered.handler?.(null));
    expect(toast.info).toHaveBeenCalledTimes(1);
  });
});
