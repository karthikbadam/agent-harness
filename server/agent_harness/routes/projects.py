from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schemas import IntegrateIn, ProjectCreate, ProjectOut, ProjectUpdate, TaskOut
from ..services import claude_md, integration
from ..routes.tasks import _to_out as _task_to_out

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
        instructions=p.instructions,
        skills=list(p.skills or []),
        context_paths=list(p.context_paths or []),
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
        instructions=body.instructions,
        skills=list(body.skills),
        context_paths=list(body.context_paths),
    )
    s.add(p)
    s.flush()
    if body.is_default:
        _clear_other_defaults(s, p.id)
    s.commit()
    s.refresh(p)
    _safe_sync_claude_md(p)
    return _to_out(p)


def _safe_sync_claude_md(p: models.Project) -> None:
    try:
        claude_md.sync_project(p)
    except Exception:  # noqa: BLE001
        # CLAUDE.md sync is best-effort; never block a project write on it.
        pass


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
        "instructions",
        "skills",
        "context_paths",
    ):
        v = getattr(body, field)
        if v is not None:
            setattr(p, field, v)
    if body.is_default is True:
        _clear_other_defaults(s, p.id)
    s.commit()
    s.refresh(p)
    _safe_sync_claude_md(p)
    return _to_out(p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, s: Session = Depends(get_session)) -> None:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "not found")
    s.delete(p)
    s.commit()


@router.post("/{project_id}/integrate", response_model=TaskOut)
def create_integration(
    project_id: str, body: IntegrateIn, s: Session = Depends(get_session)
) -> TaskOut:
    """Create a synthetic 'merge these branches' task and return it.

    The caller is responsible for running the returned task via the usual
    ``POST /api/tasks/{id}/run`` flow.
    """
    try:
        tid = integration.create_integration_task(
            project_id=project_id,
            task_ids=body.task_ids,
            target_branch=body.target_branch,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    t = s.get(models.Task, tid)
    assert t is not None
    return _task_to_out(s, t)
