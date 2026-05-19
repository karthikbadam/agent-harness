from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from agent_harness import config
from agent_harness.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


@pytest.fixture
async def app_client(initdb: Path, monkeypatch: pytest.MonkeyPatch):
    config.write_toml({"auth_token": "test-token", "claude_path": str(SHIM)})
    config.reset_settings_cache()
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(FIXTURES / "stream" / "tool_use_ok.jsonl"))
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


# ----------------------------- auth ------------------------------------- #


async def test_no_token_401(app_client) -> None:
    client, _ = app_client
    r = await client.get("/api/projects")
    assert r.status_code == 401


async def test_bearer_token_works(app_client) -> None:
    client, _ = app_client
    r = await client.get("/api/projects", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200


async def test_query_token_works(app_client) -> None:
    client, _ = app_client
    r = await client.get("/api/projects?token=test-token")
    assert r.status_code == 200


async def test_wrong_token_401(app_client) -> None:
    client, _ = app_client
    r = await client.get("/api/projects", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ----------------------------- projects --------------------------------- #


async def test_projects_crud(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post(
        "/api/projects", json={"name": "book", "path": "/tmp/book"}, headers=auth
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["permission_mode"] == "acceptEdits"
    assert r.json()["instructions"] is None
    assert r.json()["skills"] == []
    assert r.json()["context_paths"] == []

    r = await client.get("/api/projects", headers=auth)
    assert r.status_code == 200
    assert any(p["id"] == pid for p in r.json())

    r = await client.patch(
        f"/api/projects/{pid}", json={"dangerously_skip": True}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["dangerously_skip"] is True

    r = await client.delete(f"/api/projects/{pid}", headers=auth)
    assert r.status_code == 204
    r = await client.get(f"/api/projects/{pid}", headers=auth)
    assert r.status_code == 404


async def test_project_context_fields(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post(
        "/api/projects",
        json={
            "name": "ctx",
            "path": "/tmp/ctx",
            "instructions": "Always use snake_case.",
            "skills": ["init", "review"],
            "context_paths": ["/tmp/notes", "/tmp/refs"],
        },
        headers=auth,
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["instructions"] == "Always use snake_case."
    assert r.json()["skills"] == ["init", "review"]
    assert r.json()["context_paths"] == ["/tmp/notes", "/tmp/refs"]

    r = await client.patch(
        f"/api/projects/{pid}",
        json={"instructions": "Updated.", "skills": ["security-review"]},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["instructions"] == "Updated."
    assert r.json()["skills"] == ["security-review"]
    assert r.json()["context_paths"] == ["/tmp/notes", "/tmp/refs"]


# ----------------------------- jobs end-to-end -------------------------- #


async def test_create_job_runs_to_completion(app_client) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        "/api/jobs", json={"project_id": pid, "prompt": "hello"}, headers=auth
    )
    assert r.status_code == 201
    jid = r.json()["id"]
    await app.state.job_manager.wait(jid)
    r = await client.get(f"/api/jobs/{jid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["session_id"] == "sess_xyz"


async def test_followup_and_stop(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        "/api/jobs", json={"project_id": pid, "prompt": "first"}, headers=auth
    )
    jid = r.json()["id"]
    await app.state.job_manager.wait(jid)

    r = await client.post(
        f"/api/jobs/{jid}/followup", json={"prompt": "second"}, headers=auth
    )
    assert r.status_code == 200
    await app.state.job_manager.wait(jid)
    r = await client.get(f"/api/jobs/{jid}", headers=auth)
    assert len(r.json()["turns"]) == 2


# ----------------------------- schedules -------------------------------- #


async def test_schedules_validate_cron(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        "/api/schedules",
        json={"project_id": pid, "name": "daily", "cron": "0 9 * * *", "prompt": "go"},
        headers=auth,
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await client.post(
        "/api/schedules",
        json={"project_id": pid, "name": "bad", "cron": "garbage", "prompt": "x"},
        headers=auth,
    )
    assert r.status_code == 400

    r = await client.delete(f"/api/schedules/{sid}", headers=auth)
    assert r.status_code == 204


# ----------------------------- allowlist -------------------------------- #


async def test_allowlist_global_and_project(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post("/api/allowlist", json={"rule": "Bash(npm test:*)"}, headers=auth)
    assert r.status_code == 201
    r = await client.post(
        "/api/allowlist", json={"rule": "Edit(**/*.py)", "project_id": pid}, headers=auth
    )
    assert r.status_code == 201
    r = await client.get(f"/api/allowlist?project_id={pid}", headers=auth)
    assert r.status_code == 200
    rules = sorted(x["rule"] for x in r.json())
    assert rules == ["Bash(npm test:*)", "Edit(**/*.py)"]


# ----------------------------- tasks ------------------------------------ #


async def test_tasks_crud_and_status(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]

    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "t1", "prompt": "do 1"},
        headers=auth,
    )
    assert r.status_code == 201
    t1 = r.json()
    # No deps → ready immediately.
    assert t1["status"] == "ready"
    assert t1["source"] == "manual"

    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "t2", "prompt": "do 2", "depends_on": [t1["id"]]},
        headers=auth,
    )
    assert r.status_code == 201
    t2 = r.json()
    assert t2["status"] == "pending"
    assert t2["depends_on"] == [t1["id"]]

    # Cycle detection: t1 cannot depend on t2.
    r = await client.patch(
        f"/api/tasks/{t1['id']}", json={"depends_on": [t2["id"]]}, headers=auth
    )
    assert r.status_code == 400

    # Delete works only when no jobs reference the task.
    r = await client.delete(f"/api/tasks/{t2['id']}", headers=auth)
    assert r.status_code == 204
    r = await client.get(f"/api/tasks/{t2['id']}", headers=auth)
    assert r.status_code == 404


async def test_task_run_creates_job_and_records_task_id(app_client) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "t1", "prompt": "go"},
        headers=auth,
    )
    tid = r.json()["id"]

    r = await client.post(f"/api/tasks/{tid}/run", headers=auth)
    assert r.status_code == 200
    jid = r.json()["id"]
    assert r.json()["task_id"] == tid
    await app.state.job_manager.wait(jid)


async def test_task_create_accepts_mode_one_shot(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    # Default mode (omitted) → plan_then_execute (model default).
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "default", "prompt": "go"},
        headers=auth,
    )
    assert r.json()["mode"] == "plan_then_execute"
    # Explicit one_shot is honored.
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "quick", "prompt": "fix typo", "mode": "one_shot"},
        headers=auth,
    )
    assert r.status_code == 201
    assert r.json()["mode"] == "one_shot"
    assert r.json()["status"] == "ready"


async def test_task_create_with_run_flag_kicks_immediately(app_client) -> None:
    client, app = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks?run=true",
        json={"title": "kick", "prompt": "go", "mode": "one_shot"},
        headers=auth,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "running"
    assert body["phase"] == "executing"
    # The kicked job exists with kind=execute.
    jobs = await client.get(f"/api/jobs?project_id={pid}", headers=auth)
    matching = [j for j in jobs.json() if j["task_id"] == body["id"]]
    assert matching and matching[0]["kind"] == "execute"
    # Let the (fake) runner finish so the test fixture cleanup doesn't dangle.
    await app.state.job_manager.wait(matching[0]["id"])


async def test_task_create_run_flag_noop_when_pending(app_client) -> None:
    """A task with unsatisfied deps lands in pending; ?run=true is a no-op."""
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "t1", "prompt": "first"},
        headers=auth,
    )
    t1 = r.json()["id"]
    # t2 depends on t1 (which is just `ready`, not done) — so t2 is pending.
    r = await client.post(
        f"/api/projects/{pid}/tasks?run=true",
        json={"title": "t2", "prompt": "second", "depends_on": [t1]},
        headers=auth,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["phase"] is None


async def test_task_run_requires_ready_status(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "t1", "prompt": "go"},
        headers=auth,
    )
    t1 = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "t2", "prompt": "go2", "depends_on": [t1]},
        headers=auth,
    )
    t2 = r.json()["id"]
    # t2 is pending; cannot run.
    r = await client.post(f"/api/tasks/{t2}/run", headers=auth)
    assert r.status_code == 409


