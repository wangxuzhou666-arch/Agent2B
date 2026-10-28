"""Refuse a request whose ``Host`` header names a host this server was not set up to answer to.

Why: DNS rebinding. A page on ``rebind.attacker.example`` can make that name resolve to
127.0.0.1 after it has loaded; the browser then treats requests from the page to
``rebind.attacker.example:8000`` as same-origin, so neither CORS nor the missing cookie stops
them, and the page can use whatever the API hands to anyone who can reach it: in demo mode, an
admin token. The one thing the page cannot change is the name the browser puts in ``Host``,
so the server checks that.

Accepted: ``localhost``, ``127.0.0.1`` and ``[::1]`` on any port, always (a health check or a
``curl`` on the machine itself has no other name for it), plus the names in
``TWOTRACK_ALLOWED_HOSTS``. The check is on in demo mode, and in every mode once that setting
is set. A missing, repeated or malformed Host header is refused too.
"""

import re
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from twotrack.settings import ALLOWED_HOSTS_ENV, HOST_PATTERN

from .errors import envelope

LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "[::1]"})
_HOST_HEADER = re.compile(rf"({HOST_PATTERN})(?::[0-9]{{1,5}})?")
_REFUSED = ("This server does not answer to the Host name the request used. "
            f"To allow a name, list it in {ALLOWED_HOSTS_ENV}.")


def host_of(header: str) -> str | None:
    """The host in a Host header value, lower-cased and without its port; None when the value
    is not exactly ``host`` or ``host:port``."""
    match = _HOST_HEADER.fullmatch(header.lower())
    return match.group(1) if match else None


class HostCheckMiddleware:
    """Pure ASGI, like the other middleware (``BaseHTTPMiddleware`` would buffer the SSE
    stream). Not Starlette's ``TrustedHostMiddleware``: that answers in plain text where every
    error here is the JSON envelope, and it reads the first of several Host headers. Only HTTP
    is checked; the API has no WebSocket route."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed = LOOPBACK_NAMES | frozenset(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = Headers(scope=scope).getlist("host")
        # two Host headers are two answers to "which host?": refuse rather than pick one
        host = host_of(values[0]) if len(values) == 1 else None
        if host not in self.allowed:
            await envelope(400, "invalid_host", _REFUSED)(scope, receive, send)
            return
        await self.app(scope, receive, send)
