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
from ..routes.attachments import stamp_job_on_attachments

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
        kind=j.kind,
        agent_provider=j.agent_provider,
        cwd=j.cwd,
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
                attachment_ids=list(t.attachment_ids or []),
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
        jid = mgr.create_job(
            project_id,
            body.prompt,
            body.title or "",
            attachment_ids=body.attachment_ids,
            agent_provider=body.agent_provider,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    stamp_job_on_attachments(body.attachment_ids, jid, project_id, s)
    s.commit()
    await mgr.start(jid)
    s.expire_all()
    j = s.get(models.Job, jid)
    assert j is not None
    return _to_out(j)


def _resolve_default_project_id(s: Session) -> str:
    row = s.execute(select(models.Project.id).where(models.Project.is_default.is_(True))).first()
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
    """Send a follow-up turn to an existing Job (same conversation, new turn).

    Followups stay on the same Job (same cwd, same session). To advance a
    plan-then-execute Task from awaiting_ack to executing — which spawns a
    NEW Execute Job in the worktree — call ``POST /api/tasks/{id}/ack``
    instead. For backward compatibility, a followup on a Plan Job that's
    bound to a Task at ``phase=awaiting_ack`` is routed to the ack flow.
    """
    mgr = _manager(request)
    j = s.get(models.Job, job_id)
    if j is None:
        raise HTTPException(404, "not found")
    # Back-compat: a followup on a plan_then_execute Task parked at awaiting_ack
    # means "ack the plan" → advance to executing. A top-level planner task
    # (mode='plan') parked at awaiting_ack is a GATED DRAFT — a followup there
    # is a steering re-plan, so let it fall through to the normal followup turn
    # (which re-runs the planner and replaces the drafts).
    if j.kind == "plan" and j.task_id is not None:
        task = s.get(models.Task, j.task_id)
        if task is not None and task.mode == "plan_then_execute" and task.phase == "awaiting_ack":
            from ..services import task_runner

            try:
                spawn = task_runner.advance_to_executing(
                    j.task_id, prompt_addendum=body.prompt or ""
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            try:
                new_jid = mgr.create_job(
                    project_id=spawn.project_id,
                    prompt=spawn.prompt,
                    title=spawn.title,
                    task_id=spawn.task_id,
                    kind="execute",
                    cwd=spawn.cwd,
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            await mgr.start(new_jid)
            s.expire_all()
            new_job = s.get(models.Job, new_jid)
            assert new_job is not None
            return _to_out(new_job)
    try:
        await mgr.followup(job_id, body.prompt, attachment_ids=body.attachment_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    s.expire_all()
    j = s.get(models.Job, job_id)
    assert j is not None
    stamp_job_on_attachments(body.attachment_ids, job_id, j.project_id, s)
    s.commit()
    return _to_out(j)


@router.post("/{job_id}/stop", response_model=JobOut)
async def stop_job(job_id: str, request: Request, s: Session = Depends(get_session)) -> JobOut:
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
    # Outcomes/artifacts/driver_notes FK to this job but aren't covered by the
    # Job.turns SQLA cascade, so a bare delete trips a FOREIGN KEY constraint
    # for any job that produced a checkpoint (every planner/execute job does).
    # Drain the outcome rows and detach the nullable references first.
    s.execute(models.Outcome.__table__.delete().where(models.Outcome.job_id == job_id))
    s.execute(
        models.Artifact.__table__.update()
        .where(models.Artifact.job_id == job_id)
        .values(job_id=None)
    )
    s.execute(
        models.DriverNote.__table__.update()
        .where(models.DriverNote.job_id == job_id)
        .values(job_id=None)
    )
    s.delete(j)
    s.commit()
    settings = get_settings()
    assert settings.logs_dir is not None
    shutil.rmtree(settings.logs_dir / "jobs" / job_id, ignore_errors=True)
