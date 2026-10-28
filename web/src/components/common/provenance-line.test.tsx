import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Provenance } from "@/lib/api";

import { renderWithIntl } from "../../../test/render";
import { ProvenanceLine } from "./provenance-line";

const provenance: Provenance = {
  tool: "low_stock",
  label: "Low stock",
  row_count: 19,
  as_of: "2026-10-26T08:00:00Z",
  scope: "all warehouses",
};

describe("ProvenanceLine", () => {
  it("names the tool in the table strip by its label, then its id in mono, after a 'Source' for screen readers", () => {
    const en = renderWithIntl(<ProvenanceLine provenance={provenance} variant="strip" />);
    expect(en.container.querySelector(".sr-only")?.textContent).toBe("Source: ");
    expect(en.container).toHaveTextContent(/^Source: Low stock \(low_stock\)/);
    expect(screen.getByText("(low_stock)")).toHaveClass("font-mono", "text-faint");
    en.unmount();

    const zh = renderWithIntl(<ProvenanceLine provenance={{ ...provenance, label: "低库存" }} variant="strip" />, { locale: "zh" });
    // full-width colon and parentheses, no space around them
    expect(zh.container.querySelector(".sr-only")?.textContent).toBe("来源：");
    expect(zh.container).toHaveTextContent(/^来源：低库存（low_stock）/);
  });

  it("writes the standalone line as one sentence: label (tool id), then the meta", () => {
    renderWithIntl(<ProvenanceLine provenance={{ ...provenance, label: "低库存" }} />, { locale: "zh" });
    expect(document.body).toHaveTextContent(
      /^来源：低库存（low_stock） · 19 行 · 数据截至 \d{4}-\d{2}-\d{2} \d{2}:\d{2} · 范围：all warehouses$/,
    );
  });

  it.each([
    ["no label", ""],
    ["a label that is the id", "low_stock"],
  ])("falls back to the tool id alone with %s", (_case, label) => {
    renderWithIntl(<ProvenanceLine provenance={{ ...provenance, label }} />);
    expect(document.body).toHaveTextContent(/^Source: low_stock · 19 rows · /);
  });
});
