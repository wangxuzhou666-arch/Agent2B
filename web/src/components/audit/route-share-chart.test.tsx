import { cloneElement, isValidElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { shareRows } from "@/lib/audit";

import { renderWithIntl } from "../../../test/render";
import { RouteShareChart } from "./route-share-chart";

// jsdom has no layout, so give the chart the size a ResponsiveContainer would measure
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement<{ width?: number; height?: number }>(children)
        ? cloneElement(children, { width: 640, height: 160 })
        : null,
  };
});

const rows = shareRows({ rule: 5, llm: 3, teleport: 2 });

describe("RouteShareChart", () => {
  it("draws one bar per route source in one hue, labelled with count and share", () => {
    const { container } = renderWithIntl(
      <RouteShareChart rows={rows} buckets={[]} selected={null} onSelect={() => undefined} meaningOf={() => null} />,
    );
    const chart = container.querySelector('[data-testid="route-share-chart"]')!;
    expect(chart).toHaveAttribute("aria-hidden", "true");
    const bars = chart.querySelectorAll("path.recharts-rectangle, .recharts-bar-rectangle path");
    expect(bars.length).toBeGreaterThanOrEqual(rows.length);
    const fills = [...chart.querySelectorAll("[class*='fill-chart-']")].map((el) => el.getAttribute("class") ?? "");
    expect(fills.length).toBeGreaterThan(0);
    expect(fills.every((c) => c.includes("fill-chart-1"))).toBe(true);
    const text = chart.textContent ?? "";
    for (const key of ["rule", "llm", "teleport"]) expect(text).toContain(key);
    expect(text).toContain("5 · 50%");
    expect(text).toContain("2 · 20%");
  });

  it("keeps the selected source in the accent and greys the rest", () => {
    const { container } = renderWithIntl(
      <RouteShareChart rows={rows} buckets={[]} selected="llm" onSelect={() => undefined} meaningOf={() => null} />,
    );
    const fills = [...container.querySelectorAll("[class*='fill-chart-']")].map((el) => el.getAttribute("class") ?? "");
    expect(fills.filter((c) => c.includes("fill-chart-1")).length).toBeGreaterThan(0);
    expect(fills.filter((c) => c.includes("fill-chart-5")).length).toBeGreaterThan(0);
  });
});
