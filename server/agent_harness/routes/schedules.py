from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schedule_service import parse_cron
from ..schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate

router = APIRouter(
    prefix="/api/schedules", tags=["schedules"], dependencies=[Depends(require_auth)]
)


def _to_out(s: models.Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        project_id=s.project_id,
        name=s.name,
        cron=s.cron,
        prompt=s.prompt,
        enabled=s.enabled,
        created_at=s.created_at,
    )


def _validate_cron(expr: str) -> None:
    parts = expr.strip().split()
    if len(parts) not in (5, 6):
        raise HTTPException(400, "cron must have 5 fields (minute hour dom mon dow)")
    try:
        parse_cron(expr)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid cron expression: {e}")


def _service(request: Request):
    svc = getattr(request.app.state, "schedules", None)
    return svc


@router.get("", response_model=list[ScheduleOut])
def list_schedules(s: Session = Depends(get_session)) -> list[ScheduleOut]:
    return [_to_out(x) for x in s.query(models.Schedule).order_by(models.Schedule.created_at).all()]


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(
    body: ScheduleCreate, request: Request, s: Session = Depends(get_session)
) -> ScheduleOut:
    if s.get(models.Project, body.project_id) is None:
        raise HTTPException(400, "unknown project")
    _validate_cron(body.cron)
    sched = models.Schedule(
        project_id=body.project_id,
        name=body.name,
        cron=body.cron,
        prompt=body.prompt,
        enabled=body.enabled,
    )
    s.add(sched)
    s.commit()
    s.refresh(sched)
    svc = _service(request)
    if svc is not None:
        svc.upsert(sched.id, sched.cron, sched.project_id, sched.prompt, sched.enabled)
    return _to_out(sched)


@router.patch("/{sched_id}", response_model=ScheduleOut)
def update_schedule(
    sched_id: str,
    body: ScheduleUpdate,
    request: Request,
    s: Session = Depends(get_session),
) -> ScheduleOut:
    sched = s.get(models.Schedule, sched_id)
    if sched is None:
        raise HTTPException(404, "not found")
    if body.cron is not None:
        _validate_cron(body.cron)
    for field in ("name", "cron", "prompt", "enabled"):
        v = getattr(body, field)
        if v is not None:
            setattr(sched, field, v)
    s.commit()
    s.refresh(sched)
    svc = _service(request)
    if svc is not None:
        svc.upsert(sched.id, sched.cron, sched.project_id, sched.prompt, sched.enabled)
    return _to_out(sched)


@router.delete("/{sched_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    sched_id: str, request: Request, s: Session = Depends(get_session)
) -> None:
    sched = s.get(models.Schedule, sched_id)
    if sched is None:
        raise HTTPException(404, "not found")
    s.delete(sched)
    s.commit()
    svc = _service(request)
    if svc is not None:
        svc.remove(sched_id)
