from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_harness import models
from agent_harness.db import session_scope
from agent_harness.services import task_runner


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True
    )
    (path / "f.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--no-gpg-sign", "-q", "-m", "init"],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"]
    ).decode().strip()


def _write_event_log(log_dir: Path, text: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "type": "assistant_text",
            "job_id": "x",
            "turn": 0,
            "ts": "2026-05-16T00:00:00Z",
            "seq": 1,
            "text": text,
        }
    )
    (log_dir / "turn-0.jsonl").write_text(line + "\n", encoding="utf-8")


def test_records_outcome_on_done(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(project_id=proj.id, title="t", prompt="p", status="running")
        s.add(t)
        s.flush()
        job = models.Job(project_id=proj.id, title="run", task_id=t.id)
        s.add(job)
        s.flush()
        jid, tid = job.id, t.id

    log_dir = tmp_path / "logs" / jid
    _write_event_log(log_dir, "all done!")
    task_runner.on_job_finalized(jid, "done", log_dir=log_dir)

    with session_scope() as s:
        outcomes = s.query(models.Outcome).all()
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.task_id == tid
        assert o.job_id == jid
        assert o.commit_sha == sha
        assert o.branch == "main"
        assert o.summary == "all done!"
        assert o.status == "success"
        assert s.get(models.Task, tid).status == "done"


def test_failed_job_records_failed_outcome(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(project_id=proj.id, title="t", prompt="p", status="running")
        s.add(t)
        s.flush()
        job = models.Job(project_id=proj.id, task_id=t.id)
        s.add(job)
        s.flush()
        jid, tid = job.id, t.id
    task_runner.on_job_finalized(jid, "failed", log_dir=tmp_path / "nope")
    with session_scope() as s:
        o = s.query(models.Outcome).one()
        assert o.status == "failed"
        assert s.get(models.Task, tid).status == "failed"


def test_propagates_downstream_to_ready(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t1 = models.Task(project_id=proj.id, title="t1", prompt="x", status="running")
        t2 = models.Task(project_id=proj.id, title="t2", prompt="y", status="pending")
        t3 = models.Task(project_id=proj.id, title="t3", prompt="z", status="pending")
        s.add_all([t1, t2, t3])
        s.flush()
        s.add(models.TaskDependency(task_id=t2.id, depends_on_id=t1.id))
        s.add(models.TaskDependency(task_id=t3.id, depends_on_id=t2.id))
        job = models.Job(project_id=proj.id, task_id=t1.id)
        s.add(job)
        s.flush()
        jid, t1id, t2id, t3id = job.id, t1.id, t2.id, t3.id
    task_runner.on_job_finalized(jid, "done", log_dir=None)
    with session_scope() as s:
        assert s.get(models.Task, t1id).status == "done"
        assert s.get(models.Task, t2id).status == "ready"
        assert s.get(models.Task, t3id).status == "pending"


def test_no_task_id_is_noop(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        job = models.Job(project_id=proj.id)  # no task_id
        s.add(job)
        s.flush()
        jid = job.id
    task_runner.on_job_finalized(jid, "done", log_dir=None)
    with session_scope() as s:
        assert s.query(models.Outcome).count() == 0


def test_no_git_records_null_sha(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "norepo"
    repo.mkdir()
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(project_id=proj.id, title="t", prompt="p", status="running")
        s.add(t)
        s.flush()
        job = models.Job(project_id=proj.id, task_id=t.id)
        s.add(job)
        s.flush()
        jid = job.id
    task_runner.on_job_finalized(jid, "done", log_dir=None)
    with session_scope() as s:
        o = s.query(models.Outcome).one()
        assert o.commit_sha is None
        assert o.branch is None
        assert o.status == "success"


def test_planning_phase_records_plan_outcome_and_keeps_task_running(
    initdb: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id,
            title="t",
            prompt="p",
            status="running",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id, title="run", task_id=t.id, phase="awaiting_ack"
        )
        s.add(job)
        s.flush()
        jid, tid = job.id, t.id

    log_dir = tmp_path / "logs" / jid
    _write_event_log(log_dir, "1. do a thing\n2. do another")
    task_runner.on_job_finalized(jid, "done", log_dir=log_dir)

    with session_scope() as s:
        outcomes = s.query(models.Outcome).all()
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.kind == "plan"
        assert o.commit_sha is None
        assert o.summary and "do a thing" in o.summary
        # Task is still running — waiting on ack.
        assert s.get(models.Task, tid).status == "running"


def test_executing_phase_marks_done_and_integration_pending(
    initdb: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id,
            title="t",
            prompt="p",
            status="running",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id, title="run", task_id=t.id, phase="executing"
        )
        s.add(job)
        s.flush()
        jid, tid = job.id, t.id

    task_runner.on_job_finalized(jid, "done", log_dir=None)
    with session_scope() as s:
        outcomes = s.query(models.Outcome).all()
        assert len(outcomes) == 1
        assert outcomes[0].kind == "execute"
        task = s.get(models.Task, tid)
        assert task.status == "done"
        assert task.integration_status == "pending"


def test_on_ack_creates_worktree_and_flips_phase(
    initdb: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id,
            title="t",
            prompt="do the thing",
            status="running",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id, title="run", task_id=t.id, phase="awaiting_ack"
        )
        s.add(job)
        s.flush()
        jid, tid = job.id, t.id

    exec_prompt = task_runner.on_ack(jid, prompt_addendum="also handle errors")

    assert "do the thing" in exec_prompt
    assert "also handle errors" in exec_prompt
    with session_scope() as s:
        job = s.get(models.Job, jid)
        task = s.get(models.Task, tid)
        assert job.phase == "executing"
        assert job.cwd_override is not None
        assert task.worktree_path == job.cwd_override
        assert task.worktree_branch == f"task/{tid}"
        # The worktree directory exists and is itself a checkout.
        wt = Path(job.cwd_override)
        assert wt.is_dir()
        assert (wt / "f.txt").exists()  # carried over from the initial commit


def test_on_ack_rejects_wrong_phase(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id, title="t", prompt="p", status="running",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        job = models.Job(project_id=proj.id, task_id=t.id, phase="planning")
        s.add(job)
        s.flush()
        jid = job.id
    import pytest

    with pytest.raises(ValueError, match="not awaiting ack"):
        task_runner.on_ack(jid)


def test_reconcile_on_startup_flips_pending_to_ready(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp")
        s.add(proj)
        s.flush()
        t1 = models.Task(project_id=proj.id, title="a", prompt="x", status="done")
        t2 = models.Task(project_id=proj.id, title="b", prompt="y", status="pending")
        s.add_all([t1, t2])
        s.flush()
        s.add(models.TaskDependency(task_id=t2.id, depends_on_id=t1.id))
        t2id = t2.id
    task_runner.reconcile_on_startup()
    with session_scope() as s:
        assert s.get(models.Task, t2id).status == "ready"
