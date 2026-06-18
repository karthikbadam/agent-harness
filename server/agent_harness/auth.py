"""Single bearer-token auth, with a signed-cookie path for browsers.

The token is generated at install time (`agent-harness gen-token`) and stored
in `config.toml`. Clients authenticate one of two ways:

- ``Authorization: Bearer <token>`` — scripts, curl, the SPA's fetch calls, MCP.
- ``ah_session`` cookie — minted by ``POST /api/session`` after a one-time bearer
  exchange. Used by the browser (and especially SSE/EventSource, which can't set
  headers) so the token never rides in a URL query string where it would leak
  into access logs and history. See ``session.py``.

Repeated auth failures from one client are throttled to blunt probing.
"""

from __future__ import annotations

import secrets
import time

from fastapi import Cookie, Header, HTTPException, Request, status

from .config import get_settings
from .session import verify as verify_cookie

# Sliding-window throttle on failed auth attempts, keyed by client IP. The
# 192-bit token makes brute force infeasible anyway; this mostly trims log noise
# and slows blind probing. In-memory and best-effort — fine for a single-user
# service behind Tailscale.
_FAIL_WINDOW_SECONDS = 60.0
_FAIL_MAX = 20
_failures: dict[str, list[float]] = {}


def reset_throttle() -> None:
    """Clear the failure counters (used by tests for isolation)."""
    _failures.clear()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _recent(ip: str) -> list[float]:
    now = time.monotonic()
    bucket = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW_SECONDS]
    _failures[ip] = bucket
    return bucket


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    ah_session: str | None = Cookie(default=None),
) -> None:
    expected = get_settings().auth_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth_token not configured; run agent-harness gen-token",
        )

    ip = _client_ip(request)
    if len(_recent(ip)) >= _FAIL_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many auth failures; slow down",
            headers={"Retry-After": "60"},
        )

    # Bearer header.
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
        if secrets.compare_digest(provided, expected):
            _failures.pop(ip, None)
            return

    # Session cookie (browser / SSE).
    if verify_cookie(ah_session, expected):
        _failures.pop(ip, None)
        return

    _failures.setdefault(ip, []).append(time.monotonic())
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")
