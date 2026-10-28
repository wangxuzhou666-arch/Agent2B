"""One error shape for every non-2xx response: ``{"error": {"code", "message"}}``.

``code`` is stable and machine-readable (the console switches on it); ``message`` is for people
and never echoes request values, tokens or stack traces. Validation errors list the field path
and the rule that failed, not the offending input, because the input may be a secret.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from twotrack.core.guards import UploadRejected
from twotrack.core.i18n import language_of_request
from twotrack.core.store import StoreError

from .headers import SECURITY_HEADERS

log = logging.getLogger("twotrack.server")

# UploadRejected.code -> HTTP status; anything not listed is a plain 400.
UPLOAD_STATUS = {"forbidden": 403, "too_large": 413, "digest_mismatch": 409,
                 "grant_invalid": 409}


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} was not found")


def envelope(status: int, code: str, message: str,
             headers: dict[str, str] | None = None) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status,
                        headers={**SECURITY_HEADERS, **(headers or {})})


def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return envelope(exc.status, exc.code, exc.message, exc.headers)


_HTTP_CODES = {400: "bad_request", 401: "not_authenticated", 403: "forbidden",
               404: "not_found", 405: "method_not_allowed", 413: "payload_too_large",
               415: "unsupported_media_type", 429: "too_many_requests"}


def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
    return envelope(exc.status_code, code, message, dict(exc.headers or {}))


def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return envelope(422, "validation_error", "; ".join(problems)[:500])


def _upload_rejected(request: Request, exc: UploadRejected) -> JSONResponse:
    # a file-level import problem is worded in the language the request asks for
    language = language_of_request(request.headers.get("accept-language"))
    return envelope(UPLOAD_STATUS.get(exc.code, 400), exc.code, exc.text(language))


def _store_error(_: Request, exc: StoreError) -> JSONResponse:
    # store messages name fields and rules, never values (see entities.values)
    return envelope(400, "invalid_request", str(exc).splitlines()[0][:300])


def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    # type only: the message may carry row values or SQL
    log.error("unhandled %s while serving a request", type(exc).__name__, exc_info=exc)
    return envelope(500, "internal_error", "The server could not complete the request.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(UploadRejected, _upload_rejected)
    app.add_exception_handler(StoreError, _store_error)
    app.add_exception_handler(Exception, _unexpected)


_DESCRIPTIONS = {
    400: "The request is malformed or names something that does not exist",
    401: "Missing, invalid or expired token",
    403: "The caller's role may not do this",
    404: "Not found",
    409: "Conflicts with the current state",
    410: "The confirmation has expired",
    413: "Body or upload too large",
    422: "Request validation failed",
    429: "Too many attempts",
    503: "The server could not complete the request right now",
}


def errors(*statuses: int) -> dict[int | str, dict]:
    """``responses=`` for a route: documents the error envelope for the given statuses."""
    return {s: {"model": ErrorEnvelope, "description": _DESCRIPTIONS[s]} for s in statuses}
