import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "../../../test/render";
import { UserMenu } from "./user-menu";

const auth = vi.hoisted(() => ({
  session: { userId: "demo-operator", roles: ["operator"] as string[] },
  signOut: vi.fn(),
}));
vi.mock("@/components/providers/auth-provider", () => ({ useAuth: () => auth }));

beforeEach(() => {
  auth.session = { userId: "demo-operator", roles: ["operator"] };
});

describe("UserMenu", () => {
  it("names the account button by the user and role it shows, then 'Account'", () => {
    renderWithIntl(<UserMenu />);
    const button = screen.getByRole("button", { name: "demo-operator Operator Account" });
    // the visible label is part of the accessible name (WCAG 2.5.3 Label in Name)
    expect(button).toHaveTextContent("demo-operator");
    expect(button).toHaveTextContent("Operator");
  });

  it("is named in the console's language, and without a role when there is none", () => {
    const view = renderWithIntl(<UserMenu />, { locale: "zh" });
    expect(screen.getByRole("button", { name: "demo-operator 操作员 账户" })).toBeInTheDocument();
    view.unmount();

    auth.session = { userId: "auditor-7", roles: [] };
    renderWithIntl(<UserMenu />);
    expect(screen.getByRole("button", { name: "auditor-7 Account" })).toBeInTheDocument();
  });
});
