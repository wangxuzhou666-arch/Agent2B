"""Health, sign-in and sign-out."""

from fastapi import APIRouter, Depends, Request

from twotrack import __version__
from twotrack.core import Role
from twotrack.providers.base import provider_info
from twotrack.runtime import Runtime

from ..auth import DEMO_USERS, demo_user_id
from ..deps import Session, authenticated, get_runtime
from ..errors import ApiError, errors, not_found
from ..schemas import (
    DemoRequest,
    HealthOut,
    LoginRequest,
    LogoutOut,
    MeOut,
    TokenOut,
    UploadLimits,
    UserOut,
)

router = APIRouter()


def _sorted_roles(roles: frozenset[Role]) -> list[Role]:
    return sorted(roles, key=lambda r: r.value)


@router.get("/health", response_model=HealthOut, tags=["system"])
def health(runtime: Runtime = Depends(get_runtime)) -> HealthOut:
    """Liveness for load balancers and the console's banner. Needs no sign-in and reveals
    nothing beyond the version, whether the instance is a demo, which language model answers
    (its name and model id; never an endpoint or a key) and the import limits."""
    with runtime.database.read() as conn:
        conn.execute("SELECT 1")
    limits = runtime.importer.limits  # the upload guard's own settings
    return HealthOut(status="ok", version=__version__, demo_mode=runtime.settings.demo_mode,
                     provider=provider_info(runtime.provider),
                     limits=UploadLimits(max_upload_bytes=limits.max_bytes,
                                         max_rows=limits.max_rows))


def _client_key(request: Request, username: str) -> str:
    # The address of the peer that connected. Behind a reverse proxy that is the proxy, for
    # every client at once, unless uvicorn is told to trust its X-Forwarded-For
    # (FORWARDED_ALLOW_IPS names the proxy's address; it defaults to loopback only).
    host = request.client.host if request.client else "unknown"
    return f"{host}|{username}"


@router.post("/auth/login", response_model=TokenOut, tags=["auth"],
             responses=errors(401, 422, 429))
def login(body: LoginRequest, request: Request,
          runtime: Runtime = Depends(get_runtime)) -> TokenOut:
    throttle = request.app.state.throttle
    key = _client_key(request, body.username)
    # books the attempt as a failure before the password is hashed: checking now and recording
    # afterwards would hash every request of a parallel burst
    wait = throttle.reserve(key)
    if wait > 0:
        raise ApiError(429, "too_many_attempts", "Too many failed sign-ins; try again later.",
                       headers={"Retry-After": str(int(wait) + 1)})
    user = runtime.users.authenticate(body.username, body.password)
    if user is None:
        # one message for unknown user, wrong password and disabled account; the booked
        # attempt stays on the record as this failure
        raise ApiError(401, "invalid_credentials", "Invalid username or password.")
    throttle.clear(key)
    token, expires = runtime.tokens.issue(user.user_id)
    return TokenOut(access_token=token, expires_at=expires,
                    demo_mode=runtime.settings.demo_mode,
                    user=UserOut(user_id=user.user_id, roles=list(user.roles)))


@router.post("/auth/demo", response_model=TokenOut, tags=["auth"],
             responses=errors(404, 422))
def demo_login(body: DemoRequest, runtime: Runtime = Depends(get_runtime)) -> TokenOut:
    """Passwordless sign-in as the built-in demo user of a role. Exists only when the server
    runs with ``TWOTRACK_DEMO_MODE=1``; otherwise the route answers 404 as if it were absent."""
    if not runtime.settings.demo_mode:
        raise not_found("This endpoint")
    user_id = demo_user_id(body.role)
    token, expires = runtime.tokens.issue(user_id, demo=True)
    return TokenOut(access_token=token, expires_at=expires, demo_mode=True,
                    user=UserOut(user_id=user_id, roles=_sorted_roles(DEMO_USERS[user_id].roles)))


@router.get("/auth/me", response_model=MeOut, tags=["auth"], responses=errors(401))
def me(session: Session = Depends(authenticated),
       runtime: Runtime = Depends(get_runtime)) -> MeOut:
    return MeOut(user_id=session.user_id, roles=_sorted_roles(session.principal.roles),
                 demo_mode=runtime.settings.demo_mode, expires_at=session.expires_at)


@router.post("/auth/logout", response_model=LogoutOut, tags=["auth"], responses=errors(401))
def logout(session: Session = Depends(authenticated),
           runtime: Runtime = Depends(get_runtime)) -> LogoutOut:
    """Sign out: revoke the token this request was made with.

    The token is refused from now on with `401 token_revoked`, on this server and after a
    restart (its id goes on a deny-list kept until the token would have expired anyway). Other
    tokens of the same user, on other devices, keep working. Signing out twice with the same
    token answers 401 the second time: the token is already dead.
    """
    runtime.tokens.revoke(session.token_id, session.expires_at)
    return LogoutOut(status="signed_out")
