from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
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
    # Minimal sanity: 5 fields, no shell metachars. APScheduler will reject
    # garbage at registration time; we just block obvious bad input here.
    parts = expr.strip().split()
    if len(parts) not in (5, 6):
        raise HTTPException(400, "cron must have 5 fields (minute hour dom mon dow)")


@router.get("", response_model=list[ScheduleOut])
def list_schedules(s: Session = Depends(get_session)) -> list[ScheduleOut]:
    return [_to_out(x) for x in s.query(models.Schedule).order_by(models.Schedule.created_at).all()]


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScheduleCreate, s: Session = Depends(get_session)) -> ScheduleOut:
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
    return _to_out(sched)


@router.patch("/{sched_id}", response_model=ScheduleOut)
def update_schedule(
    sched_id: str, body: ScheduleUpdate, s: Session = Depends(get_session)
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
    return _to_out(sched)


@router.delete("/{sched_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(sched_id: str, s: Session = Depends(get_session)) -> None:
    sched = s.get(models.Schedule, sched_id)
    if sched is None:
        raise HTTPException(404, "not found")
    s.delete(sched)
    s.commit()
