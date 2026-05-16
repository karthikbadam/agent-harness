from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_harness import config
from agent_harness.main import create_app
from agent_harness.services import planner

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


def test_extract_json_array_direct() -> None:
    text = '[{"title":"a","prompt":"do a","depends_on_titles":[]}]'
    parsed = planner._extract_json_array(text)
    assert parsed is not None and len(parsed) == 1 and parsed[0]["title"] == "a"


def test_extract_json_array_in_fence() -> None:
    text = 'sure!\n```json\n[{"title":"b","prompt":"do b"}]\n```\nthat\'s the plan.'
    parsed = planner._extract_json_array(text)
    assert parsed is not None and parsed[0]["title"] == "b"


def test_extract_json_array_bracket_fallback() -> None:
    text = "preamble [\n  {\"title\":\"c\",\"prompt\":\"x\"}\n] postamble"
    parsed = planner._extract_json_array(text)
    assert parsed is not None and parsed[0]["title"] == "c"


def test_extract_json_array_bad() -> None:
    assert planner._extract_json_array("nothing here") is None


@pytest.fixture
async def app_client(initdb: Path, monkeypatch: pytest.MonkeyPatch):
    config.write_toml({"auth_token": "test-token", "claude_path": str(SHIM)})
    config.reset_settings_cache()
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(FIXTURES / "stream" / "planner.jsonl"))
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def test_plan_endpoint_creates_pending_tasks(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post(
        "/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth
    )
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/plan",
        json={"ask": "Build a small module with tests."},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert len(body["task_ids"]) == 2

    r = await client.get(f"/api/projects/{pid}/tasks", headers=auth)
    titles = [t["title"] for t in r.json()]
    statuses = [t["status"] for t in r.json()]
    sources = [t["source"] for t in r.json()]
    assert "scaffold module" in titles
    assert "add tests" in titles
    assert all(s == "pending" for s in statuses)
    assert all(s == "planner" for s in sources)

    by_title = {t["title"]: t for t in r.json()}
    assert by_title["add tests"]["depends_on"] == [by_title["scaffold module"]["id"]]


async def test_plan_empty_ask_rejected(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(f"/api/projects/{pid}/plan", json={"ask": "   "}, headers=auth)
    assert r.status_code == 400
