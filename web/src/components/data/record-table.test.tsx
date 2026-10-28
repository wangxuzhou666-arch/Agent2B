import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { resolveView } from "@/lib/entities";

import { ENTITIES } from "../../../test/fixtures";
import { renderWithIntl } from "../../../test/render";
import { RecordTable, type RecordRow } from "./record-table";

const pagination = { page: 1, pageSize: 50, total: 2, onPageChange: () => undefined };

const headers = () =>
  screen.getAllByRole("columnheader").map((th) => th.getAttribute("title") ?? th.textContent ?? "");

describe("RecordTable", () => {
  it("builds the columns of a category from the spec: core fields, then its attrs", () => {
    const view = resolveView(ENTITIES, "item", "chilled")!;
    const rows: RecordRow[] = [
      {
        item_id: "I-7",
        name: "Cold Box",
        unit_cost: 1234.5,
        daily_demand: 3,
        lead_time_days: 4,
        grade: "B",
        margin_pct: 12,
        storage_temp: -18,
        fragile: true,
        _entity: "chilled",
      },
    ];
    renderWithIntl(
      <RecordTable view={view} rows={rows} sort={null} onSortChange={() => undefined} pagination={pagination} onOpen={() => undefined} />,
    );
    expect(headers()).toEqual([
      "Item ID",
      "Name",
      "Unit Cost",
      "Daily Demand (units/day)",
      "Lead Time (days)", // the label already names the unit: not repeated
      "Grade",
      "Margin % (%)",
      "Storage Temp (C)",
      "Fragile",
    ]);
    expect(screen.queryByRole("columnheader", { name: /Category/ })).not.toBeInTheDocument();
    const cells = within(screen.getAllByRole("row")[1]!).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual(["I-7", "Cold Box", "1,234.5", "3", "4", "B", "12", "-18", "Yes"]);
    expect(cells[2]).toHaveClass("text-right", "tabular-nums"); // float
    expect(cells[0]).not.toHaveClass("text-right");
    // a restricted field the role may see carries a marker
    expect(screen.getByRole("columnheader", { name: /Margin %/ })).toHaveTextContent("Restricted field");
  });

  it("adds a Category column on a root with categories, from each row's _entity", () => {
    const view = resolveView(ENTITIES, "item", null)!;
    const rows: RecordRow[] = [
      { item_id: "I-1", name: "Belt", _entity: "part" },
      { item_id: "I-2", name: "Plain", _entity: "item" },
      { item_id: "I-3", name: "Mystery", _entity: "brand_new" },
    ];
    renderWithIntl(
      <RecordTable view={view} rows={rows} sort={null} onSortChange={() => undefined} pagination={pagination} onOpen={() => undefined} />,
    );
    expect(headers().slice(0, 3)).toEqual(["Item ID", "Name", "Category"]);
    const categories = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[2]!.textContent);
    expect(categories).toEqual(["Part", "Item", "brand_new"]);
    // missing values render as an em dash
    expect(within(screen.getAllByRole("row")[1]!).getAllByRole("cell")[3]).toHaveTextContent("—");
  });

  it("sorts on the server and opens a record from its key", async () => {
    const user = userEvent.setup();
    const onSortChange = vi.fn();
    const onOpen = vi.fn();
    const view = resolveView(ENTITIES, "item", "part")!;
    const row = { item_id: "I-1", name: "Belt", machine: "M-4", _entity: "part" };
    renderWithIntl(
      <RecordTable view={view} rows={[row]} sort={{ columnId: "machine", direction: "desc" }} onSortChange={onSortChange} pagination={pagination} onOpen={onOpen} />,
    );
    expect(screen.getByRole("columnheader", { name: /Machine/ })).toHaveAttribute("aria-sort", "descending");
    await user.click(within(screen.getByRole("columnheader", { name: /Unit Cost/ })).getByRole("button"));
    expect(onSortChange).toHaveBeenCalledWith({ columnId: "unit_cost", direction: "asc" });
    await user.click(screen.getByRole("button", { name: "Open record I-1" }));
    expect(onOpen).toHaveBeenCalledWith(row);
  });
});
