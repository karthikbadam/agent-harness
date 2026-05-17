"""Task runner: record outcomes and propagate task status when jobs finalize.

This is a small *status reconciler*, not an executor. It does not auto-enqueue
new jobs — the user must call `POST /api/tasks/{id}/run` for each ready task.

Responsibilities:
- `on_job_finalized(job_id, status)` is called by `JobManager._finalize_turn`
  for jobs that have a `task_id` set. It captures the project's git HEAD,
  records an `Outcome` row, flips the task status to `done`/`failed`, and
  re-evaluates downstream tasks (`pending` → `ready` when deps are done).
- `reconcile_on_startup()` flips orphaned task statuses (e.g. a `running`
  task whose job ended via the reconciler) and ensures pending/ready states
  reflect current dep statuses.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .. import models
from ..db import session_scope
from . import driver_bus, worktrees

log = logging.getLogger(__name__)


def _git_head(project_path: str) -> tuple[str | None, str | None]:
    """Return (commit_sha, branch) for the project's working dir, or (None, None)."""
    pdir = Path(project_path)
    if not pdir.is_dir() or not (pdir / ".git").exists():
        return None, None
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(pdir), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        sha = None
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(pdir), "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        branch = None
    return sha, branch


def _branch_tip(project_path: str, branch: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", project_path, "rev-parse", branch],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _commit_reachable_from_other_branch(
    project_path: str, sha: str, exclude_branch: str
) -> bool:
    """Return True iff ``sha`` is reachable from some local branch other than
    ``exclude_branch``. Used to verify an integration actually moved each input
    task's work somewhere persistent before we delete its task branch."""
    try:
        names = subprocess.check_output(
            [
                "git",
                "-C",
                project_path,
                "branch",
                "--contains",
                sha,
                "--format=%(refname:short)",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().splitlines()
    except Exception:  # noqa: BLE001
        return False
    return any(n.strip() and n.strip() != exclude_branch for n in names)


def _integration_actually_landed(project_path: str, input_tasks) -> bool:
    """Defensive check at integration finalize: for every input task whose
    worktree branch carries commits past the base, those commits must now be
    reachable from another local branch (the merge target). If any task's tip
    is only reachable via its own task/<id> branch, the integration didn't
    actually merge — the agent gave up or hit a permission wall. We refuse to
    declare success and wipe the input branches in that case."""
    if not project_path:
        return False
    for dep in input_tasks:
        branch = dep.worktree_branch
        if not branch:
            continue
        tip = _branch_tip(project_path, branch)
        if tip is None:
            continue
        if not _commit_reachable_from_other_branch(project_path, tip, branch):
            return False
    return True


def _commit_dirty_worktree(cwd: str, message: str) -> bool:
    """If ``cwd`` is a git worktree with uncommitted changes, stage and commit
    them with ``message``. Returns True if a commit was created. Used as a
    backstop after the execute turn so the worktree branch always carries the
    agent's work — even if the agent forgot to commit or got stuck on
    permission gates.
    """
    pdir = Path(cwd)
    if not pdir.is_dir() or not (pdir / ".git").exists():
        return False
    try:
        status = subprocess.check_output(
            ["git", "-C", str(pdir), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
    except Exception:  # noqa: BLE001
        return False
    if not status.strip():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(pdir), "add", "-A"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        subprocess.run(
            ["git", "-C", str(pdir), "commit", "-m", message],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("auto-commit failed in %s: %s", cwd, e)
        return False
    log.info("auto-committed dirty worktree at %s", cwd)
    return True


def _last_assistant_text(log_dir: Path) -> str | None:
    """Scan turn-*.jsonl for the most recent assistant_text payload."""
    import json

    if not log_dir.is_dir():
        return None
    files = sorted(log_dir.glob("turn-*.jsonl"))
    last: str | None = None
    for f in files:
        try:
            with f.open("rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or '"assistant_text"' not in line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if payload.get("type") != "assistant_text":
                        continue
                    text = payload.get("text")
                    if isinstance(text, str) and text.strip():
                        last = text
        except Exception:  # noqa: BLE001
            continue
    return last


def _deps_of(s, task_id: str) -> list[str]:
    rows = s.execute(
        select(models.TaskDependency.depends_on_id).where(
            models.TaskDependency.task_id == task_id
        )
    ).all()
    return [r[0] for r in rows]


def _all_deps_done(s, task_id: str) -> bool:
    dep_ids = _deps_of(s, task_id)
    if not dep_ids:
        return True
    rows = s.execute(
        select(models.Task.status).where(models.Task.id.in_(dep_ids))
    ).all()
    statuses = [r[0] for r in rows]
    if len(statuses) != len(dep_ids):
        return False
    return all(st == "done" for st in statuses)


def _reevaluate_downstream(s, task_id: str) -> list[tuple[str, str]]:
    """Flip eligible downstream tasks to 'ready'. Returns (task_id, project_id)
    pairs for tasks that transitioned, so the caller can emit driver events.
    """
    transitions: list[tuple[str, str]] = []
    rows = s.execute(
        select(models.TaskDependency.task_id).where(
            models.TaskDependency.depends_on_id == task_id
        )
    ).all()
    for (downstream_id,) in rows:
        ds = s.get(models.Task, downstream_id)
        if ds is None:
            continue
        if ds.status == "pending" and _all_deps_done(s, downstream_id):
            ds.status = "ready"
            transitions.append((ds.id, ds.project_id))
    return transitions


def on_job_finalized(
    job_id: str,
    job_status: str,
    log_dir: Path | None = None,
) -> None:
    """Record an Outcome and advance the owning Task's phase.

    No-op if the job has no ``task_id``. The Job's ``kind`` selects the path:

    - ``kind='plan'``: on clean exit, park the Task at ``phase='awaiting_ack'``
      with an ``Outcome(kind='plan')``; on failure flip the Task to ``failed``.
      Downstream tasks are NOT re-evaluated (execute hasn't happened yet).
    - ``kind='execute'``: backstop-commit any leftover dirty state in the
      worktree, record ``Outcome(kind='execute')``, flip the Task to
      ``done``/``failed`` and propagate readiness to dependents. For
      plan_then_execute Tasks the worktree carries the work for integration.
    - ``kind='integrate'`` (synthetic Task): verify the input branches'
      tips are reachable from another branch; on success cleanup input
      worktrees and mark them ``integrated``; on failure leave them and
      mark ``conflict``.
    """
    bus = driver_bus.get_bus()
    pending_events: list[tuple[str, dict]] = []  # collected, emitted after commit
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        if job is None or job.task_id is None:
            return
        task = s.get(models.Task, job.task_id)
        if task is None:
            log.warning("task %s missing for job %s; outcome skipped", job.task_id, job_id)
            return
        project = s.get(models.Project, job.project_id)
        project_id = job.project_id

        if job.kind == "plan":
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
            if job_status == "done":
                # Plan ran cleanly. Park the Task at awaiting_ack; the user
                # (or driver) calls /tasks/{id}/ack to advance to execute.
                task.phase = "awaiting_ack"
                s.add(
                    models.Outcome(
                        task_id=task.id,
                        job_id=job.id,
                        commit_sha=None,
                        branch=None,
                        summary=summary,
                        status="success",
                        kind="plan",
                    )
                )
                pending_events.append(
                    (
                        "plan_ready",
                        {"project_id": project_id, "task_id": task.id, "job_id": job.id},
                    )
                )
            else:
                task.phase = "failed"
                task.status = "failed"
                task.last_failed_at = datetime.now(timezone.utc)
                s.add(
                    models.Outcome(
                        task_id=task.id,
                        job_id=job.id,
                        commit_sha=None,
                        branch=None,
                        summary=summary,
                        status="failed",
                        kind="plan",
                    )
                )
                pending_events.append(
                    (
                        "task_failed",
                        {"project_id": project_id, "task_id": task.id, "job_id": job.id},
                    )
                )
            _emit_after(bus, pending_events)
            return

        if job.kind == "integrate":
            cwd = job.cwd or (project.path if project else None)
            sha, branch = (_git_head(cwd) if cwd else (None, None))
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
            dep_rows = s.execute(
                select(models.TaskDependency.depends_on_id).where(
                    models.TaskDependency.task_id == task.id
                )
            ).all()
            input_tasks = [s.get(models.Task, dep_id) for (dep_id,) in dep_rows]
            input_tasks = [t for t in input_tasks if t is not None]
            merge_landed = job_status == "done" and _integration_actually_landed(
                cwd or "", input_tasks
            )
            outcome_status = "success" if merge_landed else "failed"
            if job_status == "done" and not merge_landed:
                log.warning(
                    "integration job %s exited clean but no input branch was merged; "
                    "marking outcome failed and preserving task branches",
                    job.id,
                )
            s.add(
                models.Outcome(
                    task_id=task.id,
                    job_id=job.id,
                    commit_sha=sha,
                    branch=branch,
                    summary=summary,
                    status=outcome_status,
                    kind="integrate",
                )
            )
            task.phase = "done" if merge_landed else "failed"
            task.status = "done" if merge_landed else "failed"
            if not merge_landed:
                task.last_failed_at = datetime.now(timezone.utc)
            for dep in input_tasks:
                if merge_landed:
                    if project is not None and (dep.worktree_path or dep.worktree_branch):
                        worktrees.remove(project, dep)
                        dep.worktree_path = None
                        dep.worktree_branch = None
                    dep.integration_status = "integrated"
                else:
                    dep.integration_status = "conflict"
            s.flush()
            transitions = _reevaluate_downstream(s, task.id)
            event = "integration_done" if merge_landed else "integration_conflict"
            pending_events.append(
                (event, {"project_id": project_id, "task_id": task.id, "job_id": job.id})
            )
            for tid, pid in transitions:
                pending_events.append(("task_ready", {"project_id": pid, "task_id": tid}))
            _emit_after(bus, pending_events)
            return

        # Execute / one-shot / ad-hoc (kind='execute' or 'ad_hoc'). The job's
        # cwd is the worktree path for plan-then-execute tasks; project.path
        # for one-shot tasks; whatever the caller specified for ad-hoc.
        cwd = job.cwd or (project.path if project else None)
        summary = _last_assistant_text(log_dir) if log_dir is not None else None
        in_worktree = bool(
            cwd and project and cwd != project.path
        )
        if job_status == "done" and in_worktree and not task.synthetic:
            commit_msg = (task.title or "agent commit")[:72]
            _commit_dirty_worktree(cwd, commit_msg)
        sha, branch = (_git_head(cwd) if cwd else (None, None))
        outcome_status = "success" if job_status == "done" else "failed"
        s.add(
            models.Outcome(
                task_id=task.id,
                job_id=job.id,
                commit_sha=sha,
                branch=branch,
                summary=summary,
                status=outcome_status,
                kind="execute",
            )
        )
        task.status = "done" if job_status == "done" else "failed"
        task.phase = "done" if job_status == "done" else "failed"
        if task.status == "failed":
            task.last_failed_at = datetime.now(timezone.utc)
        if task.status == "done" and task.mode == "plan_then_execute" and not task.synthetic:
            task.integration_status = "pending"
        s.flush()
        transitions = _reevaluate_downstream(s, task.id)
        event = "task_done" if task.status == "done" else "task_failed"
        pending_events.append(
            (event, {"project_id": project_id, "task_id": task.id, "job_id": job.id})
        )
        for tid, pid in transitions:
            pending_events.append(("task_ready", {"project_id": pid, "task_id": tid}))
    _emit_after(bus, pending_events)


def _emit_after(bus: "driver_bus.DriverEventBus", events: list[tuple[str, dict]]) -> None:
    for event, kw in events:
        bus.emit(event, **kw)


class ExecuteSpawn:
    """Inputs ``JobManager.create_job`` needs to spawn the execute Job after
    a plan was acked. ``advance_to_executing`` returns this rather than
    spawning the Job itself, so the route layer keeps the manager dependency.
    """

    __slots__ = ("task_id", "project_id", "prompt", "cwd", "title")

    def __init__(
        self,
        task_id: str,
        project_id: str,
        prompt: str,
        cwd: str,
        title: str,
    ) -> None:
        self.task_id = task_id
        self.project_id = project_id
        self.prompt = prompt
        self.cwd = cwd
        self.title = title


def advance_to_executing(task_id: str, prompt_addendum: str = "") -> ExecuteSpawn:
    """Transition a Task from ``awaiting_ack`` to ``executing``.

    Creates the per-task git worktree, records its path + branch on the task,
    flips ``task.phase='executing'``. Returns an :class:`ExecuteSpawn` the
    route layer feeds into ``JobManager.create_job`` to start the execute
    Job (its own conversation, born at the worktree cwd — no resume from the
    plan Job).
    """
    with session_scope() as s:
        task = s.get(models.Task, task_id)
        if task is None:
            raise ValueError(f"unknown task {task_id}")
        if task.phase != "awaiting_ack":
            raise ValueError(
                f"task {task_id} is not awaiting ack (phase={task.phase!r})"
            )
        project = s.get(models.Project, task.project_id)
        if project is None:
            raise ValueError(f"project {task.project_id} for task {task_id} not found")
        if task.worktree_path and task.worktree_branch:
            # A previous ack already created the worktree (e.g. retry of just
            # the execute Job). Reuse it.
            path, branch = task.worktree_path, task.worktree_branch
        else:
            path, branch = worktrees.create(project, task)
            task.worktree_path = path
            task.worktree_branch = branch
        task.phase = "executing"
        base = task.prompt
        full_prompt = f"{base}\n\n{prompt_addendum}" if prompt_addendum else base
        title = f"[task] {task.title}"[:256]
        return ExecuteSpawn(
            task_id=task_id,
            project_id=project.id,
            prompt=full_prompt,
            cwd=path,
            title=title,
        )


def reconcile_on_startup() -> None:
    """Idempotent: refresh pending/ready statuses based on current dep states.

    A task that was `running` when the server died is left as-is — the job
    reconciler will mark its job `stopped`, and the next finalize call (or
    a user re-run) will pick it up. We only flip pending↔ready here.
    """
    with session_scope() as s:
        rows = (
            s.query(models.Task)
            .filter(models.Task.status.in_(["pending", "ready"]))
            .all()
        )
        for t in rows:
            new = "ready" if _all_deps_done(s, t.id) else "pending"
            if t.status != new:
                t.status = new
