import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { ENTITIES } from "../../../test/fixtures";
import { IntlProviders, renderWithIntl } from "../../../test/render";
import { DataView } from "./data-view";

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => "/data",
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const apiMock = vi.hoisted(() => ({ listEntities: vi.fn(), listRecords: vi.fn(), getRecord: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

beforeEach(() => {
  nav.replace.mockReset();
  apiMock.listEntities.mockResolvedValue({ items: ENTITIES });
  apiMock.listRecords.mockImplementation(async (entity: string) => ({
    entity,
    items: [{ item_id: "I-9", name: "Ice Pack", storage_temp: -20, fragile: false, _entity: "chilled" }],
    total: 1,
    page: 1,
    page_size: 50,
  }));
  apiMock.getRecord.mockResolvedValue({ item_id: "I-9", name: "Ice Pack", storage_temp: -20, fragile: false, _entity: "chilled" });
});

describe("DataView", () => {
  it("turns the URL into a records query: entity, sort, page and only the filters the view has", async () => {
    nav.search = "entity=item&category=chilled&sort=-storage_temp&page=2&f.grade=A&f.fragile=true&f.machine=M-1";
    renderWithIntl(<DataView />);
    await screen.findByRole("cell", { name: "-20" });
    expect(apiMock.listRecords).toHaveBeenCalledWith(
      "chilled",
      { sort: "-storage_temp", page: 2, page_size: 50 },
      { grade: ["A"], fragile: ["true"] }, // `machine` belongs to another category: dropped
      expect.anything(),
    );
  });

  it("shows a root's categories as tabs, including one the frontend has never seen", async () => {
    const user = userEvent.setup();
    nav.search = "entity=item";
    renderWithIntl(<DataView />);
    const tabs = await screen.findByRole("tablist", { name: "Item categories" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual(["All", "Part", "Chilled Item"]);
    await user.click(within(tabs).getByRole("tab", { name: "Chilled Item" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/data?entity=item&category=chilled", { scroll: false });
  });

  it("switches entity from the picker and resets the rest", async () => {
    const user = userEvent.setup();
    nav.search = "entity=item&sort=name&f.grade=A";
    renderWithIntl(<DataView />);
    await user.click(await screen.findByRole("button", { name: /^Site/ }));
    expect(nav.replace).toHaveBeenLastCalledWith("/data?entity=site", { scroll: false });
  });

  it("puts a new filter in the URL and starts from page one", async () => {
    const user = userEvent.setup();
    nav.search = "entity=item&category=chilled&page=3";
    renderWithIntl(<DataView />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "Field" }), "fragile");
    await user.selectOptions(screen.getByRole("combobox", { name: "Value for Fragile" }), "false");
    await user.click(screen.getByRole("button", { name: "Add filter" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/data?entity=item&category=chilled&f.fragile=false", { scroll: false });
  });

  it("opens a record with its core fields and its category's attributes", async () => {
    const user = userEvent.setup();
    nav.search = "entity=item";
    renderWithIntl(<DataView />);
    await user.click(await screen.findByRole("button", { name: "Open record I-9" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("heading", { name: "Ice Pack" })).toBeInTheDocument();
    expect(within(sheet).getByRole("heading", { name: "Item fields" })).toBeInTheDocument();
    const attrs = within(sheet).getByRole("heading", { name: "Chilled Item attributes" }).parentElement!;
    expect(within(attrs).getByText("Storage Temp")).toBeInTheDocument();
    expect(within(attrs).getByText("No")).toBeInTheDocument(); // fragile=false
    expect(apiMock.getRecord).toHaveBeenCalledWith("chilled", "I-9", expect.anything());
  });

  it("never shows the previous query's rows under filters whose fetch failed", async () => {
    nav.search = "entity=item&category=chilled";
    const view = renderWithIntl(<DataView />);
    expect(await screen.findByRole("cell", { name: "-20" })).toBeInTheDocument();

    apiMock.listRecords.mockRejectedValueOnce(
      new ApiError({ status: 503, code: "unavailable", message: "The store is busy.", kind: "http" }),
    );
    nav.search = "entity=item&category=chilled&f.fragile=true";
    view.rerender(<DataView />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The store is busy.");
    expect(within(alert).getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: "-20" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open record I-9" })).not.toBeInTheDocument();
  });

  it("fetches the entities again in the language the console switches to; the rows stay", async () => {
    const zh = ENTITIES.map((entity) =>
      entity.id === "item"
        ? {
            ...entity,
            label: "物品",
            categories: entity.categories.map((c) => (c.id === "chilled" ? { ...c, label: "冷藏物品" } : c)),
          }
        : entity,
    );
    apiMock.listEntities.mockResolvedValueOnce({ items: ENTITIES }).mockResolvedValueOnce({ items: zh });
    nav.search = "entity=item&category=chilled";
    const view = render(
      <IntlProviders locale="en">
        <DataView />
      </IntlProviders>,
    );
    expect(await screen.findByRole("tab", { name: "Chilled Item" })).toBeInTheDocument();
    expect(await screen.findByRole("cell", { name: "-20" })).toBeInTheDocument();

    view.rerender(
      <IntlProviders locale="zh">
        <DataView />
      </IntlProviders>,
    );
    expect(await screen.findByRole("tab", { name: "冷藏物品" })).toBeInTheDocument();
    expect(apiMock.listEntities).toHaveBeenCalledTimes(2);
    // records are values, not words: the same query, not fetched again, never blanked
    expect(screen.getByRole("cell", { name: "-20" })).toBeInTheDocument();
    expect(apiMock.listRecords).toHaveBeenCalledTimes(1);
  });
});
