// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, apiUrl } from "./api";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("record filters → query parameters", () => {
  it("repeats a parameter for each value and skips empty ones", () => {
    expect(apiUrl("/api/entities/item/records", { grade: ["A", "B", ""], page: 2 })).toBe(
      "http://localhost:8000/api/entities/item/records?grade=A&grade=B&page=2",
    );
  });

  it("sends one parameter per field id, with sort and paging alongside", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ entity: "part", items: [], total: 0, page: 2, page_size: 50 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await api.listRecords("part", { sort: "-unit_cost", page: 2, page_size: 50 }, { grade: ["A", "B"], machine: ["M-1"] });
    const url = new URL(String(fetchMock.mock.calls[0]?.[0]));
    expect(url.pathname).toBe("/api/entities/part/records");
    expect(url.searchParams.getAll("grade")).toEqual(["A", "B"]);
    expect(url.searchParams.get("machine")).toBe("M-1");
    expect(url.searchParams.get("sort")).toBe("-unit_cost");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("page_size")).toBe("50");
  });

  it("never lets a filter override the reserved names", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    await api.listRecords("part", { page: 1 }, { page: ["9"] });
    const url = new URL(String(fetchMock.mock.calls[0]?.[0]));
    expect(url.searchParams.getAll("page")).toEqual(["1"]);
  });
});
