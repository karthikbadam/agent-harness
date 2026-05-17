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
    """Record an Outcome and propagate task status. Safe to call always.

    No-op if the job has no ``task_id``. Branches on ``job.phase``:

    - ``awaiting_ack`` (planning turn just finished): write
      ``Outcome(kind='plan')`` capturing the plan text; do NOT mark the task
      ``done`` — it stays ``running`` while the user/agent reviews. Downstream
      tasks are NOT re-evaluated; the execute turn hasn't happened yet.
    - ``done`` after ``executing`` or ad-hoc (``phase`` was NULL): existing v1
      behavior — write ``Outcome(kind='execute')``, flip the task to ``done``
      or ``failed``, and propagate readiness to dependents. For plan-then-
      execute tasks the worktree branch is recorded so integration can find it.
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

        if job.phase == "awaiting_ack":
            # Planning turn just finished cleanly. Capture the plan text; keep
            # the task in 'running' so the orchestrator/human still sees it as
            # an in-flight commitment, just paused for ack.
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
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
                ("plan_ready", {"project_id": project_id, "task_id": task.id, "job_id": job.id})
            )
            _emit_after(bus, pending_events)
            return

        if task.synthetic:
            # Integration task. Outcome.kind='integrate', clean up the input
            # tasks' worktrees on success, mark them integrated. On failure,
            # mark inputs as 'conflict' so the orchestrator/human can followup.
            cwd = project.path if project else None
            sha, branch = (_git_head(cwd) if cwd else (None, None))
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
            outcome_status = "success" if job_status == "done" else "failed"
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
            task.status = "done" if job_status == "done" else "failed"
            if task.status == "failed":
                task.last_failed_at = datetime.now(timezone.utc)
            dep_rows = s.execute(
                select(models.TaskDependency.depends_on_id).where(
                    models.TaskDependency.task_id == task.id
                )
            ).all()
            for (dep_id,) in dep_rows:
                dep = s.get(models.Task, dep_id)
                if dep is None:
                    continue
                if job_status == "done":
                    if project is not None and (dep.worktree_path or dep.worktree_branch):
                        worktrees.remove(project, dep)
                        dep.worktree_path = None
                        dep.worktree_branch = None
                    dep.integration_status = "integrated"
                else:
                    dep.integration_status = "conflict"
            s.flush()
            transitions = _reevaluate_downstream(s, task.id)
            event = "integration_done" if job_status == "done" else "integration_conflict"
            pending_events.append(
                (event, {"project_id": project_id, "task_id": task.id, "job_id": job.id})
            )
            for tid, pid in transitions:
                pending_events.append(("task_ready", {"project_id": pid, "task_id": tid}))
            _emit_after(bus, pending_events)
            return

        # Execute / one-shot / ad-hoc path.
        cwd = job.cwd_override or (project.path if project else None)
        sha, branch = (_git_head(cwd) if cwd else (None, None))
        summary = _last_assistant_text(log_dir) if log_dir is not None else None
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


def on_ack(job_id: str, prompt_addendum: str = "") -> str:
    """Transition an ``awaiting_ack`` job into ``executing``.

    Creates the per-task git worktree, records its path + branch on the task,
    points the job's ``cwd_override`` at the worktree, and flips
    ``job.phase='executing'``. Returns the prompt the caller should pass to
    ``JobManager.followup`` for the execute turn (the original task prompt
    plus any optional addendum). The caller is responsible for actually
    enqueueing the followup; this function only handles DB state and worktree
    setup so it's safely callable from sync contexts (routes, MCP tools).
    """
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        if job is None:
            raise ValueError(f"unknown job {job_id}")
        if job.phase != "awaiting_ack":
            raise ValueError(
                f"job {job_id} is not awaiting ack (phase={job.phase!r})"
            )
        if job.task_id is None:
            raise ValueError(f"job {job_id} has no bound task")
        task = s.get(models.Task, job.task_id)
        if task is None:
            raise ValueError(f"task {job.task_id} for job {job_id} not found")
        project = s.get(models.Project, job.project_id)
        if project is None:
            raise ValueError(f"project {job.project_id} for job {job_id} not found")
        path, branch = worktrees.create(project, task)
        task.worktree_path = path
        task.worktree_branch = branch
        job.cwd_override = path
        job.phase = "executing"
        base = task.prompt
    return f"{base}\n\n{prompt_addendum}" if prompt_addendum else base


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
