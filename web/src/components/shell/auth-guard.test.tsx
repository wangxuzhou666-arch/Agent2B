import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "../../../test/render";
import { AuthGuard } from "./auth-guard";
import { safeNext } from "./login-view";

const nav = vi.hoisted(() => ({ pathname: "/hub", replace: vi.fn() }));
const auth = vi.hoisted(() => ({ status: "signed-out" as string, revalidate: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => nav.pathname,
}));
vi.mock("@/components/providers/auth-provider", () => ({ useAuth: () => auth }));

beforeEach(() => {
  nav.replace.mockReset();
  auth.status = "signed-out";
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("AuthGuard", () => {
  it("sends a signed-out deep link to /login with its query, which sign-in follows back", () => {
    window.history.replaceState(null, "", "/audit?id=42&request_id=r1#trace");
    nav.pathname = "/audit";
    renderWithIntl(
      <AuthGuard>
        <p>console</p>
      </AuthGuard>,
    );
    expect(screen.queryByText("console")).not.toBeInTheDocument();
    expect(nav.replace).toHaveBeenCalledTimes(1);
    const target = new URL(nav.replace.mock.calls[0]![0] as string, window.location.origin);
    expect(target.pathname).toBe("/login");
    expect(target.hash).toBe("");
    const next = target.searchParams.get("next");
    expect(next).toBe("/audit?id=42&request_id=r1"); // the hash stays behind (see AuthGuard)
    expect(safeNext(next, window.location.origin)).toBe("/audit?id=42&request_id=r1");
  });

  it("renders the console for a live session", () => {
    auth.status = "signed-in";
    renderWithIntl(
      <AuthGuard>
        <p>console</p>
      </AuthGuard>,
    );
    expect(screen.getByText("console")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
