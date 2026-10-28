// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ENTITIES, field, ITEM } from "../../test/fixtures";
import {
  addFilterValue,
  dataHref,
  dataSearch,
  familyLabel,
  filterKind,
  filtersFor,
  filterValueProblem,
  importTargets,
  locateView,
  parseSort,
  readDataLocation,
  removeFilterValue,
  resolveView,
  sortParam,
  unitSuffix,
  viewFields,
} from "./entities";

describe("views from the spec", () => {
  it("a root view has the core fields and no category", () => {
    const view = resolveView(ENTITIES, "item", null)!;
    expect(view.entityId).toBe("item");
    expect(view.category).toBeNull();
    expect(viewFields(view).map((f) => f.id)).toEqual(ITEM.fields.map((f) => f.id));
  });

  it("a category view adds that category's attrs after the core fields", () => {
    const view = resolveView(ENTITIES, "item", "chilled")!;
    expect(view.entityId).toBe("chilled");
    expect(view.label).toBe("Chilled Item");
    expect(viewFields(view).map((f) => f.id).slice(-2)).toEqual(["storage_temp", "fragile"]);
  });

  it("finds a category named directly in the URL and falls back to the first root", () => {
    expect(locateView(ENTITIES, "part", null)).toMatchObject({ entityId: "part", root: { id: "item" } });
    expect(locateView(ENTITIES, "site", null)?.entityId).toBe("site");
    expect(locateView(ENTITIES, "nope", null)?.entityId).toBe("item");
    expect(locateView([], null, null)).toBeNull();
  });

  it("lists every import target, categories under their root", () => {
    expect(importTargets(ENTITIES).map((t) => t.path)).toEqual(["Item", "Item › Part", "Item › Chilled Item", "Site"]);
  });

  it("labels a row's family member and links to its tab", () => {
    expect(familyLabel(ITEM, "chilled")).toBe("Chilled Item");
    expect(familyLabel(ITEM, "item")).toBe("Item");
    expect(familyLabel(ITEM, "brand_new")).toBe("brand_new");
    expect(familyLabel(ITEM, undefined)).toBeNull();
    expect(dataHref(ENTITIES, "part")).toBe("/data?entity=item&category=part");
  });

  it("prints a unit unless the label already names it", () => {
    expect(unitSuffix({ label: "Daily Demand", unit: "units/day" })).toBe("units/day");
    expect(unitSuffix({ label: "Lead Time (days)", unit: "days" })).toBeNull();
    expect(unitSuffix({ label: "Name", unit: null })).toBeNull();
  });
});

describe("filters", () => {
  it("picks a control by field type", () => {
    expect(filterKind(field({ id: "g", label: "G", type: "enum", enum_values: ["A"] }))).toBe("enum");
    expect(filterKind(field({ id: "b", label: "B", type: "bool" }))).toBe("bool");
    expect(filterKind(field({ id: "n", label: "N", type: "int" }))).toBe("number");
    expect(filterKind(field({ id: "d", label: "D", type: "date" }))).toBe("date");
    expect(filterKind(field({ id: "s", label: "S" }))).toBe("text");
  });

  it("checks values before they reach the server", () => {
    const int = field({ id: "n", label: "N", type: "int" });
    expect(filterValueProblem(int, " ")).toBe("required");
    expect(filterValueProblem(int, "2.5")).toBe("integer");
    expect(filterValueProblem(int, "x")).toBe("number");
    expect(filterValueProblem(int, "12")).toBeNull();
    expect(filterValueProblem(field({ id: "d", label: "D", type: "date" }), "26/10/2026")).toBe("date");
    expect(filterValueProblem(field({ id: "g", label: "G", type: "enum", enum_values: ["A"] }), "Z")).toBe("option");
  });

  it("adds values as 'any of', removes them, and drops fields the view does not have", () => {
    let filters = addFilterValue({}, "grade", "A");
    filters = addFilterValue(filters, "grade", "B");
    filters = addFilterValue(filters, "grade", "B");
    expect(filters).toEqual({ grade: ["A", "B"] });
    expect(removeFilterValue(filters, "grade", "A")).toEqual({ grade: ["B"] });
    expect(removeFilterValue(filters, "grade", "B")).toEqual({ grade: ["A"] });
    expect(filtersFor({ grade: ["A"], machine: ["M-1"], name: [" "] }, ITEM.fields)).toEqual({ grade: ["A"] });
  });

  it("round-trips the Data window location through the URL", () => {
    const search = dataSearch({
      entity: "item",
      category: "part",
      sort: { columnId: "unit_cost", direction: "desc" },
      page: 3,
      filters: { grade: ["A", "B"], machine: ["M-1"] },
    });
    expect(search).toBe("?entity=item&category=part&sort=-unit_cost&page=3&f.grade=A&f.grade=B&f.machine=M-1");
    expect(readDataLocation(new URLSearchParams(search.slice(1)))).toEqual({
      entity: "item",
      category: "part",
      sort: { columnId: "unit_cost", direction: "desc" },
      page: 3,
      filters: { grade: ["A", "B"], machine: ["M-1"] },
    });
  });

  it("speaks the API's sort syntax", () => {
    expect(sortParam({ columnId: "qty", direction: "asc" })).toBe("qty");
    expect(sortParam({ columnId: "qty", direction: "desc" })).toBe("-qty");
    expect(sortParam(null)).toBeUndefined();
    expect(parseSort("-qty")).toEqual({ columnId: "qty", direction: "desc" });
    expect(parseSort("-")).toBeNull();
  });
});
