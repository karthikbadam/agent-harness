"""Native loop task (F4): start_loop seeds iteration #1, each iteration's
finalize spawns the next via planner.advance_loop until a stop condition.

These drive ``task_runner.on_job_finalized`` directly with hand-written event
logs (same approach as test_task_runner) so the loop logic is exercised
deterministically without spawning real subprocesses. The autorun handoff that
jobs.py performs (kick each returned id) is simulated by following the returned
``autorun_ids``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from agent_harness import config, models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.db import session_scope
from agent_harness.main import create_app
from agent_harness.reconcile import reconcile_jobs
from agent_harness.services import planner, task_runner

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"
AUTH = {"Authorization": "Bearer test-token"}


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True)
    (path / "f.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--no-gpg-sign", "-q", "-m", "init"],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"]).decode().strip()


def _write_loop_log(log_dir: Path, text: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "type": "assistant_text",
            "job_id": "x",
            "turn": 0,
            "ts": "2026-05-30T00:00:00Z",
            "seq": 1,
            "text": text,
        }
    )
    (log_dir / "turn-0.jsonl").write_text(line + "\n", encoding="utf-8")


def _result_text(metric: float, kept: bool = True) -> str:
    return (
        "Ran one experiment.\n"
        f'LOOP_RESULT: {{"metric": {metric}, "kept": {str(kept).lower()}, '
        '"description": "tried a thing"}'
    )


def _make_loop(s, repo: Path, spec: dict) -> tuple[str, str]:
    proj = models.Project(name="r", path=str(repo))
    s.add(proj)
    s.flush()
    parent = models.Task(
        project_id=proj.id,
        title="loop",
        prompt="PROGRAM_BODY",
        status="ready",
        mode="loop",
        loop_spec=spec,
    )
    s.add(parent)
    s.flush()
    return proj.id, parent.id


def _finalize_iteration(
    tmp_path: Path,
    project_id: str,
    repo: Path,
    child_id: str,
    text: str,
    job_status: str = "done",
) -> list[str]:
    """Create a Job for the iteration child, drop a result log, finalize it,
    and return the autorun ids (the next iteration, if any)."""
    with session_scope() as s:
        job = models.Job(project_id=project_id, task_id=child_id, kind="execute", cwd=str(repo))
        s.add(job)
        s.flush()
        jid = job.id
    log_dir = tmp_path / "logs" / jid
    _write_loop_log(log_dir, text)
    return task_runner.on_job_finalized(jid, job_status, log_dir=log_dir)


# ------------------------------- unit: seeding ----------------------------- #


def test_start_loop_seeds_iteration_one(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, {"metric_name": "val_acc", "max_iterations": 5})

    child_id = planner.start_loop(parent_id)
    assert child_id is not None

    with session_scope() as s:
        parent = s.get(models.Task, parent_id)
        assert parent.status == "running"
        assert parent.loop_state["iteration"] == 0
        child = s.get(models.Task, child_id)
        assert child.source == "loop"
        assert child.mode == "one_shot"
        assert child.parent_task_id == parent_id
        assert child.order_idx == 1
        assert child.status == "ready"
        assert "iteration 1" in child.prompt.lower()
        assert "PROGRAM_BODY" in child.prompt  # standing instruction carried


# --------------------------- chain to a stop condition --------------------- #


def test_loop_runs_to_max_iterations(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    spec = {
        "metric_name": "val_acc",
        "direction": "maximize",
        "max_iterations": 3,
        "max_consecutive_failures": 5,
    }
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child_id = planner.start_loop(parent_id)
    metrics = [0.5, 0.7, 0.6]  # best is iteration 2
    seen = 0
    while child_id is not None:
        text = _result_text(metrics[seen])
        autorun = _finalize_iteration(tmp_path, pid, repo, child_id, text)
        seen += 1
        child_id = autorun[0] if autorun else None

    assert seen == 3
    with session_scope() as s:
        parent = s.get(models.Task, parent_id)
        assert parent.status == "done"
        assert parent.loop_state["iteration"] == 3
        assert parent.loop_state["best_metric"] == 0.7  # max, from iteration 2
        children = s.query(models.Task).filter(models.Task.parent_task_id == parent_id).all()
        assert len(children) == 3
        for c in children:
            o = s.query(models.Outcome).filter(models.Outcome.task_id == c.id).first()
            assert o is not None and o.meta.get("metric") in metrics
        final = (
            s.query(models.Outcome)
            .filter(models.Outcome.task_id == parent_id, models.Outcome.kind == "loop")
            .first()
        )
        assert final is not None
        assert "max-iterations" in final.summary


def test_loop_stops_at_target_metric(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    spec = {
        "metric_name": "val_acc",
        "direction": "maximize",
        "max_iterations": 50,
        "target_metric": 0.9,
        "max_consecutive_failures": 5,
    }
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child_id = planner.start_loop(parent_id)
    metrics = [0.5, 0.95]  # second one crosses the target
    seen = 0
    while child_id is not None and seen < 10:
        autorun = _finalize_iteration(
            tmp_path, pid, repo, child_id, _result_text(metrics[min(seen, 1)])
        )
        seen += 1
        child_id = autorun[0] if autorun else None

    assert seen == 2  # stopped right after crossing the target
    with session_scope() as s:
        parent = s.get(models.Task, parent_id)
        assert parent.status == "done"
        final = (
            s.query(models.Outcome)
            .filter(models.Outcome.task_id == parent_id, models.Outcome.kind == "loop")
            .first()
        )
        assert "target-reached" in final.summary


def test_loop_stops_after_consecutive_failures(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    spec = {
        "metric_name": "val_acc",
        "max_iterations": 50,
        "max_consecutive_failures": 2,
    }
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child_id = planner.start_loop(parent_id)
    seen = 0
    while child_id is not None and seen < 10:
        # job failed AND no parseable metric → counts as a failure
        autorun = _finalize_iteration(
            tmp_path,
            pid,
            repo,
            child_id,
            "crashed, no result line",
            job_status="failed",
        )
        seen += 1
        child_id = autorun[0] if autorun else None

    assert seen == 2
    with session_scope() as s:
        parent = s.get(models.Task, parent_id)
        assert parent.status == "done"
        assert parent.loop_state["consecutive_failures"] == 2


def test_stuck_detection_triggers_rethink(initdb: Path, tmp_path: Path) -> None:
    """After stuck_after non-improving iterations, the next one is a 'rethink'
    iteration — different prompt + escalated model — and the streak resets."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    spec = {
        "metric_name": "val_acc",
        "direction": "maximize",
        "max_iterations": 20,
        "max_consecutive_failures": 5,
        "stuck_after": 2,
        "escalate_model": "big-model",
    }
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child = planner.start_loop(parent_id)
    # baseline improves (0.5), then two that don't beat it → stuck.
    for m in [0.5, 0.4, 0.45]:
        autorun = _finalize_iteration(tmp_path, pid, repo, child, _result_text(m))
        child = autorun[0] if autorun else None

    assert child is not None
    with session_scope() as s:
        nxt = s.get(models.Task, child)
        assert nxt.model_override == "big-model"
        assert "rethink" in nxt.title.lower()
        assert "RETHINK" in nxt.prompt
        parent = s.get(models.Task, parent_id)
        assert parent.loop_state["non_improving_streak"] == 0  # reset for the new dir


