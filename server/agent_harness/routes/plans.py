"""Planner route.

POST /api/projects/{id}/plan {ask} creates a ``mode='plan'`` Task and kicks
off its planner Job. The task appears in the project's task list immediately;
the user can click in to see the live planner stream. When the job finishes,
``task_runner.on_job_finalized`` parses the JSON task array and inserts the
child tasks (or a fallback ``research`` task if the planner emitted nothing).

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
from ..routes.attachments import stamp_job_on_attachments

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
    ask = body.ask.strip()
    if not ask:
        raise HTTPException(400, "ask cannot be empty")
    mgr = _manager(request)
    # Materialise the plan as a first-class Task so it renders on the project
    # page with the same TaskCard/JobStream UI as every other task. The plan
    # Job is kicked asynchronously; child tasks appear once it finishes.
    task = models.Task(
        project_id=project_id,
        title=f"Plan: {ask[:60]}",
        prompt=ask,
        status="ready",
        source="user",
        mode="plan",
        agent_provider=body.agent_provider,
    )
    s.add(task)
    s.commit()
    task_id = task.id
    from ..services import task_runner

    jid = await task_runner.kickoff_first_phase(task_id, mgr)
    if jid and body.attachment_ids:
        stamp_job_on_attachments(body.attachment_ids, jid, project_id, s)
        s.commit()
    return PlanOut(task_ids=[task_id], raw=None, error=None)


@router.get("/api/projects/{project_id}/plan", response_model=LastPlanOut | None)
def last_plan(project_id: str, s: Session = Depends(get_session)) -> LastPlanOut | None:
    """Return the most recent planner run for this project, if any.

    A planner run is a Task with ``mode='plan'``; its prompt is the user's ask
    and its bound Job's assistant_text is the planner's output.
    """
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    plan_task = s.execute(
        select(models.Task)
        .where(
            models.Task.project_id == project_id,
            models.Task.mode == "plan",
        )
        .order_by(models.Task.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if plan_task is None:
        return None
    job = s.execute(
        select(models.Job)
        .where(models.Job.task_id == plan_task.id, models.Job.kind == "plan")
        .order_by(models.Job.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    raw = _assistant_text_for_job(job.id) if job is not None else ""
    # Child tasks were inserted by task_runner shortly after the plan job
    # finished; match by source='planner' inside the plan task's time window.
    task_ids = _tasks_after(s, project_id, plan_task)
    return LastPlanOut(
        job_id=job.id if job is not None else plan_task.id,
        ask=plan_task.prompt,
        raw=raw,
        created_at=plan_task.created_at,
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


def _tasks_after(s: Session, project_id: str, plan_task: models.Task) -> list[str]:
    """Return planner-sourced child task ids created during this plan task's
    lifetime. ``on_job_finalized`` inserts them immediately after the plan
    job finishes, so a generous window catches them all.
    """
    from datetime import timedelta

    anchor = plan_task.created_at
    window = anchor + timedelta(hours=1)
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
