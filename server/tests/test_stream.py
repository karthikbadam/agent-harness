from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agent_harness import config
from agent_harness.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


@pytest.fixture
async def app_client(initdb: Path, monkeypatch: pytest.MonkeyPatch):
    config.write_toml({"auth_token": "T", "claude_path": str(SHIM)})
    config.reset_settings_cache()
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(FIXTURES / "stream" / "tool_use_ok.jsonl"))
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


def _parse_sse(payload: str) -> list[dict]:
    events: list[dict] = []
    current: dict[str, str] = {}
    for line in payload.split("\n"):
        if line == "":
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            current[k.strip()] = v.lstrip()
    return events


async def test_stream_replays_persisted_events(app_client) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer T"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        "/api/jobs", json={"project_id": pid, "prompt": "hello"}, headers=auth
    )
    jid = r.json()["id"]
    await app.state.job_manager.wait(jid)

    async with client.stream("GET", f"/api/jobs/{jid}/stream?token=T") as resp:
        assert resp.status_code == 200
        buf = ""
        events: list[dict] = []
        async for chunk in resp.aiter_text():
            buf += chunk
            parsed = _parse_sse(buf)
            events = [e for e in parsed if e.get("event")]
            if any(e.get("event") == "job_status" for e in events) and len(events) >= 5:
                break
    types = [e["event"] for e in events]
    assert "tool_use" in types
    assert "tool_result" in types
    assert "turn_done" in types
    assert "job_status" in types
    assert all("id" in e for e in events)
    # ids monotonically increase
    ids = [int(e["id"]) for e in events]
    assert ids == sorted(ids)


async def test_stream_honors_last_event_id_query(app_client) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer T"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        "/api/jobs", json={"project_id": pid, "prompt": "x"}, headers=auth
    )
    jid = r.json()["id"]
    await app.state.job_manager.wait(jid)

    async with client.stream(
        "GET", f"/api/jobs/{jid}/stream?token=T&last_event_id=4"
    ) as resp:
        buf = ""
        events: list[dict] = []
        async for chunk in resp.aiter_text():
            buf += chunk
            events = [e for e in _parse_sse(buf) if e.get("event")]
            if any(e.get("event") == "job_status" for e in events):
                break
    ids = [int(e["id"]) for e in events]
    assert all(i > 4 for i in ids)


async def test_stream_404_for_unknown_job(app_client) -> None:
    client, _ = app_client
    r = await client.get("/api/jobs/nope/stream?token=T")
    assert r.status_code == 404


async def test_stream_auth_required(app_client) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer T"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        "/api/jobs", json={"project_id": pid, "prompt": "x"}, headers=auth
    )
    jid = r.json()["id"]
    r = await client.get(f"/api/jobs/{jid}/stream")
    assert r.status_code == 401
