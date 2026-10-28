import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { resolveView, viewFields } from "@/lib/entities";

import { ENTITIES } from "../../../test/fixtures";
import { renderWithIntl } from "../../../test/render";
import { RecordFilters } from "./record-filters";

const fields = viewFields(resolveView(ENTITIES, "item", "chilled")!);

describe("RecordFilters", () => {
  it("offers a value control that fits the field's type", async () => {
    const user = userEvent.setup();
    renderWithIntl(<RecordFilters fields={fields} filters={{}} onChange={() => undefined} />);
    const field = screen.getByRole("combobox", { name: "Field" });
    expect(screen.getByRole("textbox", { name: "Value for Item ID" })).toBeInTheDocument();

    await user.selectOptions(field, "grade");
    const options = screen.getByRole("combobox", { name: "Value for Grade" }).querySelectorAll("option");
    expect([...options].map((o) => o.textContent)).toEqual(["Pick a value", "A", "B", "C"]);

    await user.selectOptions(field, "lead_time_days");
    expect(screen.getByRole("spinbutton", { name: "Value for Lead Time (days)" })).toHaveAttribute("step", "1");

    await user.selectOptions(field, "fragile");
    const bool = screen.getByRole("combobox", { name: "Value for Fragile" }).querySelectorAll("option");
    expect([...bool].map((o) => o.textContent)).toEqual(["Pick a value", "Yes", "No"]);
  });

  it("adds a filter, and several values of one field as 'any of'", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = renderWithIntl(<RecordFilters fields={fields} filters={{}} onChange={onChange} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Field" }), "grade");
    await user.selectOptions(screen.getByRole("combobox", { name: "Value for Grade" }), "A");
    await user.click(screen.getByRole("button", { name: "Add filter" }));
    expect(onChange).toHaveBeenLastCalledWith({ grade: ["A"] });

    rerender(<RecordFilters fields={fields} filters={{ grade: ["A"] }} onChange={onChange} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Value for Grade" }), "C");
    await user.click(screen.getByRole("button", { name: "Add filter" }));
    expect(onChange).toHaveBeenLastCalledWith({ grade: ["A", "C"] });
  });

  it("checks values before sending, then shows active filters as removable chips", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = renderWithIntl(<RecordFilters fields={fields} filters={{}} onChange={onChange} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Field" }), "lead_time_days");
    await user.type(screen.getByRole("spinbutton", { name: "Value for Lead Time (days)" }), "2.5");
    await user.click(screen.getByRole("button", { name: "Add filter" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a whole number.");
    expect(onChange).not.toHaveBeenCalled();

    rerender(<RecordFilters fields={fields} filters={{ grade: ["A", "C"], fragile: ["true"] }} onChange={onChange} />);
    expect(screen.getByText("Several values of one field match any of them.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove filter Grade A" }));
    expect(onChange).toHaveBeenLastCalledWith({ grade: ["C"], fragile: ["true"] });
    expect(screen.getByRole("button", { name: "Remove filter Fragile Yes" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onChange).toHaveBeenLastCalledWith({});
  });
});
