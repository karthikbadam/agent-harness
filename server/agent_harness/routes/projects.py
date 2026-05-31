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
def _resolve_code_roots() -> list[str]:
    """Return distinct, existing code-root directories to scan.

    Default is just ``~/Code`` — the standard macOS convention. Override
    with the ``AH_CODE_ROOTS`` env var (colon-separated absolute paths).
    Directories that resolve to the same inode are deduped so case-variant
    paths on APFS don't produce duplicate entries.
    """
    raw = os.environ.get("AH_CODE_ROOTS")
    candidates = [r for r in raw.split(":") if r.strip()] if raw else ["~/Code"]
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
        for p in s.query(models.Project.path).filter(models.Project.path.isnot(None)).all()
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
    # Sort case-insensitive alphabetically by name.
    out.sort(key=lambda s: s.name.casefold())
    return out


def _ensure_project_dir(path: str) -> None:
    """Create the directory and make it a git repo (with an initial commit so
    HEAD exists) — used when the UI starts a project in a brand-new folder.
    Best-effort and idempotent: a path that already exists / is already a repo
    is left as-is."""
    import subprocess
    from pathlib import Path

    d = Path(path)
    d.mkdir(parents=True, exist_ok=True)
    if (d / ".git").exists():
        return
    try:
        subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True, timeout=10)
        gi = d / ".gitignore"
        if not gi.exists():
            gi.write_text("node_modules/\ndist/\nrun.log\n.DS_Store\n")
        # An initial commit so `main` is born (loops branch from HEAD). Identity
        # flags keep it working even if global git identity is unset.
        env_id = ["-c", "user.email=agent-harness@local", "-c", "user.name=agent-harness"]
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, timeout=10)
        subprocess.run(
            [
                "git",
                "-C",
                str(d),
                *env_id,
                "commit",
                "-q",
                "--no-gpg-sign",
                "-m",
                "init: project scaffold",
            ],
            check=True,
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        # Leave whatever git state we managed; the first task/loop iteration
        # can still initialize/commit. Don't block project creation on this.
        pass


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, s: Session = Depends(get_session)) -> ProjectOut:
    path = _expand_path(body.path)
    if body.create_dir:
        _ensure_project_dir(path)
    p = models.Project(
        name=body.name,
        path=path,
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
    """Delete a project and all its dependent rows.

    The schema wasn't built with ``ondelete=CASCADE`` on every reference, so
    naive ``s.delete(project)`` fails with FOREIGN KEY constraints. Tear down
    in dependency order so each DELETE sees only orphans:

      outcomes ──┐
                 ├──> tasks  ──> (TaskDependency cascades on its own)
      turns ─────┤
                 ├──> jobs
      driver_notes ──> (project + tasks + jobs)
      schedules  ──> (project)
      allowlist  ──> (handled by Project.rules SQLA cascade)
      project
    """
    from sqlalchemy import or_, select as sa_select

    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "not found")
    if p.is_default:
        raise HTTPException(409, "cannot delete the default project")

    job_ids = [
        r[0]
        for r in s.execute(
            sa_select(models.Job.id).where(models.Job.project_id == project_id)
        ).all()
    ]
    task_ids = [
        r[0]
        for r in s.execute(
            sa_select(models.Task.id).where(models.Task.project_id == project_id)
        ).all()
    ]

    if job_ids or task_ids:
        s.execute(
            models.Outcome.__table__.delete().where(
                or_(
                    models.Outcome.job_id.in_(job_ids or [""]),
                    models.Outcome.task_id.in_(task_ids or [""]),
                )
            )
        )
        # Artifacts FK to tasks/jobs and were added after this teardown was
        # written — drain them too or the tasks DELETE hits a FK constraint.
        s.execute(
            models.Artifact.__table__.delete().where(
                or_(
                    models.Artifact.job_id.in_(job_ids or [""]),
                    models.Artifact.task_id.in_(task_ids or [""]),
                )
            )
        )
    if task_ids:
        # Tasks self-reference via parent_task_id (loop iterations → loop
        # parent); clear those links before the bulk delete so the row-by-row
        # FK check can't trip on a parent removed before its child.
        s.execute(
            models.Task.__table__.update()
            .where(models.Task.id.in_(task_ids))
            .values(parent_task_id=None)
        )
    s.execute(
        models.DriverNote.__table__.delete().where(
            or_(
                models.DriverNote.project_id == project_id,
                models.DriverNote.job_id.in_(job_ids or [""]),
                models.DriverNote.task_id.in_(task_ids or [""]),
            )
        )
    )
    if job_ids:
        # Turn rows are cascade-deleted via Job.turns SQLA relationship when
        # we delete each Job, but the bulk SQL DELETE below bypasses that.
        # Wipe turns directly first.
        s.execute(models.Turn.__table__.delete().where(models.Turn.job_id.in_(job_ids)))
        s.execute(models.Job.__table__.delete().where(models.Job.id.in_(job_ids)))
    if task_ids:
        s.execute(models.Task.__table__.delete().where(models.Task.id.in_(task_ids)))
    s.execute(models.Schedule.__table__.delete().where(models.Schedule.project_id == project_id))
    s.execute(
        models.AllowlistRule.__table__.delete().where(models.AllowlistRule.project_id == project_id)
    )
    # Project itself last. The SQLA relationships from Project to jobs/rules
    # would normally cascade, but we've already drained those.
    s.execute(models.Project.__table__.delete().where(models.Project.id == project_id))
    s.commit()


@router.get("/{project_id}/worktrees", response_model=list[WorktreeOut])
def list_project_worktrees(project_id: str, s: Session = Depends(get_session)) -> list[WorktreeOut]:
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
