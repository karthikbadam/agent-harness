"""Pure decision function: what should the driver do for this project, now.

Shared by:
- ``driver_runtime`` (autopilot, mode=on): dispatches each Action via REST.
- ``routes/driver.py`` GET ``/suggestions`` (copilot, mode=off): returns the
  same Actions so the UI can render one-tap buttons.

This module reads DB state and computes. It does NOT call out to the harness,
spawn jobs, or mutate anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

MAX_RETRIES = 2
RETRY_BACKOFF_BASE_SECONDS = 60
RETRY_BACKOFF_FACTOR = 3  # → 60s, 180s, 540s


@dataclass
class Action:
    kind: Literal["ack", "retry", "integrate", "run"]
    project_id: str
    task_id: str | None = None
    job_id: str | None = None
    reason: str = ""
    rest_verb: str = "POST"
    rest_path: str = ""
    payload: dict | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _retry_backoff(retries: int) -> timedelta:
    return timedelta(
        seconds=RETRY_BACKOFF_BASE_SECONDS * (RETRY_BACKOFF_FACTOR**retries)
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _running_count(s: Session, project_id: str) -> int:
    return len(
        s.execute(
            select(models.Task.id).where(
                models.Task.project_id == project_id,
                models.Task.status == "running",
            )
        ).all()
    )


def _integration_running(s: Session, project_id: str) -> bool:
    return bool(
        s.execute(
            select(models.Task.id).where(
                models.Task.project_id == project_id,
                models.Task.synthetic.is_(True),
                models.Task.status == "running",
            )
        ).first()
    )


def _ancestors_all_integrated(
    s: Session, task_id: str, visited: set[str] | None = None
) -> bool:
    """True if every dep-chain ancestor is integrated (or a done synthetic)."""
    visited = visited if visited is not None else set()
    deps = s.execute(
        select(models.TaskDependency.depends_on_id).where(
            models.TaskDependency.task_id == task_id
        )
    ).all()
    for (dep_id,) in deps:
        if dep_id in visited:
            continue
        visited.add(dep_id)
        dep = s.get(models.Task, dep_id)
        if dep is None:
            continue
        if dep.synthetic:
            if dep.status != "done":
                return False
        else:
            if dep.integration_status != "integrated":
                return False
        if not _ancestors_all_integrated(s, dep_id, visited):
            return False
    return True


def _wave(s: Session, project_id: str) -> list[str]:
    """Return the maximal set of done+pending tasks whose ancestors are all
    integrated. Empty if nothing is integratable yet.
    """
    rows = s.execute(
        select(models.Task.id).where(
            models.Task.project_id == project_id,
            models.Task.status == "done",
            models.Task.integration_status == "pending",
            models.Task.synthetic.is_(False),
        )
    ).all()
    return [r[0] for r in rows if _ancestors_all_integrated(s, r[0])]


def next_actions(
    s: Session,
    project_id: str,
    *,
    max_actions: int = 8,
    parallel_cap: int = 2,
) -> list[Action]:
    """Compute the prioritized list of actions for ``project_id`` right now.

    Priorities: 1. ack plans, 2. retry failed tasks (with backoff),
    3. integrate complete waves, 4. run ready tasks.
    """
    actions: list[Action] = []

    def _full() -> bool:
        return len(actions) >= max_actions

    # 1. ack — Tasks parked at awaiting_ack waiting for the execute Job to spawn
    tasks_awaiting = (
        s.execute(
            select(models.Task).where(
                models.Task.project_id == project_id,
                models.Task.phase == "awaiting_ack",
            )
        )
        .scalars()
        .all()
    )
    for task in tasks_awaiting:
        actions.append(
            Action(
                kind="ack",
                project_id=project_id,
                task_id=task.id,
                job_id=None,
                reason=f"plan ready for ack on task {task.id}",
                rest_path=f"/api/tasks/{task.id}/ack",
                payload={},
            )
        )
        if _full():
            return actions

    # 2. retry
    failed = (
        s.execute(
            select(models.Task).where(
                models.Task.project_id == project_id,
                models.Task.status == "failed",
                models.Task.synthetic.is_(False),
            )
        )
        .scalars()
        .all()
    )
    now = _now()
    for t in failed:
        retries = t.retries or 0
        if retries >= MAX_RETRIES:
            continue
        if t.last_failed_at is not None:
            since = now - _aware(t.last_failed_at)
            if since < _retry_backoff(retries):
                continue
        actions.append(
            Action(
                kind="retry",
                project_id=project_id,
                task_id=t.id,
                reason=f"retry {retries + 1}/{MAX_RETRIES} for failed task {t.id}",
                rest_path=f"/api/tasks/{t.id}/retry",
            )
        )
        if _full():
            return actions

    # 3. integrate
    if not _integration_running(s, project_id):
        wave = _wave(s, project_id)
        if wave:
            actions.append(
                Action(
                    kind="integrate",
                    project_id=project_id,
                    reason=f"integrate wave of {len(wave)} task(s)",
                    rest_path=f"/api/projects/{project_id}/integrate",
                    payload={"task_ids": wave, "target_branch": None},
                )
            )
            if _full():
                return actions

    # 4. run ready
    slots = max(0, parallel_cap - _running_count(s, project_id))
    if slots > 0:
        ready = (
            s.execute(
                select(models.Task)
                .where(
                    models.Task.project_id == project_id,
                    models.Task.status == "ready",
                )
                .order_by(models.Task.order_idx, models.Task.created_at)
                .limit(slots)
            )
            .scalars()
            .all()
        )
        for t in ready:
            actions.append(
                Action(
                    kind="run",
                    project_id=project_id,
                    task_id=t.id,
                    reason=f"run ready task '{t.title}'",
                    rest_path=f"/api/tasks/{t.id}/run",
                )
            )
            if _full():
                return actions

    return actions
