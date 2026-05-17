"""Driver routes: per-project mode toggle, suggestions, notes, SSE events.

This is the only API surface the external ``agent-harness-driver`` process
talks to. The same surface backs the copilot UI when ``autopilot_mode='off'``:
GET ``/suggestions`` renders the same actions for one-click human dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schemas import (
    DriverGlobalStatus,
    DriverModeUpdate,
    DriverNoteCreate,
    DriverNoteOut,
    DriverStateOut,
    SuggestedAction,
)
from ..services import driver_bus, driver_policy

log = logging.getLogger(__name__)

router = APIRouter(tags=["driver"], dependencies=[Depends(require_auth)])


# ---------------------------- helpers ------------------------------------- #


def _to_note_out(n: models.DriverNote) -> DriverNoteOut:
    return DriverNoteOut(
        id=n.id,
        project_id=n.project_id,
        task_id=n.task_id,
        job_id=n.job_id,
        severity=n.severity,  # type: ignore[arg-type]
        kind=n.kind,
        message=n.message,
        action_url=n.action_url,
        created_at=n.created_at,
        acknowledged_at=n.acknowledged_at,
    )


def _to_action(a: driver_policy.Action) -> SuggestedAction:
    return SuggestedAction(
        kind=a.kind,
        project_id=a.project_id,
        task_id=a.task_id,
        job_id=a.job_id,
        reason=a.reason,
        rest_verb=a.rest_verb,
        rest_path=a.rest_path,
        payload=a.payload,
    )


async def _wait_for_subscriber(
    bus: driver_bus.DriverEventBus, timeout: float = 5.0
) -> bool:
    """Poll the bus for a subscriber up to `timeout` seconds. Returns True if
    a subscriber connected before the deadline.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if bus.has_subscriber():
            return True
        await asyncio.sleep(0.1)
    return False


# ------------------------ project-scoped driver state -------------------- #


@router.get("/api/projects/{project_id}/driver", response_model=DriverStateOut)
def get_driver_state(
    project_id: str, s: Session = Depends(get_session)
) -> DriverStateOut:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "unknown project")
    open_count = (
        s.query(models.DriverNote)
        .filter(
            models.DriverNote.project_id == project_id,
            models.DriverNote.acknowledged_at.is_(None),
            models.DriverNote.severity.in_(("warn", "escalate")),
        )
        .count()
    )
    return DriverStateOut(
        mode=p.autopilot_mode,  # type: ignore[arg-type]
        has_connected_driver=driver_bus.get_bus().has_subscriber(),
        open_notes=open_count,
    )


@router.patch(
    "/api/projects/{project_id}/driver", response_model=DriverStateOut
)
async def set_driver_mode(
    project_id: str,
    body: DriverModeUpdate,
    request: Request,
    s: Session = Depends(get_session),
) -> DriverStateOut:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "unknown project")
    bus = driver_bus.get_bus()
    if body.mode == "on" and not bus.has_subscriber():
        # Auto-spawn fallback: try to start agent-harness-driver and wait.
        spawned = _spawn_driver_subprocess(request)
        if spawned is None or not await _wait_for_subscriber(bus, timeout=5.0):
            if spawned is not None:
                try:
                    spawned.terminate()
                except Exception:  # noqa: BLE001
                    pass
            raise HTTPException(
                409,
                "could not start agent-harness-driver; see logs/driver.log",
            )
        request.app.state.owned_drivers[project_id] = spawned

    prev_mode = p.autopilot_mode
    if body.mode == "off" and prev_mode == "on":
        # Last wake-up before suppression.
        bus.emit("mode_off", project_id, force=True)

    p.autopilot_mode = body.mode
    s.commit()

    if body.mode == "on":
        bus.emit("reconcile_now", project_id, force=True)
    elif body.mode == "off":
        # If this was the last on project, terminate any harness-owned driver.
        any_on = (
            s.execute(
                select(models.Project.id).where(
                    models.Project.autopilot_mode == "on"
                )
            ).first()
        )
        if not any_on:
            owned = getattr(request.app.state, "owned_drivers", {})
            for pid, proc in list(owned.items()):
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                owned.pop(pid, None)

    s.refresh(p)
    return get_driver_state(project_id, s)


def _spawn_driver_subprocess(request: Request):
    """Spawn agent-harness-driver as a subprocess, log to AH_HOME/logs/driver.log."""
    import shutil
    import subprocess

    from ..config import get_settings

    binary = shutil.which("agent-harness-driver")
    if binary is None:
        log.warning("agent-harness-driver binary not found on PATH")
        return None
    settings = get_settings()
    assert settings.logs_dir is not None
    log_path = settings.logs_dir / "driver.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            [binary],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("spawn agent-harness-driver failed: %s", e)
        return None
    if not hasattr(request.app.state, "owned_drivers"):
        request.app.state.owned_drivers = {}
    return proc


