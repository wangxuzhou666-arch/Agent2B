"""FastAPI dependencies: the runtime, the signed-in session, the reply language and role
checks."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from twotrack.core import Principal
from twotrack.core.i18n import Language, language_of_request
from twotrack.runtime import Runtime

from .auth import DEMO_USERS, TokenError
from .errors import ApiError

_bearer = HTTPBearer(auto_error=False, description="Token from /api/auth/login or /api/auth/demo")


@dataclass(frozen=True)
class Session:
    principal: Principal
    demo: bool
    expires_at: datetime
    token_id: str = ""  # the ``jti`` of the token this request came with (sign-out revokes it)

    @property
    def user_id(self) -> str:
        return self.principal.user_id


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise ApiError(503, "unavailable", "The server has no runtime (schema-only app).")
    return runtime


def reply_language(request: Request) -> Language:
    """The language this REST request wants its labels and messages in: ``zh`` when
    ``Accept-Language`` asks for Chinese first (``zh``, ``zh-CN``, ...), else ``en``, a missing
    header included. The query endpoint does not use it: it follows the question's language."""
    return language_of_request(request.headers.get("accept-language"))


def _unauthenticated(code: str, message: str) -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


def authenticated(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    runtime: Runtime = Depends(get_runtime),
) -> Session:
    if credentials is None:
        raise _unauthenticated("not_authenticated", "Sign in to use this endpoint.")
    try:
        claims = runtime.tokens.verify(credentials.credentials)
    except TokenError as exc:
        raise _unauthenticated(exc.code, exc.message) from None
    if claims.demo:
        principal = DEMO_USERS.get(claims.user_id) if runtime.settings.demo_mode else None
        if principal is None:
            raise _unauthenticated("invalid_token", "The token is not valid.")
        return Session(principal, True, claims.expires_at, claims.token_id)
    user = runtime.users.get(claims.user_id)
    if user is None or user.disabled:
        raise _unauthenticated("invalid_token", "The token is not valid.")
    return Session(user.principal, False, claims.expires_at, claims.token_id)


def require_roles(*roles: str) -> Callable[[Session], Session]:
    """Dependency factory. Admin passes every list (``Principal.can_access``)."""
    def check(session: Session = Depends(authenticated)) -> Session:
        if not session.principal.can_access(roles):
            raise ApiError(403, "forbidden", "Your role may not use this endpoint.")
        return session
    return check
