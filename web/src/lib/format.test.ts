import { describe, expect, it } from "vitest";

import { formatClock, formatDuration, formatNumber, humanizeKey } from "./format";

describe("formatDuration", () => {
  it("follows the trace spec: ms under 1 s, one decimal under 10 s, whole seconds after", () => {
    expect(formatDuration(412.4)).toBe("412 ms");
    expect(formatDuration(3_449)).toBe("3.4 s");
    expect(formatDuration(12_400)).toBe("12 s");
    expect(formatDuration(0.3)).toBe("<1 ms");
  });

  it("omits missing or zero timings instead of showing false precision", () => {
    expect(formatDuration(0)).toBeNull();
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(undefined)).toBeNull();
  });
});

describe("formatClock", () => {
  it("formats countdown time", () => {
    expect(formatClock(42_000)).toBe("0:42");
    expect(formatClock(899_001)).toBe("15:00");
    expect(formatClock(3_725_000)).toBe("1:02:05");
    expect(formatClock(-5)).toBe("0:00");
  });
});

describe("formatNumber", () => {
  it("groups digits for the locale and keeps every fraction digit", () => {
    expect(formatNumber(1234567, "en")).toBe("1,234,567");
    expect(formatNumber(12.345678, "en")).toBe("12.345678");
  });
});

describe("humanizeKey", () => {
  it("turns column ids into headers and keeps short codes upper case", () => {
    expect(humanizeKey("days_late")).toBe("Days late");
    expect(humanizeKey("sku_id")).toBe("SKU ID");
    expect(humanizeKey("po_id")).toBe("PO ID");
  });
});
