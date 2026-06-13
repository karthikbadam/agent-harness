"""SSE endpoint for live job events.

GET /api/jobs/{id}/stream
  Auth: ``ah_session`` cookie (EventSource can't set headers, so the browser
        relies on the same-origin cookie minted by POST /api/session). curl/CLI
        clients can use ``Authorization: Bearer`` instead.
  Reconnect: honors `Last-Event-ID` header (set automatically by browsers) and
             also a `?last_event_id=` query param for non-browser clients.
  Heartbeat: ":\n\n" comment every 15s.

The handler is generator-driven; FastAPI awaits the next chunk and forwards it
to the wire. Client disconnect → asyncio CancelledError on the generator → we
exit cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import models
from ..auth import require_auth
from ..broadcaster import BroadcasterRegistry
from ..db import session_scope
from ..schemas import StreamEvent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["stream"])

# Codegen-only: a real endpoint returning StreamEvent so the discriminated
# union lands in components.schemas (the SSE endpoint uses text/event-stream
# and would otherwise leave the event types out of the OpenAPI). The endpoint
# is callable but only useful as a runtime example.
schema_router = APIRouter(prefix="/api/_codegen", tags=["codegen"], include_in_schema=True)


@schema_router.get("/stream-event", response_model=StreamEvent)
def stream_event_schema() -> StreamEvent:  # pragma: no cover - codegen-only
    raise HTTPException(404, "codegen-only endpoint")


HEARTBEAT_SECONDS = 15


def _format_event(seq: int, event_type: str, data_json: str) -> bytes:
    return (f"id: {seq}\nevent: {event_type}\ndata: {data_json}\n\n").encode("utf-8")


@router.get("/{job_id}/stream", dependencies=[Depends(require_auth)])
async def stream_job(
    job_id: str,
    request: Request,
    last_event_id: int | None = Query(default=None),
) -> StreamingResponse:
    with session_scope() as s:
        if s.get(models.Job, job_id) is None:
            raise HTTPException(404, "job not found")

    header_lei = request.headers.get("last-event-id")
    cursor = 0
    if last_event_id is not None:
        cursor = int(last_event_id)
    elif header_lei:
        try:
            cursor = int(header_lei)
        except ValueError:
            cursor = 0

    registry: BroadcasterRegistry = request.app.state.broadcasters
    broadcaster = registry.get(job_id)

    async def gen() -> AsyncIterator[bytes]:
        # Yield an initial retry hint + heartbeat so the client opens the stream
        # before we wait for events.
        yield b"retry: 3000\n\n"
        sub = broadcaster.subscribe(last_event_id=cursor)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(sub.__anext__(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield b": hb\n\n"
                    continue
                except StopAsyncIteration:
                    break
                if await request.is_disconnected():
                    break
                yield _format_event(ev.seq, ev.type, ev.model_dump_json())
        except asyncio.CancelledError:
            raise
        finally:
            await sub.aclose() if hasattr(sub, "aclose") else None

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # if behind nginx; harmless otherwise
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
