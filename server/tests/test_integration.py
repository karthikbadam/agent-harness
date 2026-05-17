from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.db import session_scope
from agent_harness.services import integration, task_runner, worktrees


def _init_git_repo(path: Path) -> None:
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


def test_create_integration_task_rejects_undone_input(
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
            project_id=proj.id, title="t", prompt="p",
            status="running", mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        pid, tid = proj.id, t.id

    with pytest.raises(ValueError, match="status"):
        integration.create_integration_task(pid, [tid])


def test_create_integration_task_builds_synthetic_task_with_deps(
    initdb: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t1 = models.Task(
            project_id=proj.id, title="t1", prompt="p1",
            status="done", mode="plan_then_execute",
            worktree_branch="task/t1", worktree_path="/tmp/wt1",
            integration_status="pending",
        )
        t2 = models.Task(
            project_id=proj.id, title="t2", prompt="p2",
            status="done", mode="plan_then_execute",
            worktree_branch="task/t2", worktree_path="/tmp/wt2",
            integration_status="pending",
        )
        s.add_all([t1, t2])
        s.flush()
        pid, t1id, t2id = proj.id, t1.id, t2.id

    synth_id = integration.create_integration_task(pid, [t1id, t2id], target_branch="main")

    with session_scope() as s:
        synth = s.get(models.Task, synth_id)
        assert synth is not None
        assert synth.synthetic is True
        assert synth.mode == "one_shot"
        assert synth.status == "ready"
        assert "task/t1" in synth.prompt
        assert "task/t2" in synth.prompt
        assert "main" in synth.prompt
        deps = {
            r[0]
            for r in s.execute(
                models.TaskDependency.__table__.select().where(
                    models.TaskDependency.task_id == synth_id
                )
            ).all()
        }
        # join row tuples are (task_id, depends_on_id); index 1 is dep
        deps_set = set()
        rows = s.execute(
            models.TaskDependency.__table__.select().where(
                models.TaskDependency.task_id == synth_id
            )
        ).all()
        for r in rows:
            deps_set.add(r[1])
        assert deps_set == {t1id, t2id}


def test_integration_finalize_cleans_worktrees_on_success(
    initdb: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t1 = models.Task(
            project_id=proj.id, title="t1", prompt="p1",
            status="done", mode="plan_then_execute",
            integration_status="pending",
        )
        s.add(t1)
        s.flush()
        # Create a real worktree for t1 so cleanup has something to do.
        path, branch = worktrees.create(proj, t1)
        t1.worktree_path = path
        t1.worktree_branch = branch
        s.flush()
        # Build the synthetic integration task + dep + job.
        synth = models.Task(
            project_id=proj.id, title="integrate", prompt="merge",
            status="running", mode="one_shot", synthetic=True,
        )
        s.add(synth)
        s.flush()
        s.add(models.TaskDependency(task_id=synth.id, depends_on_id=t1.id))
        job = models.Job(
            project_id=proj.id, task_id=synth.id, phase="integrating"
        )
        s.add(job)
        s.flush()
        t1id, sid, jid, wt_path = t1.id, synth.id, job.id, path

    task_runner.on_job_finalized(jid, "done", log_dir=None)

    with session_scope() as s:
        outcomes = s.query(models.Outcome).filter(models.Outcome.task_id == sid).all()
        assert len(outcomes) == 1
        assert outcomes[0].kind == "integrate"
        t1 = s.get(models.Task, t1id)
        assert t1.integration_status == "integrated"
        assert t1.worktree_path is None
        assert t1.worktree_branch is None
    assert not Path(wt_path).exists()


def test_integration_finalize_marks_conflict_on_failure(
    initdb: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t1 = models.Task(
            project_id=proj.id, title="t1", prompt="p1",
            status="done", mode="plan_then_execute",
            worktree_branch="task/t1", worktree_path="/tmp/wt1",
            integration_status="pending",
        )
        s.add(t1)
        s.flush()
        synth = models.Task(
            project_id=proj.id, title="integrate", prompt="merge",
            status="running", mode="one_shot", synthetic=True,
        )
        s.add(synth)
        s.flush()
        s.add(models.TaskDependency(task_id=synth.id, depends_on_id=t1.id))
        job = models.Job(
            project_id=proj.id, task_id=synth.id, phase="integrating"
        )
        s.add(job)
        s.flush()
        t1id, jid = t1.id, job.id

    task_runner.on_job_finalized(jid, "failed", log_dir=None)

    with session_scope() as s:
        t1 = s.get(models.Task, t1id)
        # Conflict — worktree path/branch preserved so a followup can resolve.
        assert t1.integration_status == "conflict"
        assert t1.worktree_branch == "task/t1"
