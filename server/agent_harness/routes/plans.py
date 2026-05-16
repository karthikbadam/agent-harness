"""Planner route.

POST /api/projects/{id}/plan {ask} runs a one-off claude job that returns a
JSON list of draft tasks. Drafts are inserted with status=pending so the
user can edit/confirm them before running.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..config import get_settings
from ..db import get_session
from ..jobs import JobManager
from ..schemas import PlanCreate, PlanOut
from ..services import planner

router = APIRouter(tags=["plans"], dependencies=[Depends(require_auth)])


def _manager(request: Request) -> JobManager:
    mgr = getattr(request.app.state, "job_manager", None)
    if mgr is None:
        raise HTTPException(503, "job manager not initialised")
    return mgr


@router.post("/api/projects/{project_id}/plan", response_model=PlanOut)
async def plan_project(
    project_id: str,
    body: PlanCreate,
    request: Request,
    s: Session = Depends(get_session),
) -> PlanOut:
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    if not body.ask.strip():
        raise HTTPException(400, "ask cannot be empty")
    settings = get_settings()
    assert settings.logs_dir is not None
    mgr = _manager(request)
    task_ids, raw, err = await planner.plan(
        project_id=project_id,
        ask=body.ask,
        job_manager=mgr,
        log_root=settings.logs_dir,
    )
    return PlanOut(task_ids=task_ids, raw=raw, error=err)
