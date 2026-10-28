import { describe, expect, it } from "vitest";

import en from "../messages/en.json";
import zh from "../messages/zh.json";

function keys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
}

describe("messages", () => {
  it("en and zh define exactly the same keys", () => {
    expect(keys(zh).sort()).toEqual(keys(en).sort());
  });

  it("has no empty strings", () => {
    for (const catalog of [en, zh]) {
      for (const key of keys(catalog)) {
        const value = key.split(".").reduce<unknown>((node, part) => (node as Record<string, unknown>)[part], catalog);
        expect(typeof value === "string" && value.trim().length > 0, key).toBe(true);
      }
    }
  });
});
