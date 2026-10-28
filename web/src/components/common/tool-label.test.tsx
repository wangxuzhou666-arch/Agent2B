import { renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { IntlProviders, renderWithIntl, type TestLocale } from "../../../test/render";
import { ToolLabel, useToolLabelText } from "./tool-label";

describe("ToolLabel", () => {
  it("shows the label first, then the id in faint mono (DESIGN 10.4)", () => {
    const { container } = renderWithIntl(<ToolLabel label="Create purchase order" id="create_po" />);
    expect(container).toHaveTextContent(/^Create purchase order \(create_po\)$/);
    expect(screen.getByText("(create_po)")).toHaveClass("font-mono", "text-faint");
  });

  it("uses full-width parentheses in Chinese, with no space before them", () => {
    const { container } = renderWithIntl(<ToolLabel label="新建采购单" id="create_po" />, { locale: "zh" });
    expect(container).toHaveTextContent(/^新建采购单（create_po）$/);
  });

  it("lets the id take its parent's colour where the ground changes (a chip that inverts)", () => {
    renderWithIntl(<ToolLabel label="Show all categories" id="low_stock" tone="inherit" />);
    const id = screen.getByText("(low_stock)");
    expect(id).toHaveClass("font-mono", "opacity-70");
    expect(id).not.toHaveClass("text-faint");
  });

  it.each([
    ["no label", undefined, "create_po", "create_po"],
    ["a blank label", "  ", "create_po", "create_po"],
    ["a label that is the id", "create_po", "create_po", "create_po"],
    ["no id", "Create purchase order", null, "Create purchase order"],
  ])("shows one name only with %s", (_case, label, id, shown) => {
    const { container } = renderWithIntl(<ToolLabel label={label} id={id} />);
    expect(container).toHaveTextContent(new RegExp(`^${shown}$`));
  });

  it("renders nothing with neither", () => {
    const { container } = renderWithIntl(<ToolLabel label="" id={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("useToolLabelText", () => {
  const named = (locale: TestLocale) =>
    renderHook(() => useToolLabelText(), {
      wrapper: ({ children }: { children: ReactNode }) => <IntlProviders locale={locale}>{children}</IntlProviders>,
    }).result.current;

  it("writes the same pattern as plain text, for captions and region names", () => {
    expect(named("en")("Late shipments", "late_shipments")).toBe("Late shipments (late_shipments)");
    expect(named("zh")("逾期发货", "late_shipments")).toBe("逾期发货（late_shipments）");
    expect(named("en")("", "late_shipments")).toBe("late_shipments");
    expect(named("en")("Late shipments", null)).toBe("Late shipments");
  });
});
