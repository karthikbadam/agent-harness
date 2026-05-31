"""Prompt-driven planning: gate (auto-run vs draft), steer (followup replaces
drafts), confirm (launch), loop nodes with deps, and the run/retry/delete fixes.

Service-level tests drive ``task_runner.on_job_finalized`` with hand-written turn
logs (like test_task_runner); API tests use the fake_claude app client.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from agent_harness import config, models
from agent_harness.db import session_scope
from agent_harness.main import create_app
from agent_harness.services import planner, task_runner

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"
AUTH = {"Authorization": "Bearer test-token"}


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("hi")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--no-gpg-sign", "-q", "-m", "init"],
        check=True,
    )


def _plan_setup(repo: Path) -> tuple[str, str, str]:
    """A project + a running plan task + its plan job. Returns (pid, tid, jid)."""
    with session_scope() as s:
        p = models.Project(name="r", path=str(repo))
        s.add(p)
        s.flush()
        t = models.Task(
            project_id=p.id, title="Plan: x", prompt="x", status="running",
            phase="planning", mode="plan", source="user",
        )
        s.add(t)
        s.flush()
        j = models.Job(project_id=p.id, title="plan", task_id=t.id, kind="plan",
                       cwd=str(repo))
        s.add(j)
        s.flush()
        return p.id, t.id, j.id


def _write_turn(log_dir: Path, idx: int, text: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"type": "assistant_text", "job_id": "x", "turn": idx,
                       "ts": "2026-05-31T00:00:00Z", "seq": 1, "text": text})
    (log_dir / f"turn-{idx}.jsonl").write_text(line + "\n", encoding="utf-8")


# ------------------------------- gate ------------------------------------- #


def test_gate_loop_plan_parks_for_review(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_git_repo(repo)
    pid, tid, jid = _plan_setup(repo)
    arr = json.dumps([
        {"kind": "task", "title": "setup", "prompt": "set up", "depends_on_titles": []},
        {"kind": "loop", "title": "optimize", "prompt": "one iteration",
         "metric_name": "score", "depends_on_titles": ["setup"]},
    ])
    log = tmp_path / "logs" / jid
    _write_turn(log, 0, arr)
    autorun = task_runner.on_job_finalized(jid, "done", log_dir=log)

    assert autorun == [], "a plan with a loop must not auto-run"
    with session_scope() as s:
        plan = s.get(models.Task, tid)
        assert plan.status == "running" and plan.phase == "awaiting_ack"
        drafts = s.query(models.Task).filter(
            models.Task.parent_task_id == tid).all()
        assert len(drafts) == 2
        assert all(d.status == "pending" for d in drafts)  # not promoted
        assert all(d.parent_task_id == tid for d in drafts)  # linked to plan


def test_gate_large_plan_parks(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_git_repo(repo)
    pid, tid, jid = _plan_setup(repo)
    arr = json.dumps([
        {"title": f"t{i}", "prompt": f"do {i}", "depends_on_titles": []}
        for i in range(10)  # == PLAN_AUTORUN_MAX → gated
    ])
    log = tmp_path / "logs" / jid
    _write_turn(log, 0, arr)
    autorun = task_runner.on_job_finalized(jid, "done", log_dir=log)
    assert autorun == []
    with session_scope() as s:
        assert s.get(models.Task, tid).phase == "awaiting_ack"


def test_gate_simple_plan_autoruns(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_git_repo(repo)
    pid, tid, jid = _plan_setup(repo)
    arr = json.dumps([
        {"title": "a", "prompt": "do a", "mode": "research", "depends_on_titles": []},
        {"title": "b", "prompt": "do b", "mode": "research", "depends_on_titles": []},
    ])
    log = tmp_path / "logs" / jid
    _write_turn(log, 0, arr)
    autorun = task_runner.on_job_finalized(jid, "done", log_dir=log)
    assert len(autorun) == 2, "simple no-loop plan auto-runs its ready tasks"
    with session_scope() as s:
        assert s.get(models.Task, tid).status == "done"


# ------------------------------- steer ------------------------------------ #


def test_steering_followup_replaces_drafts(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_git_repo(repo)
    pid, tid, jid = _plan_setup(repo)
    log = tmp_path / "logs" / jid
    # First plan (gated by the loop) → drafts A.
    _write_turn(log, 0, json.dumps([
        {"title": "oldsetup", "prompt": "old", "depends_on_titles": []},
        {"kind": "loop", "title": "oldloop", "prompt": "x", "metric_name": "m",
         "depends_on_titles": ["oldsetup"]},
    ]))
    task_runner.on_job_finalized(jid, "done", log_dir=log)
    # Steering followup turn → a revised list B (latest turn only).
    _write_turn(log, 1, json.dumps([
        {"kind": "loop", "title": "newloop", "prompt": "y", "metric_name": "m",
         "depends_on_titles": []},
    ]))
    task_runner.on_job_finalized(jid, "done", log_dir=log)

    with session_scope() as s:
        plan = s.get(models.Task, tid)
        assert plan.phase == "awaiting_ack"  # still parked for review
        titles = {d.title for d in s.query(models.Task).filter(
            models.Task.parent_task_id == tid).all()}
        assert titles == {"newloop"}, "old drafts replaced by the revised list"


# --------------------------- loop node with deps -------------------------- #


def test_loop_node_with_deps_is_pending(initdb: Path) -> None:
    with session_scope() as s:
        p = models.Project(name="r", path="/tmp")
        s.add(p)
        s.flush()
        pid = p.id
    ids = planner._insert_drafts(pid, [
        {"title": "setup", "prompt": "build it", "depends_on_titles": []},
        {"kind": "loop", "title": "opt", "prompt": "iterate", "metric_name": "m",
         "depends_on_titles": ["setup"]},
    ])
    with session_scope() as s:
        loop = next(t for t in s.query(models.Task).filter(models.Task.id.in_(ids))
                    if t.mode == "loop")
        assert loop.status == "pending"  # has a dep → waits
        deps = s.query(models.TaskDependency).filter(
            models.TaskDependency.task_id == loop.id).all()
        assert len(deps) == 1  # depends on setup


# ------------------------------- API -------------------------------------- #


@pytest.fixture
async def app_client(initdb: Path):
    config.write_toml({"auth_token": "test-token", "claude_path": str(SHIM)})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_run_endpoint_starts_a_loop(app_client, tmp_path: Path) -> None:
    """POST /run on a loop task must go through start_loop (spawn iteration 1),
    not run the loop parent as one direct turn (the run_task bug)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    r = await app_client.post("/api/projects", headers=AUTH,
                              json={"name": "p", "path": str(repo)})
    pid = r.json()["id"]
    r = await app_client.post(f"/api/projects/{pid}/loops?run=false", headers=AUTH,
                              json={"title": "loop", "prompt": "P", "metric_name": "m"})
    loop_id = r.json()["id"]
    r = await app_client.post(f"/api/tasks/{loop_id}/run", headers=AUTH)
    assert r.status_code == 200, r.text
    # start_loop seeded an iteration child + set loop_state.
    r = await app_client.get(f"/api/tasks/{loop_id}/iterations", headers=AUTH)
    assert len(r.json()) >= 1, "loop ran via start_loop, not a single direct turn"
    r = await app_client.get(f"/api/tasks/{loop_id}", headers=AUTH)
    assert r.json()["loop_state"] is not None


