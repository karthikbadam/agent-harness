"""Single bearer-token auth.

The token is generated at install time (`agent-harness gen-token`) and stored
in `config.toml`. Clients send it as `Authorization: Bearer <token>` for
normal endpoints, or as `?token=<token>` for SSE (since EventSource can't set
headers).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Query, status

from .config import get_settings


async def require_auth(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    expected = get_settings().auth_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth_token not configured; run agent-harness gen-token",
        )
    provided: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
    elif token:
        provided = token
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")
