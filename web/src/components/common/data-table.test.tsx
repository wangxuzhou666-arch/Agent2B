import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "../../../test/render";
import { DataTable, type DataTableColumn } from "./data-table";

type Row = { sku: string; qty: number | null };
const rows: Row[] = [
  { sku: "SKU-2", qty: 1200 },
  { sku: "SKU-10", qty: null },
  { sku: "SKU-1", qty: 30 },
];
const columns: DataTableColumn<Row>[] = [
  { id: "sku", header: "SKU", headerLabel: "sku", value: (r) => r.sku },
  { id: "qty", header: "Qty", headerLabel: "qty", value: (r) => r.qty, numeric: true },
];

const bodyCells = (column: number) =>
  within(screen.getAllByRole("rowgroup")[1]!)
    .getAllByRole("row")
    .map((row) => within(row).getAllByRole("cell")[column]!.textContent);

describe("DataTable", () => {
  it("sorts numbers numerically, keeps empty values last, and cycles asc → desc → none", async () => {
    const user = userEvent.setup();
    renderWithIntl(<DataTable columns={columns} rows={rows} />);
    const qty = screen.getByRole("columnheader", { name: /qty/i });
    expect(qty).toHaveAttribute("aria-sort", "none");

    await user.click(within(qty).getByRole("button"));
    expect(qty).toHaveAttribute("aria-sort", "ascending");
    expect(bodyCells(1)).toEqual(["30", "1,200", "—"]);

    await user.click(within(qty).getByRole("button"));
    expect(qty).toHaveAttribute("aria-sort", "descending");
    expect(bodyCells(1)).toEqual(["1,200", "30", "—"]);

    await user.click(within(qty).getByRole("button"));
    expect(bodyCells(1)).toEqual(["1,200", "—", "30"]);
  });

  it("sorts text with natural number order", async () => {
    const user = userEvent.setup();
    renderWithIntl(<DataTable columns={columns} rows={rows} />);
    await user.click(within(screen.getByRole("columnheader", { name: /sku/i })).getByRole("button"));
    expect(bodyCells(0)).toEqual(["SKU-1", "SKU-2", "SKU-10"]);
  });

  it("right-aligns numeric columns with tabular figures", () => {
    renderWithIntl(<DataTable columns={columns} rows={rows} />);
    const cell = screen.getByText("1,200").closest("td");
    expect(cell).toHaveClass("text-right", "tabular-nums");
  });

  it("leaves ordering to the caller when sort is controlled (server-side)", async () => {
    const user = userEvent.setup();
    const onSortChange = vi.fn();
    renderWithIntl(<DataTable columns={columns} rows={rows} sort={null} onSortChange={onSortChange} />);
    await user.click(within(screen.getByRole("columnheader", { name: /qty/i })).getByRole("button"));
    expect(onSortChange).toHaveBeenCalledWith({ columnId: "qty", direction: "asc" });
    expect(bodyCells(0)).toEqual(["SKU-2", "SKU-10", "SKU-1"]);
  });

  it("shows an empty state and a pagination bar", async () => {
    const user = userEvent.setup();
    const onPageChange = vi.fn();
    renderWithIntl(
      <DataTable columns={columns} rows={[]} pagination={{ page: 1, pageSize: 50, total: 120, onPageChange }} />,
    );
    expect(screen.getByText("No rows.")).toBeInTheDocument();
    expect(screen.getByText("1–50 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
