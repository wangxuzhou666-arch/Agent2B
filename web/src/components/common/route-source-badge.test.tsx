import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuditFilterBar } from "@/components/audit/audit-filters";
import { AuditStats } from "@/components/audit/audit-stats";
import { TooltipBody } from "@/components/audit/route-share-chart";
import type { AuditStatsOut, TraceStep } from "@/lib/api";
import { ROUTE_SOURCES, shareRows } from "@/lib/audit";

import { renderWithIntl } from "../../../test/render";
import { RouteSourceBadge, useRouteSourceMeaning } from "./route-source-badge";
import { TraceList } from "./trace-list";

vi.mock("@/components/audit/route-share-chart", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/audit/route-share-chart")>();
  return { ...actual, RouteShareChart: () => <div data-testid="route-share-chart" /> };
});

const FALLBACK = {
  en: "Chat fallback: no report matched",
  zh: "对话兜底：没有匹配的报表",
} as const;

/** A query no arm could place: the chat fallback, as the server traces it. */
const fallbackTrace: TraceStep[] = [
  { step: "candidates", status: "ok", source: null, facts: { count: 9 }, duration_ms: 0.4 },
  { step: "understand", status: "ok", source: "fallback", facts: { kind: "chat", reason: "no target matched" }, duration_ms: 1 },
  { step: "respond", status: "ok", source: null, facts: { rows: 0 }, duration_ms: 0 },
];

const stats = {
  since: "2026-10-19T08:00:00Z",
  until: null,
  total: 4,
  by_route_source: { rule: 3, fallback: 1 },
  by_status: { ok: 3, chat: 1 },
  buckets: [
    { route_source: "rule", status: "ok", count: 3 },
    { route_source: "fallback", status: "chat", count: 1 },
  ],
} satisfies AuditStatsOut;

describe.each(["en", "zh"] as const)("the fallback route source (%s)", (locale) => {
  const meaning = FALLBACK[locale];

  it("is a known source with its own meaning", () => {
    expect(ROUTE_SOURCES).toContain("fallback");
    function Probe() {
      return <span>{useRouteSourceMeaning()("fallback")}</span>;
    }
    renderWithIntl(<Probe />, { locale });
    expect(screen.getByText(meaning)).toBeInTheDocument();
  });

  it("badge: verbatim value, meaning in the title and for screen readers", () => {
    renderWithIntl(<RouteSourceBadge source="fallback" />, { locale });
    const badge = screen.getByText((_, el) => el?.getAttribute("data-route-source") === "fallback");
    expect(badge).toHaveAttribute("title", meaning);
    expect(badge).toHaveTextContent(`fallback (${meaning})`);
  });

  it("hub trace: the chat line carries the tagged source", () => {
    renderWithIntl(<TraceList steps={fallbackTrace} elapsedMs={12} defaultOpen />, { locale });
    const steps = within(screen.getByRole("list"));
    const line = steps.getByText((_, el) => el?.tagName === "LI" && el.textContent?.includes("fallback") === true);
    expect(line).toHaveTextContent(locale === "en" ? "No report matches; answering as general chat" : "没有匹配的报表，按通用对话回答");
    expect(within(line).getByTitle(meaning)).toHaveTextContent("fallback");
  });

  it("audit filters: the option and the active chip name it", () => {
    renderWithIntl(
      <AuditFilterBar filters={{ route_source: "fallback" }} onChange={() => undefined} statusOptions={[]} sourceOptions={ROUTE_SOURCES} />,
      { locale },
    );
    const select = screen.getByRole("combobox", { name: locale === "en" ? "Route source" : "路由来源" });
    expect(within(select).getByRole("option", { name: `fallback · ${meaning}` })).toHaveAttribute("value", "fallback");
    expect(select).toHaveValue("fallback");
    expect(select).toHaveAttribute("title", meaning);
    expect(screen.getByTitle(`fallback · ${meaning}`)).toHaveTextContent("fallback");
  });

  it("audit stats: the table view and the chart tooltip name it", () => {
    renderWithIntl(
      <AuditStats
        stats={stats}
        loading={false}
        error={null}
        statsWindow="7d"
        onWindowChange={() => undefined}
        onRetry={() => undefined}
        selectedSource={null}
        selectedStatus={null}
        onSelectSource={() => undefined}
        onSelectStatus={() => undefined}
      />,
      { locale },
    );
    const table = screen.getByRole("table");
    const row = within(table).getAllByRole("row").find((r) => r.textContent?.startsWith("fallback"));
    expect(row).toBeDefined();
    expect(within(row!).getByRole("button")).toHaveAttribute("title", meaning);
  });

  it("chart tooltip: the source verbatim, then its meaning", () => {
    const rows = shareRows(stats.by_route_source);
    function Tip() {
      const meaningOf = useRouteSourceMeaning();
      return <TooltipBody active payload={[{ payload: rows[1]! }]} buckets={stats.buckets} meaningOf={meaningOf} />;
    }
    renderWithIntl(<Tip />, { locale });
    expect(screen.getByText(`· ${meaning}`)).toBeInTheDocument();
    expect(screen.getByText(`· ${meaning}`).parentElement).toHaveTextContent(`fallback · ${meaning}`);
  });
});
