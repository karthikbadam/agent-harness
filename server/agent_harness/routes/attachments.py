"""Attachment routes.

Users can upload files (images, PDFs, etc.) that are attached to conversation
turns. The files are stored under AH_HOME/attachments/ and referenced in the
turn prompt so the agent can read them. Image attachments are also eligible to
become the project's card cover image.

POST /api/attachments         – upload a file; returns AttachmentOut
GET  /api/attachments/{id}/file – serve the raw file (no auth required,
                                  localhost-only service)
DELETE /api/attachments/{id}  – remove file + DB row
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..config import get_settings
from ..db import get_session, session_scope
from ..schemas import AttachmentOut

log = logging.getLogger(__name__)

router = APIRouter(tags=["attachments"])

# Public (no auth) router just for file serving — the URLs are opaque IDs,
# not guessable paths, and this is a localhost-only service.
public_router = APIRouter(tags=["attachments"])


def _attachments_dir() -> Path:
    d = get_settings().home / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _to_out(att: models.Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=att.id,
        project_id=att.project_id,
        job_id=att.job_id,
        filename=att.filename,
        mime_type=att.mime_type,
        url=f"/api/attachments/{att.id}/file",
        created_at=att.created_at,
    )


@router.post(
    "/api/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
async def upload_attachment(
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    s: Session = Depends(get_session),
) -> AttachmentOut:
    """Upload a file to be attached to a conversation turn."""
    att = models.Attachment(
        project_id=project_id,
        filename=file.filename or "upload",
        mime_type=file.content_type or "application/octet-stream",
        path="",  # filled after we know the id
    )
    s.add(att)
    s.flush()  # get the id

    dest = _attachments_dir() / f"{att.id}_{Path(att.filename).name}"
    content = await file.read()
    dest.write_bytes(content)
    att.path = str(dest)
    s.commit()

    # Async: if this is an image for a known project, refresh the cover.
    if project_id and att.mime_type.startswith("image/"):
        asyncio.create_task(_refresh_cover(project_id))

    return _to_out(att)


@public_router.get("/api/attachments/{attachment_id}/file")
def serve_attachment(attachment_id: str, s: Session = Depends(get_session)) -> FileResponse:
    att = s.get(models.Attachment, attachment_id)
    if att is None or not Path(att.path).exists():
        raise HTTPException(404, "not found")
    return FileResponse(att.path, media_type=att.mime_type, filename=att.filename)


@router.delete(
    "/api/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def delete_attachment(attachment_id: str, s: Session = Depends(get_session)) -> None:
    att = s.get(models.Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "not found")
    project_id = att.project_id
    cover_cleared = False
    if project_id:
        proj = s.get(models.Project, project_id)
        if proj and proj.cover_image_id == attachment_id:
            proj.cover_image_id = None
            cover_cleared = True
    try:
        Path(att.path).unlink(missing_ok=True)
    except Exception:
        pass
    s.delete(att)
    s.commit()
    if cover_cleared and project_id:
        asyncio.create_task(_refresh_cover(project_id))


async def _refresh_cover(project_id: str) -> None:
    """Pick the most recently uploaded image as the project cover."""
    try:
        with session_scope() as s:
            images = (
                s.query(models.Attachment)
                .filter(
                    models.Attachment.project_id == project_id,
                    models.Attachment.mime_type.startswith("image/"),
                )
                .order_by(models.Attachment.created_at.desc())
                .all()
            )
            proj = s.get(models.Project, project_id)
            if proj is None:
                return
            proj.cover_image_id = images[0].id if images else None
    except Exception:
        log.exception("cover refresh failed for project %s", project_id)


def stamp_job_on_attachments(
    attachment_ids: list[str], job_id: str, project_id: str, s: Session
) -> None:
    """Stamp job_id + project_id on attachment rows when they're consumed."""
    for att_id in attachment_ids:
        att = s.get(models.Attachment, att_id)
        if att is None:
            continue
        att.job_id = job_id
        if att.project_id is None:
            att.project_id = project_id
