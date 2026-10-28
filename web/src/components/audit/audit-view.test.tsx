import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AuditDetailOut, type AuditPageOut, type AuditStatsOut } from "@/lib/api";

import { deferred } from "../../../test/deferred";
import { auditDetail, auditEntry } from "../../../test/fixtures";
import { renderWithIntl } from "../../../test/render";
import { AuditView } from "./audit-view";

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => "/audit",
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock("./route-share-chart", () => ({ RouteShareChart: () => <div data-testid="chart" /> }));

const apiMock = vi.hoisted(() => ({ auditStats: vi.fn(), listAudit: vi.fn(), getAudit: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

const STATS: AuditStatsOut = {
  since: "2026-10-19T08:00:00Z",
  until: null,
  total: 3,
  by_route_source: { rule: 2, llm: 1 },
  by_status: { ok: 2, clarify: 1 },
  buckets: [],
};
const PAGE: AuditPageOut = {
  items: [
    auditEntry({ id: 7, status: "ok", request_id: "0123456789abcdef", row_count: 12, target: "late_orders", effect: "read" }),
    auditEntry({ id: 6, status: "clarify", route_source: "llm", target: null }),
  ],
  total: 40,
  page: 2,
  page_size: 25,
};

beforeEach(() => {
  nav.replace.mockReset();
  for (const fn of Object.values(apiMock)) fn.mockReset();
  apiMock.auditStats.mockResolvedValue(STATS);
  apiMock.listAudit.mockResolvedValue(PAGE);
  apiMock.getAudit.mockResolvedValue(auditDetail({ id: 7 }));
});
afterEach(() => vi.useRealTimers());

describe("AuditView", () => {
  it("reads filters, page and the open entry from the URL", async () => {
    // nothing here waits on a clock: each answer is handed over, then the screen is read once
    vi.useFakeTimers();
    const stats = deferred<AuditStatsOut>();
    const list = deferred<AuditPageOut>();
    const detail = deferred<AuditDetailOut>();
    apiMock.auditStats.mockReturnValue(stats.promise);
    apiMock.listAudit.mockReturnValue(list.promise);
    apiMock.getAudit.mockReturnValue(detail.promise);
    nav.search = "status=ok&route_source=llm&user=ops&page=2&id=7";
    renderWithIntl(<AuditView />);

    // the URL's filters, page and entry are what the requests ask for
    expect(apiMock.listAudit).toHaveBeenCalledWith(
      expect.objectContaining({ status: "ok", route_source: "llm", user: "ops", page: 2, page_size: 25 }),
      expect.anything(),
    );
    expect(apiMock.auditStats).toHaveBeenCalledWith({ since: expect.any(String) }, expect.anything());
    expect(apiMock.getAudit).toHaveBeenCalledWith(7, expect.anything());

    await act(async () => {
      stats.resolve(STATS);
      list.resolve(PAGE);
      detail.resolve(auditDetail({ id: 7 }));
    });
    // the open entry's sheet is modal: the log behind it is hidden from assistive tech
    expect(screen.getByRole("dialog", { name: "Entry #7" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /, open entry #7$/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /, open entry #7$/, hidden: true })).toBeInTheDocument();
    expect(screen.getByText("26–40 of 40")).toBeInTheDocument();
  });

  it("renders the log row by row, with short request ids that narrow the log", async () => {
    const user = userEvent.setup();
    nav.search = "";
    renderWithIntl(<AuditView />);
    const row = (await screen.findByRole("button", { name: /, open entry #7$/ })).closest("tr")!;
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(1)).toEqual(["demo-operator", "late_orders", "rule (Keyword rule arm)", "OK", "12", "01234567", "#7"]);
    await user.click(within(row).getByRole("button", { name: "Show every record of request 0123456789abcdef" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/audit?request_id=0123456789abcdef", { scroll: false });
  });

  it("toggles an outcome filter from the stats and opens an entry", async () => {
    const user = userEvent.setup();
    nav.search = "status=clarify";
    renderWithIntl(<AuditView />);
    const outcomes = await screen.findByRole("region", { name: "Outcomes" });
    await user.click(within(outcomes).getByRole("button", { name: /Clarify/ }));
    expect(nav.replace).toHaveBeenLastCalledWith("/audit", { scroll: false }); // selected again → cleared
    await user.click(within(outcomes).getByRole("button", { name: /OK/ }));
    expect(nav.replace).toHaveBeenLastCalledWith("/audit?status=ok", { scroll: false });
    await user.click(screen.getByRole("button", { name: /, open entry #6$/ }));
    expect(nav.replace).toHaveBeenLastCalledWith("/audit?status=clarify&id=6", { scroll: false });
  });

  it("names the time button by its visible time first (label in name)", async () => {
    nav.search = "";
    renderWithIntl(<AuditView />);
    const open = await screen.findByRole("button", { name: /, open entry #7$/ });
    const time = open.textContent ?? "";
    expect(time).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(open).toHaveAccessibleName(`${time}, open entry #7`);
  });

  it("says so when a new filter's log fails to load and the previous rows are still shown", async () => {
    const user = userEvent.setup();
    nav.search = "";
    const view = renderWithIntl(<AuditView />);
    await screen.findByRole("button", { name: /, open entry #7$/ });
    expect(screen.queryByText(/Couldn't load the log/)).not.toBeInTheDocument();

    apiMock.listAudit.mockRejectedValueOnce(new ApiError({ status: 503, code: "unavailable", message: "busy", kind: "http" }));
    nav.search = "status=clarify";
    view.rerender(<AuditView />);
    const banner = await screen.findByText(
      "Couldn't load the log for the current filters. The entries below are from the last successful load.",
    );
    expect(banner.closest("[role=alert]")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /, open entry #7$/ })).toBeInTheDocument(); // kept, dimmed

    await user.click(within(banner.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByText(/Couldn't load the log/)).not.toBeInTheDocument());
    expect(apiMock.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ status: "clarify" }), expect.anything());
  });

  it("says so when the overview for a new window fails to load", async () => {
    const user = userEvent.setup();
    nav.search = "";
    renderWithIntl(<AuditView />);
    expect(await screen.findByText(/3 decisions/)).toBeInTheDocument();

    apiMock.auditStats.mockRejectedValueOnce(new ApiError({ status: 500, code: "internal", message: "boom", kind: "http" }));
    await user.click(screen.getByRole("button", { name: "24 h" }));
    expect(
      await screen.findByText("Couldn't refresh the overview. The figures below are from the last successful load."),
    ).toBeInTheDocument();
    expect(screen.getByText(/3 decisions/)).toBeInTheDocument(); // the last figures stay, marked stale
  });
});
