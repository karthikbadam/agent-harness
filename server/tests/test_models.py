from __future__ import annotations

from pathlib import Path

from agent_harness import models
from agent_harness.db import session_scope


def test_roundtrip_project_job_turn(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        job = models.Job(project_id=proj.id, title="chapter 0")
        s.add(job)
        s.flush()
        turn = models.Turn(job_id=job.id, idx=0, prompt="hello", status="queued")
        s.add(turn)
        s.flush()
        pid = proj.id
        jid = job.id

    with session_scope() as s:
        proj = s.get(models.Project, pid)
        assert proj is not None
        assert proj.name == "book"
        assert proj.permission_mode == "acceptEdits"
        assert proj.dangerously_skip is False
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "queued"
        assert len(job.turns) == 1
        assert job.turns[0].prompt == "hello"


def test_allowlist_rule_global_and_project(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        s.add(models.AllowlistRule(rule="Bash(npm test:*)"))  # global
        s.add(models.AllowlistRule(project_id=proj.id, rule="Edit(**/*.py)"))
        s.flush()
        rules = s.query(models.AllowlistRule).all()
        assert {r.rule for r in rules} == {"Bash(npm test:*)", "Edit(**/*.py)"}


def test_gather_allowlist_grants_bash_for_execute_kind(initdb: Path) -> None:
    """Execute jobs run inside an isolated worktree; granting full Bash there
    keeps language toolchains (npm, pytest, cargo, …) and git workflow
    commands from deadlocking on permission prompts the non-interactive
    `claude -p` process can't answer."""
    from agent_harness.jobs import _gather_allowlist

    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp/r", skills=["init"])
        s.add(proj)
        s.flush()
        s.add(models.AllowlistRule(project_id=proj.id, rule="Bash(npm test:*)"))
        s.flush()
        pid = proj.id

    base = _gather_allowlist(pid)
    assert "Bash(npm test:*)" in base
    assert "Skill(init)" in base
    assert "Bash(*)" not in base  # only granted for execute/integrate kinds

    executing = _gather_allowlist(pid, kind="execute")
    assert "Bash(*)" in executing
    # Original rules still present and not duplicated.
    assert executing.count("Bash(npm test:*)") == 1


def test_gather_allowlist_grants_bash_for_integrate_kind(initdb: Path) -> None:
    """The integrate Job runs in the project repo and needs to merge task
    branches, run language toolchains for any post-merge verification, and
    push the result. Full Bash is the simplest blast radius that doesn't
    deadlock on approval prompts."""
    from agent_harness.jobs import _gather_allowlist

    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp/r")
        s.add(proj)
        s.flush()
        pid = proj.id

    integrating = _gather_allowlist(pid, kind="integrate")
    assert "Bash(*)" in integrating


def test_task_and_outcome_models(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        t1 = models.Task(project_id=proj.id, title="t1", prompt="do thing 1")
        t2 = models.Task(project_id=proj.id, title="t2", prompt="do thing 2")
        s.add_all([t1, t2])
        s.flush()
        s.add(models.TaskDependency(task_id=t2.id, depends_on_id=t1.id))
        s.flush()
        job = models.Job(project_id=proj.id, title="run t1", task_id=t1.id)
        s.add(job)
        s.flush()
        s.add(
            models.Outcome(
                task_id=t1.id,
                job_id=job.id,
                commit_sha="abc123",
                branch="main",
                summary="ok",
                status="success",
            )
        )
        s.flush()

        deps = s.query(models.TaskDependency).all()
        assert len(deps) == 1
        assert deps[0].task_id == t2.id and deps[0].depends_on_id == t1.id
        outcomes = s.query(models.Outcome).all()
        assert len(outcomes) == 1
        assert outcomes[0].commit_sha == "abc123"
        assert s.get(models.Job, job.id).task_id == t1.id


def test_schedule_inserts(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        sched = models.Schedule(
            project_id=proj.id, name="daily", cron="0 9 * * *", prompt="write"
        )
        s.add(sched)
        s.flush()
        assert sched.enabled is True