async def test_project_skills_become_allowlist_entries(app_client) -> None:
    from agent_harness.jobs import _gather_allowlist

    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post(
        "/api/projects",
        json={"name": "skp", "path": "/tmp", "skills": ["init", "review"]},
        headers=auth,
    )
    pid = r.json()["id"]
    r = await client.post(
        "/api/allowlist", json={"rule": "Bash(pytest:*)"}, headers=auth
    )
    assert r.status_code == 201

    merged = _gather_allowlist(pid)
    assert "Bash(pytest:*)" in merged
    assert "Skill(init)" in merged
    assert "Skill(review)" in merged


# ----------------------------- outcomes --------------------------------- #


async def test_outcomes_empty_until_runner_records(app_client) -> None:
    client, _ = app_client
    auth = {"Authorization": "Bearer test-token"}
    r = await client.post("/api/projects", json={"name": "p", "path": "/tmp"}, headers=auth)
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "t", "prompt": "x"}, headers=auth
    )
    tid = r.json()["id"]
    r = await client.get(f"/api/tasks/{tid}/outcomes", headers=auth)
    assert r.status_code == 200
    assert r.json() == []
    r = await client.get(f"/api/projects/{pid}/outcomes", headers=auth)
    assert r.status_code == 200
    assert r.json() == []


# ----------------------------- /api/me ---------------------------------- #


async def test_me_returns_ok(app_client) -> None:
    client, _ = app_client
    r = await client.get("/api/me?token=test-token")
    assert r.status_code == 200
    assert r.json()["ok"] is True