def test_improvement_resets_stuck_streak(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    spec = {
        "metric_name": "val_acc",
        "direction": "maximize",
        "max_iterations": 20,
        "max_consecutive_failures": 5,
        "stuck_after": 2,
    }
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child = planner.start_loop(parent_id)
    # 0.5 (improve) → 0.4 (streak 1) → 0.6 (improve, resets) → next is NOT meta.
    for m in [0.5, 0.4, 0.6]:
        autorun = _finalize_iteration(tmp_path, pid, repo, child, _result_text(m))
        child = autorun[0] if autorun else None

    with session_scope() as s:
        nxt = s.get(models.Task, child)
        assert nxt.model_override is None
        assert "rethink" not in nxt.title.lower()
        assert s.get(models.Task, parent_id).loop_state["non_improving_streak"] == 0


async def test_loop_survives_restart(initdb: Path, tmp_path: Path) -> None:
    """A server restart kills the in-flight iteration; reconcile must finalize
    it and spawn the next so the loop resumes instead of stalling."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    spec = {"metric_name": "val_acc", "max_iterations": 5, "max_consecutive_failures": 3}
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child1 = planner.start_loop(parent_id)
    # Simulate that iteration #1 had been kicked: a running task + running job.
    with session_scope() as s:
        s.get(models.Task, child1).status = "running"
        job = models.Job(
            project_id=pid,
            task_id=child1,
            kind="execute",
            cwd=str(repo),
            status="running",
        )
        s.add(job)
        s.flush()
        jid = job.id

    # Restart reconciliation (no manager → the resumed iteration is left ready).
    reg = BroadcasterRegistry(initdb / "logs")
    reconciled = await reconcile_jobs(reg)
    assert jid in reconciled

    with session_scope() as s:
        parent = s.get(models.Task, parent_id)
        assert parent.status == "running", "loop should still be going"
        assert parent.loop_state["iteration"] == 1
        # A restart interrupt is operational, NOT a research failure — so it
        # must not count toward the consecutive-failure stop (else repeated
        # deploys would kill a long run).
        assert parent.loop_state["consecutive_failures"] == 0
        kids = s.query(models.Task).filter(models.Task.parent_task_id == parent_id).all()
        assert len(kids) == 2, "interrupted iter1 + resumed iter2"
        nxt = [k for k in kids if k.status == "ready"]
        assert len(nxt) == 1 and nxt[0].order_idx == 2, "next iteration queued"


def test_planner_emits_runnable_loop_task(initdb: Path) -> None:
    """A planner 'loop' entry becomes a ready mode='loop' task (which then
    auto-runs), with the loop_spec carried from the entry."""
    with session_scope() as s:
        p = models.Project(name="x", path="/tmp")
        s.add(p)
        s.flush()
        pid = p.id
    parsed = [
        {
            "kind": "loop",
            "title": "galaxy",
            "prompt": "Build one Milky Way characteristic per iteration.",
            "metric_name": "characteristics",
            "direction": "maximize",
            "max_iterations": 20,
            "target_metric": 18,
            "stuck_after": 3,
        }
    ]
    ids = planner._insert_drafts(pid, parsed)
    assert len(ids) == 1
    with session_scope() as s:
        t = s.get(models.Task, ids[0])
        assert t.mode == "loop"
        assert t.source == "planner"
        assert t.status == "ready"  # no deps → ready → planner autorun kicks it
        assert t.idle_timeout_seconds == 0
        assert t.loop_spec["metric_name"] == "characteristics"
        assert t.loop_spec["max_iterations"] == 20
        assert t.loop_spec["target_metric"] == 18
        assert t.loop_spec["stuck_after"] == 3


def test_planner_loop_defaults(initdb: Path) -> None:
    with session_scope() as s:
        p = models.Project(name="x", path="/tmp")
        s.add(p)
        s.flush()
        pid = p.id
    ids = planner._insert_drafts(
        pid, [{"kind": "loop", "title": "survey", "prompt": "one iteration"}]
    )
    with session_scope() as s:
        spec = s.get(models.Task, ids[0]).loop_spec
        assert spec["metric_name"] == "metric"
        assert spec["direction"] == "maximize"
        assert spec["max_iterations"] == 25
        assert spec["stuck_after"] == 4
        assert spec["target_metric"] is None


def test_metric_backstop_from_results_tsv(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    # No LOOP_RESULT line; the metric must come from results.tsv.
    (repo / "results.tsv").write_text(
        "commit\tval_acc\tstatus\na1b2c3d\t0.81\tkeep\n", encoding="utf-8"
    )
    spec = {"metric_name": "val_acc", "max_iterations": 1}
    with session_scope() as s:
        pid, parent_id = _make_loop(s, repo, spec)

    child_id = planner.start_loop(parent_id)
    _finalize_iteration(tmp_path, pid, repo, child_id, "I forgot the result line.")

    with session_scope() as s:
        parent = s.get(models.Task, parent_id)
        assert parent.loop_state["best_metric"] == 0.81  # read from results.tsv


# --------------------------------- API ------------------------------------- #


@pytest.fixture
async def app_client(initdb: Path):
    config.write_toml({"auth_token": "test-token", "claude_path": str(SHIM)})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client


async def test_create_loop_paused(app_client, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    r = await app_client.post("/api/projects", headers=AUTH, json={"name": "p", "path": str(repo)})
    pid = r.json()["id"]
    # run=false so no subprocess is spawned in the test.
    r = await app_client.post(
        f"/api/projects/{pid}/loops?run=false",
        headers=AUTH,
        json={
            "title": "autoresearch",
            "prompt": "PROGRAM",
            "metric_name": "val_acc",
            "direction": "maximize",
            "max_iterations": 25,
            "target_metric": 0.97,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == "loop"
    assert body["status"] == "ready"
    assert body["loop_spec"]["metric_name"] == "val_acc"
    assert body["loop_spec"]["max_iterations"] == 25
    assert body["loop_spec"]["target_metric"] == 0.97

    # No iterations until it runs.
    r = await app_client.get(f"/api/tasks/{body['id']}/iterations", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == []
