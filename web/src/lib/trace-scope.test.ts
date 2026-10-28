// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { TraceStep } from "./api";
import { describeStep, describeTrace } from "./trace";

const step = (over: Partial<TraceStep> & Pick<TraceStep, "step">): TraceStep =>
  ({ status: "ok", source: null, facts: {}, duration_ms: 0, ...over }) as TraceStep;

describe("scope step", () => {
  it("says every named item is bound", () => {
    const [line] = describeStep(step({ step: "scope", facts: { mentioned: ["SKU-1", "WH-2"], bound: ["SKU-1", "WH-2"], unbound: [] } }), 0);
    expect(line).toMatchObject({ key: "scopeOk", values: { count: 2 }, tone: "ok" });
  });

  it("names the items the parameters do not cover, as a warning", () => {
    const [line] = describeStep(
      step({ step: "scope", status: "blocked", facts: { mentioned: ["SKU-1", "WH-9"], bound: ["SKU-1"], unbound: ["WH-9"] } }),
      0,
    );
    expect(line).toMatchObject({ key: "scopeUnbound", values: { count: 1, items: "WH-9" }, tone: "warn" });
  });

  it("reads the scope guard's verdicts and mention objects", () => {
    const gap = describeStep(
      step({
        step: "scope",
        status: "blocked",
        facts: {
          verdict: "gap",
          mentioned: [{ key: "field:inventory.warehouse", value: "WH-EAST", text: "WH EAST" }],
          bound: [],
          unbound: [{ key: "field:inventory.warehouse", value: "WH-EAST" }],
          conflicts: [],
          acknowledged: false,
        },
      }),
      0,
    )[0];
    // named by the question's own words, which only `mentioned` carries
    expect(gap).toMatchObject({ key: "scopeUnbound", values: { count: 1, items: "WH EAST" }, tone: "warn" });
    const conflict = describeStep(
      step({ step: "scope", status: "blocked", facts: { verdict: "conflict", conflicts: [{ key: "k", value: "B", param: "grade", negated: true }] } }),
      0,
    )[0];
    expect(conflict).toMatchObject({ key: "scopeConflict", values: { count: 1, items: "not B" }, tone: "warn" });
    const unfiltered = describeStep(
      step({ step: "scope", facts: { verdict: "unfiltered", acknowledged: true, unbound: [{ key: "k", value: "WH-EAST" }] } }),
      0,
    )[0];
    expect(unfiltered).toMatchObject({ key: "scopeUnfiltered", values: { items: "WH-EAST" }, tone: "neutral" });
    const pass = describeStep(
      step({ step: "scope", facts: { verdict: "pass", mentioned: [{ key: "k", value: "A", label: "grade" }], bound: [{ key: "k", value: "A" }] } }),
      0,
    )[0];
    expect(pass).toMatchObject({ key: "scopeOk", values: { count: 1 }, tone: "ok" });
  });

  it("names constraints the way the scope guard sends them: words from `mentioned`, never bare ids", () => {
    // facts exactly as core/routing/scope_guard.py emits them for "low stock items except packaging"
    const facts = {
      target: "low_stock",
      verdict: "gap",
      mentioned: [
        { key: "category:sku", value: "packaging", negated: true, text: "packaging" },
        { key: "category:sku", value: "cold_chain_item", text: "cold chain" },
      ],
      bound: [],
      unbound: [
        { key: "category:sku", value: "packaging", negated: true },
        { key: "category:sku", value: "cold_chain_item" },
      ],
      conflicts: [],
      filled: {},
      acknowledged: false,
      options: ["low_stock"],
    };
    const [gap] = describeStep(step({ step: "scope", status: "blocked", facts }), 0);
    expect(gap).toMatchObject({ key: "scopeUnbound", values: { count: 2, items: "not packaging, cold chain" } });

    // a display label wins when the server sends one; with neither label nor words, the value
    const [labelled] = describeStep(
      step({ step: "scope", status: "blocked", facts: { unbound: [{ key: "category:sku", value: "packaging", label: "Packaging" }, { key: "field:inventory.warehouse", value: "WH-9" }] } }),
      0,
    );
    expect(labelled?.values.items).toBe("Packaging, WH-9");
  });

  it("reads exclusions and lists in the caller's language", () => {
    const zh = { not: (item: string) => `非${item}`, join: (items: readonly string[]) => items.join("、") };
    const facts = {
      verdict: "unfiltered",
      acknowledged: true,
      mentioned: [
        { key: "category:sku", value: "packaging", negated: true, text: "包装" },
        { key: "field:inventory.warehouse", value: "WH-EAST", text: "WH EAST" },
      ],
      unbound: [
        { key: "category:sku", value: "packaging", negated: true },
        { key: "field:inventory.warehouse", value: "WH-EAST" },
      ],
    };
    const [line] = describeStep(step({ step: "scope", facts }), 0, zh);
    expect(line).toMatchObject({ key: "scopeUnfiltered", values: { count: 2, items: "非包装、WH EAST" }, tone: "neutral" });
  });

  it("reports a failed scope check as an error, whatever else the facts say", () => {
    const [line] = describeStep(step({ step: "scope", status: "error", facts: { verdict: "conflict", conflicts: [{ key: "k", value: "B" }], error: "boom" } }), 0);
    expect(line).toMatchObject({ key: "scopeError", tone: "error", note: "boom" });
  });

  it("accepts counts or objects instead of plain lists", () => {
    const [counted] = describeStep(step({ step: "scope", facts: { mentioned: 3, bound: 3, unbound: 0 } }), 0);
    expect(counted).toMatchObject({ key: "scopeOk", values: { count: 3 } });
    const [objects] = describeStep(
      step({ step: "scope", facts: { unbound: [{ field: "warehouse", value: "WH-9" }, { id: 7 }] } }),
      0,
    );
    expect(objects).toMatchObject({ key: "scopeUnbound", values: { count: 2, items: "WH-9, 7" } });
    const [none] = describeStep(step({ step: "scope", facts: {} }), 0);
    expect(none?.key).toBe("scopeNone");
  });
});

describe("steps this build does not know", () => {
  it("are shown verbatim with their status and plain facts", () => {
    const [line] = describeStep(
      step({ step: "teleport", status: "blocked", source: "rule", facts: { reason: "too far", hops: 3, nested: { a: 1 } } }),
      4,
    );
    expect(line).toMatchObject({
      key: "unknown",
      values: { step: "teleport", status: "blocked" },
      tone: "warn",
      source: "rule",
      note: "reason=too far, hops=3",
    });
  });

  it("never drop a step from the replay", () => {
    const steps = [
      step({ step: "candidates", facts: { count: 3 } }),
      step({ step: "understand", source: "fallback" as TraceStep["source"], facts: { kind: "chat" } }),
      step({ step: "scope" }),
      step({ step: "teleport" }),
      step({ step: "respond", facts: { rows: 0 } }),
    ];
    expect(describeTrace(steps).map((l) => l.key)).toEqual(["candidates", "routeChat", "scopeNone", "unknown", "respondOk"]);
  });
});
