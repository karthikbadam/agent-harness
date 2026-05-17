from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from agent_harness import config, models
from agent_harness.db import session_scope
from agent_harness.main import create_app
from agent_harness.services import driver_bus


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    driver_bus.reset_bus()
    yield
    driver_bus.reset_bus()


@pytest.fixture
async def app_client(initdb: Path):
    config.write_toml({"auth_token": "test-token"})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


async def test_get_driver_state_default_off(app_client: httpx.AsyncClient) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pa", name="p", path="/tmp"))
    r = await app_client.get("/api/projects/pa/driver", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "off"
    assert body["has_connected_driver"] is False


async def test_mode_on_without_driver_409(app_client: httpx.AsyncClient) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pb", name="p", path="/tmp"))
    # Patch the spawn helper to refuse so we exercise the 409 path.
    from agent_harness.routes import driver as driver_routes

    with patch.object(driver_routes, "_spawn_driver_subprocess", return_value=None):
        r = await app_client.patch(
            "/api/projects/pb/driver",
            headers=_auth(),
            json={"mode": "on"},
        )
    assert r.status_code == 409


async def test_mode_on_with_existing_subscriber_succeeds(
    app_client: httpx.AsyncClient,
) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pc", name="p", path="/tmp"))
    bus = driver_bus.get_bus()
    bus.subscribe()  # simulate a connected driver
    try:
        r = await app_client.patch(
            "/api/projects/pc/driver",
            headers=_auth(),
            json={"mode": "on"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "on"
        assert body["has_connected_driver"] is True
    finally:
        # cleanup
        for q in list(bus._subscribers):
            bus.unsubscribe(q)


async def test_suggestions_returns_actions(app_client: httpx.AsyncClient) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pd", name="p", path="/tmp"))
        s.flush()
        s.add(models.Task(project_id="pd", title="r1", prompt="x", status="ready"))
    r = await app_client.get(
        "/api/projects/pd/driver/suggestions", headers=_auth()
    )
    assert r.status_code == 200
    actions = r.json()
    assert any(a["kind"] == "run" for a in actions)


async def test_notes_crud(app_client: httpx.AsyncClient) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pe", name="p", path="/tmp"))
    r = await app_client.post(
        "/api/driver/notes",
        headers=_auth(),
        json={
            "project_id": "pe",
            "severity": "warn",
            "kind": "stuck",
            "message": "hello",
        },
    )
    assert r.status_code == 201
    nid = r.json()["id"]

    r = await app_client.get(
        "/api/projects/pe/driver/notes", headers=_auth()
    )
    assert r.status_code == 200
    assert any(n["id"] == nid for n in r.json())

    r = await app_client.post(
        f"/api/driver/notes/{nid}/acknowledge", headers=_auth()
    )
    assert r.status_code == 200
    assert r.json()["acknowledged_at"] is not None


async def test_driver_events_409_on_second_subscribe(
    app_client: httpx.AsyncClient,
) -> None:
    bus = driver_bus.get_bus()
    bus.subscribe()
    try:
        r = await app_client.get("/api/driver/events", headers=_auth())
        assert r.status_code == 409
    finally:
        for q in list(bus._subscribers):
            bus.unsubscribe(q)


async def test_driver_status_reflects_state(app_client: httpx.AsyncClient) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pf", name="p", path="/tmp", autopilot_mode="on"))
    r = await app_client.get("/api/driver/status", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert "pf" in body["mode_on_projects"]


async def test_retry_resets_failed_task_and_increments(
    app_client: httpx.AsyncClient,
) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pg", name="p", path="/tmp"))
        s.flush()
        s.add(
            models.Task(
                id="tg1",
                project_id="pg",
                title="t",
                prompt="x",
                status="failed",
                retries=0,
            )
        )

    # The route calls JobManager.start which spawns claude — we mock it out.
    from agent_harness.jobs import JobManager

    async def _noop_start(self, jid):  # type: ignore[no-untyped-def]
        return None

    with patch.object(JobManager, "start", _noop_start):
        r = await app_client.post("/api/tasks/tg1/retry", headers=_auth())

    assert r.status_code == 200, r.text
    with session_scope() as s:
        t = s.get(models.Task, "tg1")
        assert t.retries == 1
        assert t.status == "running"
