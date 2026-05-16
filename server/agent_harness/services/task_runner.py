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
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from .. import models
from ..db import session_scope

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


def _reevaluate_downstream(s, task_id: str) -> None:
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


def on_job_finalized(
    job_id: str,
    job_status: str,
    log_dir: Path | None = None,
) -> None:
    """Record an Outcome and propagate task status. Safe to call always.

    No-op if the job has no `task_id`. `job_status` is one of the JobManager
    finalize statuses: done | failed | stopped.
    """
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        if job is None or job.task_id is None:
            return
        task = s.get(models.Task, job.task_id)
        if task is None:
            log.warning("task %s missing for job %s; outcome skipped", job.task_id, job_id)
            return
        project = s.get(models.Project, job.project_id)
        sha, branch = (_git_head(project.path) if project else (None, None))
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
            )
        )
        # Map job status to task status. `stopped` is treated as `failed` here
        # since the task did not produce a successful outcome; the user can
        # re-run the task manually if needed.
        task.status = "done" if job_status == "done" else "failed"
        s.flush()
        _reevaluate_downstream(s, task.id)


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
