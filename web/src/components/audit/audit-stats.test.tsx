import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AuditStatsOut } from "@/lib/api";

import { renderWithIntl } from "../../../test/render";
import { AuditStats } from "./audit-stats";

// the SVG chart is decorative for assistive tech; its numbers are asserted through the table view
vi.mock("./route-share-chart", () => ({
  RouteShareChart: ({ rows }: { rows: { key: string }[] }) => (
    <div data-testid="route-share-chart">{rows.map((r) => r.key).join(",")}</div>
  ),
}));

const stats = {
  since: "2026-10-19T08:00:00Z",
  until: null,
  total: 10,
  by_route_source: { rule: 5, llm: 2, teleport: 2, unknown: 1 },
  by_status: { ok: 6, quarantined: 2, needs_confirmation: 1, chat: 1 },
  buckets: [
    { route_source: "rule", status: "ok", count: 4 },
    { route_source: "rule", status: "needs_confirmation", count: 1 },
    { route_source: "llm", status: "ok", count: 2 },
    { route_source: "teleport", status: "quarantined", count: 2 },
    { route_source: null, status: "chat", count: 1 },
  ],
} as unknown as AuditStatsOut; // "teleport" / "quarantined" are values this build has never seen

function renderStats(over: Partial<Parameters<typeof AuditStats>[0]> = {}) {
  const props = {
    stats,
    loading: false,
    error: null,
    statsWindow: "7d" as const,
    onWindowChange: vi.fn(),
    onRetry: vi.fn(),
    selectedSource: null,
    selectedStatus: null,
    onSelectSource: vi.fn(),
    onSelectStatus: vi.fn(),
    ...over,
  };
  renderWithIntl(<AuditStats {...props} />);
  return props;
}

describe("AuditStats", () => {
  it("shows the total once, every route source in share order, and the window choice", () => {
    renderStats();
    expect(screen.getByText(/10 decisions/)).toBeInTheDocument();
    expect(screen.getByTestId("route-share-chart")).toHaveTextContent("rule,llm,teleport,unknown");
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps numbers outside the chart: a table with counts, shares and outcomes per source", async () => {
    const user = userEvent.setup();
    const props = renderStats();
    await user.click(screen.getByText("Show as table"));
    const table = screen.getByRole("table", { name: "Route sources" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["5", "50%", "OK 4 · Needs approval 1"],
      ["2", "20%", "OK 2"],
      ["2", "20%", "quarantined 2"],
      ["1", "10%", "Chat 1"],
    ]);
    // unknown route sources are listed verbatim and still filter the log
    await user.click(within(table).getByRole("button", { name: "Filter the log by teleport" }));
    expect(props.onSelectSource).toHaveBeenCalledWith("teleport");
  });

  it("lists outcomes with tone, count and share; an unknown status renders neutral and verbatim", async () => {
    const user = userEvent.setup();
    const props = renderStats({ selectedStatus: "ok" });
    const outcomes = screen.getByRole("region", { name: "Outcomes" });
    const buttons = within(outcomes).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["OK660%", "quarantined220%", "Chat110%", "Needs approval110%"]);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    const unknown = buttons[1]!;
    expect(unknown).toHaveAttribute("data-status", "quarantined");
    expect(unknown.querySelector(".bg-muted-foreground")).not.toBeNull(); // neutral dot
    await user.click(unknown);
    expect(props.onSelectStatus).toHaveBeenCalledWith("quarantined");
  });

  it("switches the window", async () => {
    const user = userEvent.setup();
    const props = renderStats();
    await user.click(screen.getByRole("button", { name: "24 h" }));
    expect(props.onWindowChange).toHaveBeenCalledWith("24h");
  });

  it("says when the window is empty, and offers a retry on failure", async () => {
    const user = userEvent.setup();
    renderStats({ stats: { ...stats, total: 0, by_route_source: {}, by_status: {}, buckets: [] } });
    expect(screen.getByText("No decisions in this window")).toBeInTheDocument();
    const props = renderStats({ stats: null, error: new Error("boom") });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(props.onRetry).toHaveBeenCalled();
  });
});
