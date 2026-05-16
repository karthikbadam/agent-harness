from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schemas import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_auth)])


def _to_out(p: models.Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        path=p.path,
        permission_mode=p.permission_mode,
        dangerously_skip=p.dangerously_skip,
        extra_claude_args=list(p.extra_claude_args or []),
        idle_timeout_seconds=p.idle_timeout_seconds,
        is_default=bool(p.is_default),
        created_at=p.created_at,
    )


def _clear_other_defaults(s: Session, keep_id: str) -> None:
    s.execute(
        update(models.Project)
        .where(models.Project.id != keep_id, models.Project.is_default.is_(True))
        .values(is_default=False)
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(s: Session = Depends(get_session)) -> list[ProjectOut]:
    return [_to_out(p) for p in s.query(models.Project).order_by(models.Project.created_at).all()]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, s: Session = Depends(get_session)) -> ProjectOut:
    p = models.Project(
        name=body.name,
        path=body.path,
        permission_mode=body.permission_mode,
        dangerously_skip=body.dangerously_skip,
        extra_claude_args=list(body.extra_claude_args),
        idle_timeout_seconds=body.idle_timeout_seconds,
        is_default=body.is_default,
    )
    s.add(p)
    s.flush()
    if body.is_default:
        _clear_other_defaults(s, p.id)
    s.commit()
    s.refresh(p)
    return _to_out(p)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, s: Session = Depends(get_session)) -> ProjectOut:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "not found")
    return _to_out(p)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: str, body: ProjectUpdate, s: Session = Depends(get_session)
) -> ProjectOut:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "not found")
    for field in (
        "name",
        "path",
        "permission_mode",
        "dangerously_skip",
        "extra_claude_args",
        "idle_timeout_seconds",
        "is_default",
    ):
        v = getattr(body, field)
        if v is not None:
            setattr(p, field, v)
    if body.is_default is True:
        _clear_other_defaults(s, p.id)
    s.commit()
    s.refresh(p)
    return _to_out(p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, s: Session = Depends(get_session)) -> None:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "not found")
    s.delete(p)
    s.commit()
