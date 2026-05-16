"""Outcomes routes (read-only).

Outcomes are checkpoints created by the task runner when a task-bound job
finishes. They tie a task to the git commit-sha + branch at the time the job
ended, plus the assistant's closing summary.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schemas import OutcomeOut

router = APIRouter(tags=["outcomes"], dependencies=[Depends(require_auth)])


def _to_out(o: models.Outcome) -> OutcomeOut:
    return OutcomeOut(
        id=o.id,
        task_id=o.task_id,
        job_id=o.job_id,
        commit_sha=o.commit_sha,
        branch=o.branch,
        summary=o.summary,
        status=o.status,
        created_at=o.created_at,
    )


@router.get("/api/tasks/{task_id}/outcomes", response_model=list[OutcomeOut])
def list_task_outcomes(task_id: str, s: Session = Depends(get_session)) -> list[OutcomeOut]:
    if s.get(models.Task, task_id) is None:
        raise HTTPException(404, "unknown task")
    rows = (
        s.query(models.Outcome)
        .filter(models.Outcome.task_id == task_id)
        .order_by(models.Outcome.created_at.desc())
        .all()
    )
    return [_to_out(o) for o in rows]


@router.get("/api/projects/{project_id}/outcomes", response_model=list[OutcomeOut])
def list_project_outcomes(
    project_id: str, s: Session = Depends(get_session)
) -> list[OutcomeOut]:
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    # Filter outcomes via their task's project.
    rows = (
        s.query(models.Outcome)
        .join(models.Task, models.Outcome.task_id == models.Task.id)
        .filter(models.Task.project_id == project_id)
        .order_by(models.Outcome.created_at.desc())
        .all()
    )
    return [_to_out(o) for o in rows]
