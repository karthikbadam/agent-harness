"""Session cookie exchange.

``POST /api/session`` — authenticate with the bearer token (or an existing valid
cookie) and receive an ``HttpOnly`` ``ah_session`` cookie. Browsers use this so
the token never appears in a URL (SSE/EventSource can't set headers and would
otherwise need ``?token=``).

``DELETE /api/session`` — clear the cookie (log out on this device).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ..auth import require_auth
from ..config import get_settings
from ..schemas import AuthInfo
from ..session import COOKIE_NAME, DEFAULT_TTL_SECONDS, mint

router = APIRouter(prefix="/api/session", tags=["session"])


def _is_https(request: Request) -> bool:
    # Behind Tailscale Serve the app sees http on loopback but the client used
    # https; Serve forwards the original scheme. Honor it so we set Secure in
    # prod while staying usable over plain http in local dev.
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"


@router.post("", dependencies=[Depends(require_auth)])
async def create_session(request: Request, response: Response) -> AuthInfo:
    token = get_settings().auth_token
    assert token is not None  # require_auth already rejected the unconfigured case
    response.set_cookie(
        key=COOKIE_NAME,
        value=mint(token),
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        secure=_is_https(request),
        samesite="strict",
        path="/",
    )
    return AuthInfo()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(response: Response) -> None:
    # No auth required: clearing your own cookie is always safe.
    response.delete_cookie(key=COOKIE_NAME, path="/")
