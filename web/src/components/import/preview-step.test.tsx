import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ImportPreviewOut } from "@/lib/api";
import { importTargets } from "@/lib/entities";

import { ENTITIES } from "../../../test/fixtures";
import { renderWithIntl } from "../../../test/render";
import { PreviewStep } from "./preview-step";

const target = importTargets(ENTITIES).find((t) => t.id === "chilled")!;

const preview: ImportPreviewOut = {
  entity: "chilled",
  file_type: "csv",
  encoding: "utf-8",
  mapping: [
    { column: 0, header: "物料编码", field_id: "item_id", label: "Item ID" },
    { column: 1, header: "Item Name", field_id: "name", label: "Name" },
    { column: 2, header: "Cost", field_id: "unit_cost", label: "Unit Cost" },
    { column: 3, header: "Temp", field_id: "storage_temp", label: "Storage Temp" },
  ],
  unmapped: ["Notes"],
  rows: [
    {
      row: 2,
      values: { item_id: "I-1", name: "Ice Pack", unit_cost: 2.5, storage_temp: -20 },
      errors: [],
      field_errors: [],
      action: "new",
    },
    {
      row: 3,
      values: { item_id: "I-2", name: null, unit_cost: "twelve", storage_temp: -5 },
      errors: ["Name: required", "Unit Cost: must be a number"],
      field_errors: [
        { field_id: "name", message: "required" },
        { field_id: "unit_cost", message: "must be a number" },
      ],
      action: null,
    },
    {
      row: 4,
      values: { item_id: "I-3", name: "Cooler", unit_cost: 9, storage_temp: 4 },
      errors: [],
      field_errors: [],
      action: "update",
    },
    {
      row: 5,
      values: { item_id: "I-1", name: "Again", unit_cost: 1, storage_temp: 0 },
      errors: ["duplicate key; first seen in row 2"],
      field_errors: [{ field_id: null, message: "duplicate key; first seen in row 2" }],
      action: null,
    },
  ],
  valid_count: 2,
  error_count: 2,
  digest: "abc",
  grant: null,
};

function renderPreview(over: Partial<Parameters<typeof PreviewStep>[0]> = {}) {
  const props = {
    target,
    file: { name: "chilled.csv", size: 900 },
    preview,
    committing: false,
    issue: null,
    onCommit: vi.fn(),
    onCancel: vi.fn(),
    ...over,
  };
  renderWithIntl(<PreviewStep {...props} />);
  return props;
}

describe("PreviewStep", () => {
  it("maps file headers to field labels and flags the ones that map to nothing", () => {
    renderPreview();
    const mapping = screen.getByRole("list", { name: "Column mapping" });
    const items = within(mapping).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("物料编码maps toItem ID item_id");
    expect(items.at(-1)).toHaveTextContent("NotesNot imported");
    expect(screen.getByText("1 column doesn't match any field and is ignored.")).toBeInTheDocument();
  });

  it("counts rows, valid rows and errors", () => {
    renderPreview();
    const stats = screen.getByText((_, el) => el?.tagName === "P" && /^4 rows · 2 valid/.test(el.textContent ?? ""));
    expect(stats).toHaveTextContent("4 rows · 2 valid · 2 errors (won't be imported)");
  });

  it("highlights the cells at fault and lists every message under the row", () => {
    renderPreview();
    const table = screen.getByRole("table", { name: "Rows" });
    const row3 = table.querySelector('tr[data-row="3"]')!;
    expect(row3).toHaveAttribute("data-invalid", "true");
    const invalid = [...row3.querySelectorAll('td[aria-invalid="true"]')];
    expect(invalid.map((td) => td.getAttribute("title"))).toEqual(["Name: required", "Unit Cost: must be a number"]);
    expect(invalid[1]).toHaveTextContent("twelve");
    expect(within(table).getByText("Name: required; Unit Cost: must be a number")).toBeInTheDocument();
    // a row-level problem tints the row without blaming a cell
    const row5 = table.querySelector('tr[data-row="5"]')!;
    expect(row5.querySelectorAll('td[aria-invalid="true"]')).toHaveLength(0);
    expect(within(table).getByText("duplicate key; first seen in row 2")).toBeInTheDocument();
    // statuses: New / Error / Update
    expect(within(table).getAllByText(/^(New|Update|Error)$/).map((el) => el.textContent)).toEqual(["New", "Error", "Update", "Error"]);
    // numbers line up on the right
    expect(within(row3 as HTMLElement).getByText("-5").closest("td")).toHaveClass("text-right", "tabular-nums");
  });

  it("can show only the rows with errors", async () => {
    const user = userEvent.setup();
    renderPreview();
    await user.click(screen.getByRole("button", { name: "Only rows with errors (2)" }));
    const rows = [...screen.getByRole("table", { name: "Rows" }).querySelectorAll("tr[data-row]")];
    expect(rows.map((r) => r.getAttribute("data-row"))).toEqual(["3", "5"]);
  });

  it("imports the valid rows, or explains why there is nothing to import", async () => {
    const user = userEvent.setup();
    const props = renderPreview();
    await user.click(screen.getByRole("button", { name: "Import 2 rows" }));
    expect(props.onCommit).toHaveBeenCalled();
    renderPreview({ preview: { ...preview, valid_count: 0, error_count: 4 } });
    expect(screen.getByRole("button", { name: "Import 0 rows" })).toBeDisabled();
    expect(screen.getByText(/No row passed validation/)).toBeInTheDocument();
  });
});
