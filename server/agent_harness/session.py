"""Stateless signed session cookies.

Browsers can't set an ``Authorization`` header on ``EventSource`` (SSE), and we
don't want the bearer token riding in URL query strings where it lands in access
logs and browser history. Instead the client exchanges the bearer token once
(``POST /api/session``) for an ``HttpOnly`` cookie that authenticates subsequent
requests — including SSE, which sends same-origin cookies automatically.

The cookie is a stateless HMAC token: ``<expiry>.<base64url(sig)>`` where the
signature is keyed by the *auth token itself*. That gives two nice properties
with zero server-side storage:

- Rotating the auth token (``agent-harness gen-token --force``) invalidates every
  outstanding cookie immediately.
- A cookie can't be forged without knowing the auth token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

COOKIE_NAME = "ah_session"
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _sign(expiry: int, auth_token: str) -> str:
    key = auth_token.encode("utf-8")
    sig = hmac.new(key, str(expiry).encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")


def mint(auth_token: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed cookie value valid for ``ttl_seconds``."""
    expiry = int(time.time()) + ttl_seconds
    return f"{expiry}.{_sign(expiry, auth_token)}"


def verify(value: str | None, auth_token: str) -> bool:
    """Return True iff ``value`` is a valid, unexpired cookie for ``auth_token``."""
    if not value or "." not in value:
        return False
    expiry_str, sig = value.split(".", 1)
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    return secrets.compare_digest(sig, _sign(expiry, auth_token))
