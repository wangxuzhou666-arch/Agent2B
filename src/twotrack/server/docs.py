"""Interactive API docs, served in demo mode only.

Outside demo mode the frozen ``openapi.json`` in the repository is the contract and nothing
serves it. In demo mode ``/openapi.json`` (the live schema) and ``/docs`` (Swagger UI) help a
visitor try the API with a demo token. The page is the only response that may run a script,
so its Content-Security-Policy is scoped to that one path and pins exactly what it loads: the
two Swagger UI assets by URL and its one inline script by hash.
"""

import base64
import hashlib
import re

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

DOCS_PATH = "/docs"
SCHEMA_PATH = "/openapi.json"
SWAGGER_JS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
SWAGGER_CSS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
# a blank icon, so the page makes no request to any third-party host for one
FAVICON = "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'/%3E"
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def docs_page(title: str) -> tuple[bytes, str]:
    """The Swagger UI page and the Content-Security-Policy that lets exactly it render."""
    page = get_swagger_ui_html(
        openapi_url=SCHEMA_PATH, title=title, swagger_js_url=SWAGGER_JS,
        swagger_css_url=SWAGGER_CSS, swagger_favicon_url=FAVICON,
        swagger_ui_parameters={"validatorUrl": None})  # no third-party validator badge
    body = bytes(page.body)
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
        for script in _INLINE_SCRIPT.findall(body.decode()))
    policy = ("default-src 'none'; "
              f"script-src {SWAGGER_JS} {hashes}; "
              # Swagger UI sets inline styles on the elements it renders
              f"style-src {SWAGGER_CSS} 'unsafe-inline'; "
              "img-src 'self' data:; connect-src 'self'; "
              "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    return body, policy


def install_docs(app: FastAPI, body: bytes) -> None:
    """``GET /docs``. ``/openapi.json`` itself comes from FastAPI's ``openapi_url``."""

    @app.get(DOCS_PATH, include_in_schema=False)
    def swagger_ui() -> HTMLResponse:
        return HTMLResponse(body)