# --------------------------- suggestions (copilot) ----------------------- #


@router.get(
    "/api/projects/{project_id}/driver/suggestions",
    response_model=list[SuggestedAction],
)
def list_suggestions(
    project_id: str, s: Session = Depends(get_session)
) -> list[SuggestedAction]:
    p = s.get(models.Project, project_id)
    if p is None:
        raise HTTPException(404, "unknown project")
    actions = driver_policy.next_actions(s, project_id)
    return [_to_action(a) for a in actions]


# -------------------------------- notes --------------------------------- #


@router.get(
    "/api/projects/{project_id}/driver/notes",
    response_model=list[DriverNoteOut],
)
def list_project_notes(
    project_id: str,
    severity: str | None = Query(default=None),
    acknowledged: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    s: Session = Depends(get_session),
) -> list[DriverNoteOut]:
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    q = s.query(models.DriverNote).filter(models.DriverNote.project_id == project_id)
    if severity:
        q = q.filter(models.DriverNote.severity == severity)
    if acknowledged is True:
        q = q.filter(models.DriverNote.acknowledged_at.isnot(None))
    elif acknowledged is False:
        q = q.filter(models.DriverNote.acknowledged_at.is_(None))
    rows = q.order_by(models.DriverNote.created_at.desc()).limit(limit).all()
    return [_to_note_out(n) for n in rows]


@router.post(
    "/api/driver/notes", response_model=DriverNoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_note(
    body: DriverNoteCreate, s: Session = Depends(get_session)
) -> DriverNoteOut:
    if s.get(models.Project, body.project_id) is None:
        raise HTTPException(404, "unknown project")
    # 7-day prune of old notes — cheap lazy garbage collection.
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    s.query(models.DriverNote).filter(
        models.DriverNote.created_at < cutoff,
        models.DriverNote.acknowledged_at.isnot(None),
    ).delete()
    n = models.DriverNote(
        project_id=body.project_id,
        task_id=body.task_id,
        job_id=body.job_id,
        severity=body.severity,
        kind=body.kind,
        message=body.message,
        action_url=body.action_url,
    )
    s.add(n)
    s.commit()
    s.refresh(n)
    return _to_note_out(n)


@router.post(
    "/api/driver/notes/{note_id}/acknowledge", response_model=DriverNoteOut
)
def acknowledge_note(
    note_id: str, s: Session = Depends(get_session)
) -> DriverNoteOut:
    n = s.get(models.DriverNote, note_id)
    if n is None:
        raise HTTPException(404, "not found")
    if n.acknowledged_at is None:
        n.acknowledged_at = datetime.now(timezone.utc)
        s.commit()
        s.refresh(n)
    return _to_note_out(n)


# ----------------------------- global status ---------------------------- #


@router.get("/api/driver/status", response_model=DriverGlobalStatus)
def driver_status(s: Session = Depends(get_session)) -> DriverGlobalStatus:
    rows = s.execute(
        select(models.Project.id).where(models.Project.autopilot_mode == "on")
    ).all()
    return DriverGlobalStatus(
        connected=driver_bus.get_bus().has_subscriber(),
        last_seen=driver_bus.get_bus().last_seen_at(),
        mode_on_projects=[r[0] for r in rows],
    )


# ------------------------------- SSE events ----------------------------- #


@router.get("/api/driver/events")
async def driver_events(
    request: Request, s: Session = Depends(get_session)
) -> Response:
    """Long-lived SSE: events emitted by the bus, gated by autopilot_mode.

    At most one active subscriber — second connect gets 409. On subscribe,
    we send one ``reconcile_now`` event per project currently in mode=on
    so the driver can sweep state before listening for changes.
    """
    bus = driver_bus.get_bus()
    try:
        q = bus.subscribe()
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    # Snapshot mode=on projects before starting the stream; we emit a
    # reconcile_now for each so the driver immediately processes state.
    mode_on = [
        r[0]
        for r in s.execute(
            select(models.Project.id).where(models.Project.autopilot_mode == "on")
        ).all()
    ]
    for pid in mode_on:
        bus.emit("reconcile_now", pid, force=True)

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # heartbeat — keep the connection alive through proxies
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps(evt.to_json())
                yield f"event: {evt.event}\ndata: {payload}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
