"""Pure-ASGI middleware: security headers and the request body cap.

They are plain ASGI callables rather than ``BaseHTTPMiddleware`` subclasses because that base
class buffers the response, which would break the SSE stream.
"""

from collections.abc import Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import envelope
from .headers import SECURITY_HEADERS

UPLOAD_PATH_SUFFIX = "/import/preview"
MULTIPART_OVERHEAD = 64 * 1024  # boundaries and part headers around the file bytes
BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SecurityHeadersMiddleware:
    """``SECURITY_HEADERS`` on every response. ``path_overrides`` replaces some of them for an
    exact path only (the demo-mode docs page needs a CSP that lets its page script run)."""

    def __init__(self, app: ASGIApp,
                 path_overrides: Mapping[str, Mapping[str, str]] | None = None) -> None:
        self.app = app
        self.overrides = {path: {**SECURITY_HEADERS, **headers}
                          for path, headers in (path_overrides or {}).items()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chosen = self.overrides.get(scope["path"], SECURITY_HEADERS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in chosen.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """Refuse oversize request bodies before the application reads them.

    A declared ``Content-Length`` over the limit is answered at once. A body without one
    (chunked) is counted as it is read and cut off at the limit, so a client cannot dodge the
    cap by not announcing the size. Only the upload route gets the larger upload limit.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, max_upload_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_upload_bytes = max_upload_bytes

    def _limit(self, path: str) -> int:
        if path.endswith(UPLOAD_PATH_SUFFIX):
            return self.max_upload_bytes + MULTIPART_OVERHEAD
        return self.max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in BODYLESS_METHODS:
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope["path"])
        too_large = HTTPException(413, f"The request body is larger than {limit} bytes.")
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and not (declared.isascii() and declared.isdigit()):
            await envelope(400, "bad_request", "Invalid Content-Length.")(scope, receive, send)
            return
        if declared is not None and int(declared) > limit:
            await envelope(413, "payload_too_large", too_large.detail)(scope, receive, send)
            return

        seen = 0

        async def counted_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # an HTTPException is what FastAPI's body parsing re-raises untouched;
                    # the shared handler then renders the 413 envelope
                    raise too_large
            return message

        await self.app(scope, counted_receive, send)
