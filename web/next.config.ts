import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const isDev = process.env.NODE_ENV !== "production";

/** The API origin the browser may talk to; mirrors src/lib/config.ts (same default). */
function apiOrigin(): string | null {
  const raw = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim() || "http://localhost:8000";
  if (raw.startsWith("/")) return null; // same origin (reverse proxy): 'self' covers it
  try {
    return new URL(raw).origin;
  } catch {
    throw new Error(`NEXT_PUBLIC_API_BASE is not a URL: ${raw}`);
  }
}

/**
 * Static CSP (no third-party origins at all). Scripts: 'self' plus 'unsafe-inline', which the
 * App Router's inline RSC bootstrap and the no-flash theme script need without per-request
 * nonces; 'unsafe-eval' only for the dev server's refresh runtime. Fonts are self-hosted by
 * next/font, so font-src is 'self'.
 */
function contentSecurityPolicy(): string {
  const api = apiOrigin();
  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src 'self'${api ? ` ${api}` : ""}`,
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
}

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy() },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
  },
  { key: "X-DNS-Prefetch-Control", value: "off" },
  ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: true,
  // Set by web/Dockerfile only. `next start`, which `pnpm start` and the e2e suite use, warns
  // that it does not support this output, so local builds keep the default.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // The console uses no next/image, so sharp is unneeded; its LGPL libvips stays out of the image.
  images: { unoptimized: true },
  outputFileTracingExcludes: {
    "*": ["./node_modules/.pnpm/sharp@*/**/*", "./node_modules/.pnpm/@img+*/**/*"],
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
