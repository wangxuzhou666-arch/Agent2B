"""Response headers every API answer carries (also used by the error handlers)."""

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # API responses are data, never documents: nothing may load or frame from them.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    # authenticated data must not sit in a shared or browser cache
    "Cache-Control": "no-store",
}
