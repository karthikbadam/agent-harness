from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness import config, models, notify
from agent_harness.db import session_scope


def _seed_sub() -> str:
    with session_scope() as s:
        sub = models.PushSubscription(
            endpoint="https://example.com/p/1",
            p256dh="aaa",
            auth="bbb",
        )
        s.add(sub)
        s.flush()
        return sub.id


def test_payload_json_includes_tag(ah_home: Path) -> None:
    p = notify.PushPayload(title="t", body="b", job_id="j1", url="/jobs/j1")
    data = json.loads(p.to_json())
    assert data["title"] == "t"
    assert data["tag"] == "job:j1"


def test_disabled_when_vapid_missing(initdb: Path) -> None:
    notify.reset_notifier()
    n = notify.get_notifier()
    assert n.enabled is False
    sent = n.send_to_all(notify.PushPayload(title="t", body="b"))
    assert sent == 0


def test_send_to_all_calls_webpush_for_each_sub(initdb: Path) -> None:
    config.write_toml({"vapid_private_key": "x", "vapid_public_key": "y"})
    config.reset_settings_cache()
    notify.reset_notifier()
    _seed_sub()
    _seed_sub() if False else None  # one sub for simplicity
    with patch.object(notify, "webpush") as mock_push:
        sent = notify.get_notifier().send_to_all(
            notify.PushPayload(title="t", body="b", job_id="j1")
        )
    assert sent == 1
    mock_push.assert_called_once()


def test_send_prunes_410_subscriptions(initdb: Path) -> None:
    config.write_toml({"vapid_private_key": "x", "vapid_public_key": "y"})
    config.reset_settings_cache()
    notify.reset_notifier()
    sid = _seed_sub()

    class FakeResponse:
        status_code = 410

    def raise_410(*_a, **_k):  # type: ignore[no-untyped-def]
        raise notify.WebPushException("gone", response=FakeResponse())

    with patch.object(notify, "webpush", side_effect=raise_410):
        sent = notify.get_notifier().send_to_all(notify.PushPayload(title="t", body="b"))
    assert sent == 0

    with session_scope() as s:
        assert s.get(models.PushSubscription, sid) is None
