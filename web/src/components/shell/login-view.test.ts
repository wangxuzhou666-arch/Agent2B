import { describe, expect, it } from "vitest";

import { safeNext } from "./login-view";

const ORIGIN = "https://console.example";

describe("safeNext", () => {
  it("follows same-origin console paths", () => {
    expect(safeNext("/audit", ORIGIN)).toBe("/audit");
    expect(safeNext("/data", ORIGIN)).toBe("/data");
    expect(safeNext(null, ORIGIN)).toBe("/hub");
    expect(safeNext("", ORIGIN)).toBe("/hub");
  });

  it("keeps a deep link's query and hash", () => {
    expect(safeNext("/audit?id=42&request_id=r1#trace", ORIGIN)).toBe("/audit?id=42&request_id=r1#trace");
    expect(safeNext("/data?entity=sku&category=packaging&f.sku_id=SKU-1", ORIGIN)).toBe(
      "/data?entity=sku&category=packaging&f.sku_id=SKU-1",
    );
    // the same deep link as an absolute URL on this origin comes back as a path
    expect(safeNext(`${ORIGIN}/audit?id=42#trace`, ORIGIN)).toBe("/audit?id=42#trace");
  });

  it("refuses absolute and protocol-relative URLs to another origin", () => {
    expect(safeNext("https://evil.example", ORIGIN)).toBe("/hub");
    expect(safeNext("https://evil.example/hub", ORIGIN)).toBe("/hub");
    expect(safeNext("http://console.example/audit", ORIGIN)).toBe("/hub"); // another scheme is another origin
    expect(safeNext("https://console.example:8443/audit", ORIGIN)).toBe("/hub");
    expect(safeNext("//evil.example", ORIGIN)).toBe("/hub");
    expect(safeNext("//evil.example/hub", ORIGIN)).toBe("/hub");
    expect(safeNext(" //evil.example", ORIGIN)).toBe("/hub"); // leading spaces are stripped by the parser
    expect(safeNext("javascript:alert(1)", ORIGIN)).toBe("/hub");
    expect(safeNext("data:text/html,hi", ORIGIN)).toBe("/hub");
  });

  it("refuses control characters the URL parser would strip (%09, %0A, %0D) and NUL (%00)", () => {
    // `next` arrives decoded: /login?next=/%09/evil.example gives "/\t/evil.example"
    for (const encoded of ["%09", "%0A", "%0D", "%00"]) {
      const raw = new URLSearchParams(`next=/${encoded}/evil.example`).get("next");
      expect(raw).toBe(`/${decodeURIComponent(encoded)}/evil.example`);
      expect(safeNext(raw, ORIGIN), encoded).toBe("/hub");
    }
    // the reason: without the check, the browser would resolve it to the other host
    expect(new URL("/\t/evil.example", ORIGIN).origin).toBe("https://evil.example");
    expect(safeNext("/\u001F/hub", ORIGIN)).toBe("/hub");
    expect(safeNext("/hub\u007F", ORIGIN)).toBe("/hub");
    expect(safeNext("/audit?id=1\n", ORIGIN)).toBe("/hub");
  });

  it("refuses backslashes, which the parser reads as slashes", () => {
    expect(safeNext("/\\evil.example", ORIGIN)).toBe("/hub");
    expect(safeNext("\\\\evil.example", ORIGIN)).toBe("/hub");
    expect(safeNext("\\/evil.example", ORIGIN)).toBe("/hub");
    expect(safeNext("/audit\\..\\hub", ORIGIN)).toBe("/hub");
  });

  it("refuses a path that only becomes protocol-relative after parsing", () => {
    expect(new URL("/.//evil.example", ORIGIN).pathname).toBe("//evil.example");
    expect(safeNext("/.//evil.example", ORIGIN)).toBe("/hub");
    expect(safeNext("/x/..//evil.example", ORIGIN)).toBe("/hub");
  });

  it("never sends a signed-in user back to /login", () => {
    expect(safeNext("/login", ORIGIN)).toBe("/hub");
    expect(safeNext("/login?next=/hub", ORIGIN)).toBe("/hub");
    expect(safeNext("/./login", ORIGIN)).toBe("/hub");
    expect(safeNext(`${ORIGIN}/login#x`, ORIGIN)).toBe("/hub");
  });
});
