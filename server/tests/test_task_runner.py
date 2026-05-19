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
            phase="planning",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id,
            title="run",
            task_id=t.id,
            kind="plan",
            cwd=str(repo),
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
        # Task is still running but parked at awaiting_ack for the user/driver.
        task = s.get(models.Task, tid)
        assert task.status == "running"
        assert task.phase == "awaiting_ack"


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
            phase="executing",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id,
            title="run",
            task_id=t.id,
            kind="execute",
            cwd=str(repo),
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
        assert task.phase == "done"
        assert task.integration_status == "pending"


def test_autodisables_autopilot_when_project_runs_dry(initdb: Path) -> None:
    """A finalize that leaves the project with no actionable tasks should
    flip autopilot_mode='on' → 'off'. Conservative: any pending/ready/
    running/failed task blocks the autodisable."""
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp", autopilot_mode="on")
        s.add(proj)
        s.flush()
        # One task that's about to finish.
        t = models.Task(
            project_id=proj.id, title="t", prompt="p",
            status="running", phase="executing", mode="one_shot",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id, task_id=t.id, kind="execute", cwd="/tmp",
        )
        s.add(job)
        s.flush()
        pid, jid = proj.id, job.id

    task_runner.on_job_finalized(jid, "done", log_dir=None)

    with session_scope() as s:
        assert s.get(models.Project, pid).autopilot_mode == "off"


def test_autopilot_stays_on_when_other_tasks_pending(initdb: Path) -> None:
    """If something is still queued for the driver to do, leave autopilot on."""
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp", autopilot_mode="on")
        s.add(proj)
        s.flush()
        finishing = models.Task(
            project_id=proj.id, title="a", prompt="p",
            status="running", phase="executing", mode="one_shot",
        )
        s.add(finishing)
        # A second task still ready — driver would run it next.
        s.add(models.Task(
            project_id=proj.id, title="b", prompt="p",
            status="ready", mode="one_shot",
        ))
        s.flush()
        job = models.Job(
            project_id=proj.id, task_id=finishing.id,
            kind="execute", cwd="/tmp",
        )
        s.add(job)
        s.flush()
        pid, jid = proj.id, job.id

    task_runner.on_job_finalized(jid, "done", log_dir=None)

    with session_scope() as s:
        assert s.get(models.Project, pid).autopilot_mode == "on"


def test_executing_finalize_commits_dirty_worktree(
    initdb: Path, tmp_path: Path
) -> None:
    """Backstop: if the execute turn left uncommitted changes in the worktree,
    finalize should commit them so the worktree branch carries the work."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    # Simulate the worktree the harness would create on ack: clone into a
    # sibling dir on a new branch with uncommitted changes (the "agent forgot
    # to commit" scenario).
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "task/x", str(wt)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (wt / "f.txt").write_text("changed by agent", encoding="utf-8")
    base_sha = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", "HEAD"]
    ).decode().strip()

    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id,
            title="implement feature x",
            prompt="p",
            status="running",
            phase="executing",
            mode="plan_then_execute",
            worktree_path=str(wt),
            worktree_branch="task/x",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id,
            title="run",
            task_id=t.id,
            kind="execute",
            cwd=str(wt),
        )
        s.add(job)
        s.flush()
        jid = job.id

    task_runner.on_job_finalized(jid, "done", log_dir=None)

    new_sha = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", "HEAD"]
    ).decode().strip()
    assert new_sha != base_sha, "expected backstop to create a commit"
    msg = subprocess.check_output(
        ["git", "-C", str(wt), "log", "-1", "--format=%s"]
    ).decode().strip()
    assert msg == "implement feature x"
    with session_scope() as s:
        outcome = s.query(models.Outcome).one()
        assert outcome.commit_sha == new_sha
        assert outcome.branch == "task/x"


def test_executing_finalize_skips_commit_when_clean(
    initdb: Path, tmp_path: Path
) -> None:
    """If the agent already committed (or there are no changes), the backstop
    must not create an empty commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "task/y", str(wt)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_sha = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", "HEAD"]
    ).decode().strip()

    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id,
            title="t",
            prompt="p",
            status="running",
            phase="executing",
            mode="plan_then_execute",
            worktree_path=str(wt),
            worktree_branch="task/y",
        )
        s.add(t)
        s.flush()
        job = models.Job(
            project_id=proj.id,
            task_id=t.id,
            kind="execute",
            cwd=str(wt),
        )
        s.add(job)
        s.flush()
        jid = job.id

    task_runner.on_job_finalized(jid, "done", log_dir=None)
    new_sha = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", "HEAD"]
    ).decode().strip()
    assert new_sha == base_sha


