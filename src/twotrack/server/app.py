"""``create_app``: the FastAPI application and its cross-cutting security.

Middleware, outermost first: security headers on every response (including errors and
preflights) -> the Host check (demo mode, or once ``TWOTRACK_ALLOWED_HOSTS`` is set; see
``hosts``) -> CORS for the configured origins only -> request body cap -> routes. Sign-in
is a bearer token in the ``Authorization`` header, never a cookie, so there is no ambient
credential for another site to ride on. A page that rebinds its own name to 127.0.0.1 is
same-origin with the server and CORS does not apply to it; the Host check is what refuses it
(it matters most in demo mode, where signing in needs no password).

``create_app(None)`` builds a schema-only app (no database, no secrets): the OpenAPI export
uses it. Interactive docs (``/docs``, ``/openapi.json``) are served in demo mode only; anywhere
else the frozen ``openapi.json`` in the repository is the contract.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from twotrack import __version__
from twotrack.runtime import Runtime, build_runtime
from twotrack.settings import Settings

from .auth import LoginThrottle
from .docs import DOCS_PATH, SCHEMA_PATH, docs_page, install_docs
from .errors import install_error_handlers
from .hosts import HostCheckMiddleware
from .middleware import BodyLimitMiddleware, SecurityHeadersMiddleware
from .routes import approvals, audit, entities, imports, query, session

DESCRIPTION = """\
Supply-chain agent console API. LLMs route and narrate; code computes.

Every endpoint except `/api/health`, `/api/auth/login` and `/api/auth/demo` needs
`Authorization: Bearer <token>`; `POST /api/auth/logout` revokes the token it is called with.
Errors share one shape: `{"error": {"code", "message"}}`.

Language. Labels and messages come in English and Chinese. `POST /api/query` follows the
language of the question. Every other endpoint that words something follows `Accept-Language`:
when it asks for `zh` (or `zh-CN` ...) first the text is Chinese; anything else, and no header
at all, gives English. Endpoints that return rows return ids and values, never labels; the
labels of entities and fields come from `GET /api/entities`.
"""


def _operation_id(route: APIRoute) -> str:
    return route.name  # function names are unique; the generated client uses them as-is


def create_app(source: Settings | Runtime | None = None) -> FastAPI:
    owns_runtime = isinstance(source, Settings)
    runtime = build_runtime(source) if isinstance(source, Settings) else source
    settings = runtime.settings if runtime else Settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_runtime and runtime is not None:
            runtime.close()

    docs = runtime is not None and settings.demo_mode
    app = FastAPI(
        title="twotrack API", version=__version__, description=DESCRIPTION, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=SCHEMA_PATH if docs else None,
        generate_unique_id_function=_operation_id)
    app.state.runtime = runtime
    app.state.throttle = LoginThrottle()
    install_error_handlers(app)
    overrides: dict[str, dict[str, str]] = {}
    if docs:
        page, policy = docs_page(f"{app.title} (demo)")
        install_docs(app, page)
        overrides[DOCS_PATH] = {"Content-Security-Policy": policy}

    # added innermost first: each add_middleware wraps everything added before it
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=settings.max_body_bytes,
                       max_upload_bytes=settings.max_upload_bytes)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Accept-Language"],
        max_age=600)
    if settings.demo_mode or settings.allowed_hosts:
        app.add_middleware(HostCheckMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware, path_overrides=overrides)

    for module in (session, query, approvals, audit, entities, imports):
        app.include_router(module.router, prefix="/api")
    return app
