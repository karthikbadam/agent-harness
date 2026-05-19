"""Planner route.

POST /api/projects/{id}/plan {ask} runs a one-off claude job that returns a
JSON list of draft tasks. Drafts are inserted as ready (no deps) or pending
(waiting on a predecessor), and any ready ones are auto-kicked.

GET /api/projects/{id}/plan returns the most recent planner run for the UI's
"view plan" affordance.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..config import get_settings
from ..db import get_session
from ..jobs import JobManager
from ..schemas import LastPlanOut, PlanCreate, PlanOut
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


_PLANNER_ASK_MARKER = "\n\nAsk:\n"


@router.get("/api/projects/{project_id}/plan", response_model=LastPlanOut | None)
def last_plan(
    project_id: str, s: Session = Depends(get_session)
) -> LastPlanOut | None:
    """Return the most recent planner run for this project, if any."""
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    # Planner jobs are ad_hoc Jobs whose title is "[plan] <truncated ask>".
    job = (
        s.execute(
            select(models.Job)
            .where(
                models.Job.project_id == project_id,
                models.Job.title.like("[plan] %"),
                models.Job.task_id.is_(None),
            )
            .order_by(models.Job.created_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    if job is None:
        return None
    # The full ask + raw planner output live in turn-0's prompt/log. Rebuild
    # them from DB so the UI doesn't need to re-read jsonl files.
    turn = (
        s.execute(
            select(models.Turn).where(
                models.Turn.job_id == job.id, models.Turn.idx == 0
            )
        )
        .scalar_one_or_none()
    )
    ask = ""
    if turn is not None and _PLANNER_ASK_MARKER in (turn.prompt or ""):
        ask = (turn.prompt or "").split(_PLANNER_ASK_MARKER, 1)[1].strip()
    # Read the assistant text from the job's log dir.
    raw = _assistant_text_for_job(job.id)
    # Find tasks created shortly after this job (planner inserts immediately
    # after the job finishes). Match by source='planner' and a created_at
    # within 60s of the job's ended_at (or current time).
    task_ids = _tasks_from_planner_job(s, project_id, job)
    return LastPlanOut(
        job_id=job.id,
        ask=ask,
        raw=raw,
        created_at=job.created_at,
        task_ids=task_ids,
    )


def _assistant_text_for_job(job_id: str) -> str:
    """Concatenate all assistant_text events from a job's turn-*.jsonl logs."""
    import json

    settings = get_settings()
    assert settings.logs_dir is not None
    log_dir = settings.logs_dir / "jobs" / job_id
    if not log_dir.is_dir():
        return ""
    pieces: list[str] = []
    for f in sorted(log_dir.glob("turn-*.jsonl")):
        try:
            with f.open("rb") as fh:
                for raw_line in fh:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or '"assistant_text"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if ev.get("type") != "assistant_text":
                        continue
                    t = ev.get("text")
                    if isinstance(t, str):
                        pieces.append(t)
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(pieces)


def _tasks_from_planner_job(
    s: Session, project_id: str, plan_job: models.Job
) -> list[str]:
    """Return planner-sourced task ids created shortly after this plan job
    finished. The planner inserts drafts synchronously after job.wait, so
    the window is small (a few seconds in practice).
    """
    from datetime import timedelta

    anchor = plan_job.ended_at or plan_job.created_at
    window = anchor + timedelta(minutes=5)
    rows = s.execute(
        select(models.Task.id)
        .where(
            models.Task.project_id == project_id,
            models.Task.source == "planner",
            models.Task.created_at >= anchor,
            models.Task.created_at <= window,
        )
        .order_by(models.Task.order_idx, models.Task.created_at)
    ).all()
    return [r[0] for r in rows]
