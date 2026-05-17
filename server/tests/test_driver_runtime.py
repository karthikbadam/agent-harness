"""Tests for the driver runtime.

The full SSE → dispatch loop is hard to exercise through httpx's
ASGITransport (it buffers streaming responses). We test the runtime's
pieces directly:

- ``_dispatch`` issues the correct REST call and posts a note.
- ``_react`` queries suggestions and dispatches each one.
- A 409 from the events endpoint surfaces as an HTTPStatusError so the
  outer ``run`` loop backs off.

End-to-end behavior is covered by the route tests + policy tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from agent_harness import config, models
from agent_harness.db import session_scope
from agent_harness.jobs import JobManager
from agent_harness.main import create_app
from agent_harness.services import driver_bus, driver_runtime


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    driver_bus.reset_bus()
    yield
    driver_bus.reset_bus()


@pytest.fixture
async def live_client(initdb: Path):
    config.write_toml({"auth_token": "test-token"})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            yield client


async def _noop_start(self, jid):  # type: ignore[no-untyped-def]
    return None


async def test_react_dispatches_run_for_ready_task(live_client: httpx.AsyncClient) -> None:
    with session_scope() as s:
        s.add(models.Project(id="pa", name="r", path="/tmp"))
        s.flush()
        s.add(models.Task(id="ta1", project_id="pa", title="t", prompt="x", status="ready"))

    runtime = driver_runtime.DriverRuntime(base_url="http://test", token="test-token")
    with patch.object(JobManager, "start", _noop_start):
        await runtime._react(live_client, "pa")

    with session_scope() as s:
        assert s.get(models.Task, "ta1").status == "running"
    # Verify a 'ran' note was posted.
    r = await live_client.get("/api/projects/pa/driver/notes")
    assert any(n["kind"] == "ran" for n in r.json())


async def test_react_dispatches_ack_for_awaiting_ack_job(
    live_client: httpx.AsyncClient,
) -> None:
    # Set up a job at awaiting_ack with a real (but small) git worktree path
    # so on_ack can succeed.
    import subprocess

    from agent_harness.config import ah_home

    repo_path = ah_home() / "repo-driver-test"
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "x@y.z"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.name", "t"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "commit.gpgsign", "false"],
        check=True,
    )
    (repo_path / "f.txt").write_text("hi")
    subprocess.run(["git", "-C", str(repo_path), "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "--no-gpg-sign", "-q", "-m", "init"],
        check=True,
    )

    with session_scope() as s:
        s.add(models.Project(id="pb", name="r", path=str(repo_path)))
        s.flush()
        t = models.Task(
            id="tb1", project_id="pb", title="t", prompt="do",
            status="running", mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        j = models.Job(
            id="jb1", project_id="pb", task_id="tb1", phase="awaiting_ack",
        )
        s.add(j)

    runtime = driver_runtime.DriverRuntime(base_url="http://test", token="test-token")
    with patch.object(JobManager, "followup", _noop_start):
        await runtime._react(live_client, "pb")

    with session_scope() as s:
        j = s.get(models.Job, "jb1")
        assert j.phase == "executing"
        assert j.cwd_override is not None


async def test_dispatch_409_is_silent(live_client: httpx.AsyncClient) -> None:
    """A 409 race is benign and shouldn't post a warn note."""
    runtime = driver_runtime.DriverRuntime(base_url="http://test", token="test-token")
    with session_scope() as s:
        s.add(models.Project(id="pc", name="r", path="/tmp"))
        s.flush()
        # task in 'running' status → /run returns 409
        s.add(
            models.Task(
                id="tc1", project_id="pc", title="t", prompt="x", status="running"
            )
        )

    await runtime._dispatch(
        live_client,
        {
            "kind": "run",
            "project_id": "pc",
            "task_id": "tc1",
            "rest_verb": "POST",
            "rest_path": "/api/tasks/tc1/run",
            "reason": "run tc1",
        },
    )
    r = await live_client.get("/api/projects/pc/driver/notes")
    notes = r.json()
    assert all(n["severity"] != "warn" for n in notes)


async def test_consume_409_on_second_subscriber(live_client: httpx.AsyncClient) -> None:
    """When a subscriber already exists, the runtime's stream attempt 409s."""
    bus = driver_bus.get_bus()
    bus.subscribe()
    try:
        runtime = driver_runtime.DriverRuntime(
            base_url="http://test", token="test-token"
        )
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await runtime._consume(live_client)
        assert exc.value.response.status_code == 409
    finally:
        for q in list(bus._subscribers):
            bus.unsubscribe(q)
