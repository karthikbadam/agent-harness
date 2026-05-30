"""Artifact routes.

An artifact is a file an agent produced and registered against a task — a
progress graph, a results table, a research report, a checkpoint pointer, a
log. Registration copies the source file into
``AH_HOME/artifacts/<task_id>/<name>`` so it survives worktree cleanup, then
the task page renders it (PNG inline, everything else as a download).

This is the surface the autoresearch loop uses each iteration:

    curl -X POST .../api/tasks/$TID/artifacts \\
      -H "Authorization: Bearer $TOKEN" \\
      -d '{"kind":"graph","path":"progress.png","meta":{"iteration":7}}'

The source ``path`` is resolved relative to the task's current job cwd
(worktree or project path) when relative, or taken as-is when absolute. Both
are confined to the project/worktree tree to prevent a prompt-injected agent
from exfiltrating arbitrary files (e.g. ``/etc/passwd``) into the artifact
store.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..config import get_settings
from ..db import get_session
from ..schemas import ArtifactCreate, ArtifactOut

router = APIRouter(tags=["artifacts"], dependencies=[Depends(require_auth)])


def _artifacts_dir(task_id: str) -> Path:
    d = get_settings().home / "artifacts" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    """Reduce a requested display name to a single safe path component."""
    base = Path(name).name  # strip any directory parts
    base = base.replace("\x00", "")
    return base or "artifact"


def _resolve_source(task: models.Task, s: Session, raw_path: str) -> Path:
    """Resolve the agent-supplied path to a real file, confined to the task's
    job tree (worktree if present, else project path). Rejects traversal
    outside that tree."""
    candidates: list[Path] = []
    # Roots the agent could legitimately have written under.
    roots: list[Path] = []
    if task.worktree_path:
        roots.append(Path(task.worktree_path))
    project = s.get(models.Project, task.project_id)
    if project is not None:
        roots.append(Path(project.path))
    # Most recent job's cwd is the authoritative root for relative paths.
    job_cwd_row = s.execute(
        select(models.Job.cwd)
        .where(models.Job.task_id == task.id, models.Job.cwd != "")
        .order_by(models.Job.created_at.desc())
    ).first()
    if job_cwd_row and job_cwd_row[0]:
        roots.insert(0, Path(job_cwd_row[0]))

    p = Path(raw_path)
    if p.is_absolute():
        candidates.append(p)
    else:
        for root in roots:
            candidates.append(root / p)

    for cand in candidates:
        try:
            resolved = cand.resolve()
        except (OSError, RuntimeError):
            continue
        if not resolved.is_file():
            continue
        # Confinement: the resolved file must live under one of the allowed
        # roots. Absolute paths are allowed only if they fall inside a root.
        for root in roots:
            try:
                resolved.relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            return resolved
    raise HTTPException(
        400,
        f"artifact source not found or outside task tree: {raw_path!r}",
    )


def _to_out(a: models.Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id,
        task_id=a.task_id,
        job_id=a.job_id,
        kind=a.kind,
        name=a.name,
        meta=a.meta or {},
        download_url=f"/api/artifacts/{a.id}/download",
        created_at=a.created_at,
    )


@router.post(
    "/api/tasks/{task_id}/artifacts",
    response_model=ArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
def create_artifact(
    task_id: str, body: ArtifactCreate, s: Session = Depends(get_session)
) -> ArtifactOut:
    task = s.get(models.Task, task_id)
    if task is None:
        raise HTTPException(404, "unknown task")
    src = _resolve_source(task, s, body.path)
    name = _safe_name(body.name or src.name)
    dest = _artifacts_dir(task_id) / name
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        raise HTTPException(500, f"failed to store artifact: {e}")
    # Re-registering the same name updates the existing row in place so a loop
    # that overwrites progress.png each iteration shows one current artifact,
    # not N duplicates.
    existing = s.execute(
        select(models.Artifact).where(
            models.Artifact.task_id == task_id, models.Artifact.name == name
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.kind = body.kind
        existing.path = str(dest)
        existing.meta = body.meta or {}
        existing.job_id = body.job_id
        a = existing
    else:
        a = models.Artifact(
            task_id=task_id,
            job_id=body.job_id,
            kind=body.kind,
            name=name,
            path=str(dest),
            meta=body.meta or {},
        )
        s.add(a)
    s.commit()
    s.refresh(a)
    return _to_out(a)


@router.get("/api/tasks/{task_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(task_id: str, s: Session = Depends(get_session)) -> list[ArtifactOut]:
    if s.get(models.Task, task_id) is None:
        raise HTTPException(404, "unknown task")
    rows = (
        s.query(models.Artifact)
        .filter(models.Artifact.task_id == task_id)
        .order_by(models.Artifact.created_at.desc())
        .all()
    )
    return [_to_out(a) for a in rows]


def _sniff_media_type(p: Path) -> str | None:
    """Best-effort content-type from the file's first bytes. Agents often
    register artifacts with a display name and no extension (e.g. "Progress
    graph"), so the default octet-stream breaks inline <img> rendering — most
    importantly for SVG, which browsers refuse to render in <img> without an
    ``image/svg+xml`` type. Sniffing the bytes fixes that."""
    try:
        with p.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    stripped = head.lstrip()
    if stripped[:4] == b"<svg" or (stripped[:5] == b"<?xml" and b"<svg" in head):
        return "image/svg+xml"
    return None


@router.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, s: Session = Depends(get_session)) -> FileResponse:
    a = s.get(models.Artifact, artifact_id)
    if a is None:
        raise HTTPException(404, "unknown artifact")
    p = Path(a.path)
    if not p.is_file():
        raise HTTPException(410, "artifact file no longer present")
    media = _sniff_media_type(p)
    return FileResponse(str(p), filename=a.name, media_type=media)


@router.delete(
    "/api/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_artifact(artifact_id: str, s: Session = Depends(get_session)) -> None:
    a = s.get(models.Artifact, artifact_id)
    if a is None:
        raise HTTPException(404, "unknown artifact")
    try:
        Path(a.path).unlink(missing_ok=True)
    except OSError:
        pass
    s.delete(a)
    s.commit()
