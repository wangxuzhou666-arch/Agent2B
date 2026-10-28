import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AUDIT_STATUSES, ROUTE_SOURCES } from "@/lib/audit";

import { renderWithIntl } from "../../../test/render";
import { AuditFilterBar } from "./audit-filters";

afterEach(() => vi.useRealTimers());

const bar = (filters: Record<string, string>, onChange = vi.fn()) => (
  <AuditFilterBar
    filters={filters}
    onChange={onChange}
    statusOptions={[...AUDIT_STATUSES, "quarantined"]}
    sourceOptions={[...ROUTE_SOURCES, "teleport"]}
  />
);

describe("AuditFilterBar", () => {
  it("commits a text filter after a pause, keeps typing intact when the URL catches up, and follows outside resets", async () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const view = renderWithIntl(bar({}, onChange));
    const user = screen.getByRole("textbox", { name: "User" });

    fireEvent.change(user, { target: { value: "ops" } });
    expect(onChange).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(onChange).toHaveBeenLastCalledWith({ user: "ops" });

    // the user keeps typing while the committed value comes back as a prop
    fireEvent.change(user, { target: { value: "ops-2" } });
    view.rerender(bar({ user: "ops" }, onChange));
    expect(user).toHaveValue("ops-2");
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(onChange).toHaveBeenLastCalledWith({ user: "ops-2" });

    // a chip removed elsewhere clears the box
    view.rerender(bar({ user: "ops-2" }, onChange));
    view.rerender(bar({}, onChange));
    expect(user).toHaveValue("");
  });

  it("applies selects at once and offers values this build does not know", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithIntl(bar({ user: "ops" }, onChange));
    const outcome = screen.getByRole("combobox", { name: "Outcome" });
    expect([...outcome.querySelectorAll("option")].map((o) => o.textContent)).toContain("quarantined");
    await user.selectOptions(outcome, "needs_confirmation");
    expect(onChange).toHaveBeenLastCalledWith({ user: "ops", status: "needs_confirmation" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Route source" }), "teleport");
    expect(onChange).toHaveBeenLastCalledWith({ user: "ops", route_source: "teleport" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Time" }), "24h");
    expect(onChange).toHaveBeenLastCalledWith({ user: "ops", since: "24h" });
  });

  it("shows each active filter as a chip with readable values", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithIntl(bar({ status: "needs_confirmation", since: "7d", request_id: "r-1" }, onChange));
    expect(screen.getByRole("button", { name: "Remove filter Outcome Needs approval" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove filter Time Last 7 days" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove filter Request ID r-1" }));
    expect(onChange).toHaveBeenLastCalledWith({ status: "needs_confirmation", since: "7d" });
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onChange).toHaveBeenLastCalledWith({});
  });
});
