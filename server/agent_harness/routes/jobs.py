from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from sqlalchemy import select

from .. import models
from ..auth import require_auth
from ..bootstrap import ensure_default_project
from ..config import get_settings
from ..db import get_session
from ..jobs import JobManager
from ..schemas import FollowupCreate, JobCreate, JobOut, TurnOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])


def _to_out(j: models.Job) -> JobOut:
    return JobOut(
        id=j.id,
        project_id=j.project_id,
        title=j.title,
        status=j.status,
        session_id=j.session_id,
        schedule_id=j.schedule_id,
        task_id=j.task_id,
        phase=j.phase,
        created_at=j.created_at,
        ended_at=j.ended_at,
        turns=[
            TurnOut(
                id=t.id,
                idx=t.idx,
                prompt=t.prompt,
                status=t.status,
                exit_code=t.exit_code,
                cost_usd=t.cost_usd,
                started_at=t.started_at,
                ended_at=t.ended_at,
            )
            for t in j.turns
        ],
    )


def _manager(request: Request) -> JobManager:
    mgr = getattr(request.app.state, "job_manager", None)
    if mgr is None:
        raise HTTPException(503, "job manager not initialised")
    return mgr


@router.get("", response_model=list[JobOut])
def list_jobs(s: Session = Depends(get_session)) -> list[JobOut]:
    rows = s.query(models.Job).order_by(models.Job.created_at.desc()).all()
    return [_to_out(j) for j in rows]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, s: Session = Depends(get_session)) -> JobOut:
    j = s.get(models.Job, job_id)
    if j is None:
        raise HTTPException(404, "not found")
    return _to_out(j)


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate, request: Request, s: Session = Depends(get_session)
) -> JobOut:
    mgr = _manager(request)
    project_id = body.project_id or _resolve_default_project_id(s)
    try:
        jid = mgr.create_job(project_id, body.prompt, body.title or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await mgr.start(jid)
    s.expire_all()
    j = s.get(models.Job, jid)
    assert j is not None
    return _to_out(j)


def _resolve_default_project_id(s: Session) -> str:
    row = s.execute(
        select(models.Project.id).where(models.Project.is_default.is_(True))
    ).first()
    if row is not None:
        return row[0]
    # Lazy bootstrap if startup hook didn't run (e.g. fresh DB created by a
    # request before lifespan completed in tests).
    return ensure_default_project()


@router.post("/{job_id}/followup", response_model=JobOut)
async def followup_job(
    job_id: str,
    body: FollowupCreate,
    request: Request,
    s: Session = Depends(get_session),
) -> JobOut:
    mgr = _manager(request)
    j = s.get(models.Job, job_id)
    if j is None:
        raise HTTPException(404, "not found")
    # If the job is parked at the plan-ack gate, treat this followup as the
    # ack: create the worktree, flip the phase, and enqueue the execute turn
    # with the original task prompt (plus any addendum the caller supplied).
    if j.phase == "awaiting_ack":
        from ..services import task_runner

        try:
            exec_prompt = task_runner.on_ack(
                job_id, prompt_addendum=body.prompt or ""
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            await mgr.followup(job_id, exec_prompt)
        except ValueError as e:
            raise HTTPException(400, str(e))
    else:
        try:
            await mgr.followup(job_id, body.prompt)
        except ValueError as e:
            raise HTTPException(400, str(e))
    s.expire_all()
    j = s.get(models.Job, job_id)
    assert j is not None
    return _to_out(j)


@router.post("/{job_id}/stop", response_model=JobOut)
async def stop_job(
    job_id: str, request: Request, s: Session = Depends(get_session)
) -> JobOut:
    mgr = _manager(request)
    j = s.get(models.Job, job_id)
    if j is None:
        raise HTTPException(404, "not found")
    await mgr.stop(job_id)
    s.expire_all()
    j = s.get(models.Job, job_id)
    assert j is not None
    return _to_out(j)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, s: Session = Depends(get_session)) -> None:
    j = s.get(models.Job, job_id)
    if j is None:
        raise HTTPException(404, "not found")
    if j.status in {"running", "queued"}:
        raise HTTPException(409, "stop the job first")
    s.delete(j)
    s.commit()
    settings = get_settings()
    assert settings.logs_dir is not None
    shutil.rmtree(settings.logs_dir / "jobs" / job_id, ignore_errors=True)