async def test_confirm_launches_drafts(app_client, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    r = await app_client.post("/api/projects", headers=AUTH,
                              json={"name": "p", "path": str(repo)})
    pid = r.json()["id"]
    # Build a gated plan + draft directly, then confirm.
    with session_scope() as s:
        plan = models.Task(project_id=pid, title="Plan", prompt="x", status="running",
                           phase="awaiting_ack", mode="plan", source="user")
        s.add(plan)
        s.flush()
        draft = models.Task(project_id=pid, title="d", prompt="do", status="pending",
                            mode="research", source="planner", parent_task_id=plan.id)
        s.add(draft)
        s.flush()
        plan_id = plan.id
    r = await app_client.post(f"/api/tasks/{plan_id}/confirm", headers=AUTH)
    assert r.status_code == 200, r.text
    with session_scope() as s:
        assert s.get(models.Task, plan_id).status == "done"
        d = s.query(models.Task).filter(models.Task.parent_task_id == plan_id).one()
        assert d.status in ("running", "done")  # promoted + launched


async def test_delete_project_with_artifacts(app_client, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "g.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    r = await app_client.post("/api/projects", headers=AUTH,
                              json={"name": "p", "path": str(repo)})
    pid = r.json()["id"]
    r = await app_client.post(f"/api/projects/{pid}/tasks", headers=AUTH,
                              json={"title": "t", "prompt": "p", "mode": "one_shot"})
    tid = r.json()["id"]
    r = await app_client.post(f"/api/tasks/{tid}/artifacts", headers=AUTH,
                              json={"kind": "graph", "path": "g.png", "name": "g.png"})
    assert r.status_code == 201
    # Delete must not trip the artifacts/outcomes FK.
    r = await app_client.delete(f"/api/projects/{pid}", headers=AUTH)
    assert r.status_code == 204, r.text
