"""Web Push sender using pywebpush.

Single-user, LAN-only. Subscriptions live in the `push_subscriptions` table.
Failed subscriptions (HTTP 404/410) are auto-pruned. Never raises out of
`send_to_all` — push failures must not break job/turn flow.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable

from pywebpush import WebPushException, webpush
from sqlalchemy import select

from . import models
from .config import get_settings
from .db import session_scope

log = logging.getLogger(__name__)


@dataclass
class PushPayload:
    title: str
    body: str
    job_id: str | None = None
    url: str = "/jobs"
    tag: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "body": self.body,
                "job_id": self.job_id,
                "url": self.url,
                "tag": self.tag or (f"job:{self.job_id}" if self.job_id else "agent-harness"),
            }
        )


class Notifier:
    def __init__(self) -> None:
        self._enabled: bool | None = None

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            s = get_settings()
            self._enabled = bool(s.vapid_private_key and s.vapid_public_key)
        return self._enabled

    def _subscriptions(self) -> list[tuple[str, str, str, str]]:
        with session_scope() as s:
            rows = s.execute(select(models.PushSubscription)).scalars().all()
            return [(r.id, r.endpoint, r.p256dh, r.auth) for r in rows]

    def _prune(self, sub_id: str) -> None:
        with session_scope() as s:
            row = s.get(models.PushSubscription, sub_id)
            if row is not None:
                s.delete(row)

    def send_to_all(self, payload: PushPayload) -> int:
        """Send to every subscription. Returns count of successes. Never raises."""
        if not self.enabled:
            return 0
        settings = get_settings()
        sent = 0
        for sub_id, endpoint, p256dh, auth in self._subscriptions():
            try:
                webpush(
                    subscription_info={
                        "endpoint": endpoint,
                        "keys": {"p256dh": p256dh, "auth": auth},
                    },
                    data=payload.to_json(),
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject},
                    ttl=60,
                )
                sent += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    log.info("pruning dead push subscription %s (%s)", sub_id, status)
                    self._prune(sub_id)
                else:
                    log.warning("push send failed for %s: %s", sub_id, e)
            except Exception as e:  # noqa: BLE001
                log.warning("push send error for %s: %s", sub_id, e)
        return sent


_singleton: Notifier | None = None


def get_notifier() -> Notifier:
    global _singleton
    if _singleton is None:
        _singleton = Notifier()
    return _singleton


def reset_notifier() -> None:
    global _singleton
    _singleton = None