def _make_integration_fixture(tmp_path: Path):
    """Repo + two input tasks on real worktree branches with one commit each,
    plus a synthetic integration task depending on both. Returns (repo, tid_a,
    tid_b, synth_id). Caller controls whether the target branch actually
    merges the inputs to test landed-vs-not-landed paths."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    branches: list[tuple[str, str, str]] = []
    for label in ("a", "b"):
        wt = tmp_path / f"wt-{label}"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", f"task/{label}", str(wt)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        (wt / f"{label}.txt").write_text("hi", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(wt), "add", "-A"], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-C", str(wt), "-c", "commit.gpgsign=false", "commit", "-m", f"work-{label}"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        branches.append((label, str(wt), f"task/{label}"))

    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        inputs = []
        for label, wt_path, br in branches:
            t = models.Task(
                project_id=proj.id, title=f"input-{label}", prompt="p",
                status="done", phase="done", mode="plan_then_execute",
                worktree_path=wt_path, worktree_branch=br,
                integration_status="pending",
            )
            s.add(t)
            s.flush()
            inputs.append(t.id)
        synth = models.Task(
            project_id=proj.id, title="integrate", prompt="merge them",
            status="running", phase="integrating",
            mode="one_shot", synthetic=True,
        )
        s.add(synth)
        s.flush()
        for in_id in inputs:
            s.add(models.TaskDependency(task_id=synth.id, depends_on_id=in_id))
        job = models.Job(
            project_id=proj.id, task_id=synth.id,
            kind="integrate", cwd=str(repo),
        )
        s.add(job)
        s.flush()
        return repo, inputs[0], inputs[1], synth.id, job.id


def test_integration_finalize_succeeds_when_target_has_merged(
    initdb: Path, tmp_path: Path
) -> None:
    """When the agent did merge the input branches into a target branch, the
    inputs' tips are reachable from another branch — finalize should mark
    integration success and clean up."""
    repo, tid_a, tid_b, synth_id, jid = _make_integration_fixture(tmp_path)
    # Simulate the agent's work: create harness-test/target and merge both
    # input branches into it.
    subprocess.run(
        ["git", "-C", str(repo), "branch", "harness-test/target", "main"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "harness-test/target"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for br in ("task/a", "task/b"):
        subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false",
             "merge", "--no-ff", "--no-edit", br],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    task_runner.on_job_finalized(jid, "done", log_dir=None)

    with session_scope() as s:
        outcome = s.query(models.Outcome).one()
        assert outcome.status == "success"
        assert outcome.kind == "integrate"
        assert s.get(models.Task, tid_a).integration_status == "integrated"
        assert s.get(models.Task, tid_b).integration_status == "integrated"
        # Worktree fields cleared post-cleanup.
        assert s.get(models.Task, tid_a).worktree_branch is None


def test_integration_finalize_fails_when_no_merge_happened(
    initdb: Path, tmp_path: Path
) -> None:
    """If the agent exited cleanly without actually merging (e.g. blocked on
    permissions and gave up), finalize must NOT mark success — that would
    silently orphan the work. Inputs stay 'conflict' and branches survive."""
    repo, tid_a, tid_b, synth_id, jid = _make_integration_fixture(tmp_path)
    # No merge done. Just call finalize with the clean exit status.
    task_runner.on_job_finalized(jid, "done", log_dir=None)

    with session_scope() as s:
        outcome = s.query(models.Outcome).one()
        assert outcome.status == "failed"
        assert outcome.kind == "integrate"
        # Inputs are marked conflict, not integrated.
        assert s.get(models.Task, tid_a).integration_status == "conflict"
        assert s.get(models.Task, tid_b).integration_status == "conflict"
        # Branches survive (worktree fields retained).
        assert s.get(models.Task, tid_a).worktree_branch == "task/a"


def test_advance_to_executing_creates_worktree_and_flips_phase(
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
            phase="awaiting_ack",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        tid = t.id

    spawn = task_runner.advance_to_executing(tid, prompt_addendum="also handle errors")

    assert "do the thing" in spawn.prompt
    assert "also handle errors" in spawn.prompt
    with session_scope() as s:
        task = s.get(models.Task, tid)
        assert task.phase == "executing"
        assert task.worktree_path is not None
        assert task.worktree_branch == f"task/{tid}"
        wt = Path(task.worktree_path)
        assert wt.is_dir()
        assert (wt / "f.txt").exists()
        # The returned spawn.cwd matches the worktree — the route will use it
        # for the new Execute Job.
        assert spawn.cwd == task.worktree_path


def test_advance_to_executing_rejects_wrong_phase(initdb: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(
            project_id=proj.id, title="t", prompt="p", status="running",
            phase="planning",
            mode="plan_then_execute",
        )
        s.add(t)
        s.flush()
        tid = t.id
    import pytest

    with pytest.raises(ValueError, match="not awaiting ack"):
        task_runner.advance_to_executing(tid)


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
