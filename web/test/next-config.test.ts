// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The headers next.config.ts sends on every path, by name. The config reads NODE_ENV and
 * NEXT_PUBLIC_API_BASE when it is loaded, so each case sets them and loads it afresh.
 */
async function headersFor(nodeEnv: "development" | "production", apiBase?: string) {
  vi.stubEnv("NODE_ENV", nodeEnv);
  vi.stubEnv("NEXT_PUBLIC_API_BASE", apiBase);
  vi.resetModules();
  const { default: config } = await import("../next.config");
  const rules = (await config.headers?.()) ?? [];
  expect(rules.map((rule) => rule.source)).toEqual(["/:path*"]);
  const headers = rules[0]?.headers ?? [];
  const byName: Record<string, string> = Object.fromEntries(headers.map(({ key, value }) => [key, value]));
  expect(Object.keys(byName)).toHaveLength(headers.length); // no header sent twice
  return byName;
}

/** "default-src 'self'; img-src 'self' data:" → { "default-src": "'self'", "img-src": "'self' data:" } */
function directives(csp: string | undefined) {
  const entries = (csp ?? "")
    .split(";")
    .map((part) => part.trim().split(/\s+/))
    .filter(([name]) => name)
    .map(([name, ...sources]) => [name, sources.join(" ")]);
  const byName: Record<string, string> = Object.fromEntries(entries);
  expect(Object.keys(byName)).toHaveLength(entries.length); // no directive given twice
  return byName;
}

/** Directives that do not depend on the environment. */
const CSP = {
  "default-src": "'self'",
  "style-src": "'self' 'unsafe-inline'",
  "img-src": "'self' data: blob:",
  "font-src": "'self'",
  "worker-src": "'self' blob:",
  "manifest-src": "'self'",
  "frame-src": "'none'",
  "frame-ancestors": "'none'",
  "object-src": "'none'",
  "base-uri": "'none'",
  "form-action": "'self'",
};

/** Headers besides the CSP that every environment sends. */
const HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
  "X-DNS-Prefetch-Control": "off",
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("next.config security headers", () => {
  it("in development: eval for the dev server's refresh runtime, the default API origin, no HSTS", async () => {
    const { "Content-Security-Policy": csp, ...rest } = await headersFor("development");
    expect(directives(csp)).toEqual({
      ...CSP,
      "script-src": "'self' 'unsafe-inline' 'unsafe-eval'",
      "connect-src": "'self' http://localhost:8000",
    });
    expect(rest).toEqual(HEADERS);
  });

  it("in production: no eval, connect-src is 'self' plus the API's origin, and HSTS", async () => {
    const { "Content-Security-Policy": csp, ...rest } = await headersFor(
      "production",
      "https://api.example.test:8443/v1",
    );
    expect(directives(csp)).toEqual({
      ...CSP,
      "script-src": "'self' 'unsafe-inline'",
      "connect-src": "'self' https://api.example.test:8443",
    });
    expect(rest).toEqual({ ...HEADERS, "Strict-Transport-Security": "max-age=63072000; includeSubDomains" });
  });

  it("an API behind the same origin adds nothing to connect-src", async () => {
    const headers = await headersFor("production", "/api");
    expect(directives(headers["Content-Security-Policy"])["connect-src"]).toBe("'self'");
  });

  it("refuses an API base that is not a URL", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "not a url");
    vi.resetModules();
    await expect(import("../next.config")).rejects.toThrow("NEXT_PUBLIC_API_BASE is not a URL: not a url");
  });
});
