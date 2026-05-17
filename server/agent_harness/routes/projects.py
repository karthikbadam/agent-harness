from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schemas import (
    IntegrateIn,
    PathSuggestion,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    TaskOut,
    WorktreeOut,
)
from ..services import claude_md, integration, worktrees
from ..routes.tasks import _to_out as _task_to_out

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_auth)])


def _expand_path(p: str) -> str:
    """Resolve ``~`` and ``~user`` in paths supplied by the FE composer. The
    UI doesn't know the user's home dir, so we expand server-side."""
    return os.path.expanduser(p) if p else p


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


# Common roots we'll scan for candidate project directories. macOS users
# typically use ``~/Code`` (capital C); ``~/code`` and ``~/src``/``~/projects``
# are common alternatives. Override via the ``AH_CODE_ROOTS`` env var
# (colon-separated list of paths) for non-standard setups.
_DEFAULT_CODE_ROOTS = ["~/Code", "~/code", "~/src", "~/projects"]


def _resolve_code_roots() -> list[str]:
    """Return distinct, existing code-root directories.

    macOS's default APFS is case-insensitive, so ``~/Code`` and ``~/code``
    resolve to the same directory — but ``os.path.realpath`` preserves the
    casing of the input, so naive de-dup doesn't catch it. We key by
    ``(st_dev, st_ino)`` to dedupe at the filesystem-identity level.
    """
    raw = os.environ.get("AH_CODE_ROOTS")
    candidates = (
        [r for r in raw.split(":") if r.strip()] if raw else list(_DEFAULT_CODE_ROOTS)
    )
    seen_keys: set[tuple[int, int]] = set()
    out: list[str] = []
    for r in candidates:
        path = os.path.expanduser(r)
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(path)
    return out


@router.get("/path-suggestions", response_model=list[PathSuggestion])
def path_suggestions(s: Session = Depends(get_session)) -> list[PathSuggestion]:
    """List candidate project directories the user can pick from when
    creating a project. Scans immediate subdirectories of common code roots
    (``~/Code``, ``~/code``, ``~/src``, ``~/projects``; override with
    ``AH_CODE_ROOTS``). Hidden directories (``.foo``) are skipped.
    """
    existing_paths = {
        os.path.realpath(p.path)
        for p in s.query(models.Project.path)
        .filter(models.Project.path.isnot(None))
        .all()
    }
    out: list[PathSuggestion] = []
    seen: set[str] = set()
    for root in _resolve_code_roots():
        root_abs = os.path.expanduser(root)
        if not os.path.isdir(root_abs):
            continue
        for entry in os.listdir(root_abs):
            if entry.startswith("."):
                continue
            child = os.path.join(root_abs, entry)
            if not os.path.isdir(child):
                continue
            real = os.path.realpath(child)
            if real in seen:
                continue
            seen.add(real)
            is_git = os.path.isdir(os.path.join(child, ".git"))
            out.append(
                PathSuggestion(
                    path=child,
                    name=entry,
                    is_git=is_git,
                    already_registered=real in existing_paths,
                )
            )
    # Sort case-insensitive by name. Git repos come first within the same
    # bucket so the things the user is more likely to want are at the top.
    out.sort(key=lambda s: (0 if s.is_git else 1, s.name.casefold()))
    return out


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, s: Session = Depends(get_session)) -> ProjectOut:
    p = models.Project(
        name=body.name,
        path=_expand_path(body.path),
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


@router.get("/{project_id}/worktrees", response_model=list[WorktreeOut])
def list_project_worktrees(
    project_id: str, s: Session = Depends(get_session)
) -> list[WorktreeOut]:
    """List outstanding ``git worktree list`` entries for the project.

    Each entry includes the on-disk ``task_id`` if the worktree's path matches
    a task this harness knows about — useful for spotting orphans left by a
    killed server or a failed cleanup.
    """
    proj = s.get(models.Project, project_id)
    if proj is None:
        raise HTTPException(404, "not found")
    raw = worktrees.list_outstanding(proj)
    # Build a lookup from worktree_path → task_id for the matching project.
    task_rows = (
        s.query(models.Task.id, models.Task.worktree_path)
        .filter(
            models.Task.project_id == project_id,
            models.Task.worktree_path.isnot(None),
        )
        .all()
    )
    by_path = {row[1]: row[0] for row in task_rows}
    out: list[WorktreeOut] = []
    for entry in raw:
        path = entry.get("worktree", "")
        out.append(
            WorktreeOut(
                path=path,
                branch=entry.get("branch") or None,
                head=entry.get("HEAD") or None,
                detached="detached" in entry,
                task_id=by_path.get(path),
            )
        )
    return out


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
