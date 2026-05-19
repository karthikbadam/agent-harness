"""Driver event bus: in-process fan-out of mode-on events.

Emit points (``task_runner.on_job_finalized``, phase transitions in
``jobs._finalize_turn``, new task creation, integration outcomes) call
:meth:`DriverEventBus.emit`. Each call consults the project's
``autopilot_mode``; if ``off``, the emit is **suppressed at the source** so
no subscriber sees it. This is the "signals only when activated" contract.

The SSE endpoint in ``routes/driver.py`` is the canonical subscriber — at
most one active driver process at a time (409 on second connect). Subscribers
get an ``asyncio.Queue``; ``put_nowait`` is sync-safe so emit can be called
from anywhere (sync or async).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .. import models
from ..db import session_scope

log = logging.getLogger(__name__)


@dataclass
class DriverEvent:
    event: str
    project_id: str
    task_id: str | None = None
    job_id: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class DriverEventBus:
    """Single-subscriber-at-a-time bus for driver events."""

    _QUEUE_BOUND = 1024

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[DriverEvent]] = []
        self._last_seen_at: datetime | None = None

    # ------------------------------ subscription ------------------------- #

    def has_subscriber(self) -> bool:
        return bool(self._subscribers)

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def last_seen_at(self) -> datetime | None:
        return self._last_seen_at

    def subscribe(self) -> asyncio.Queue[DriverEvent]:
        if self._subscribers:
            raise RuntimeError("a driver is already connected")
        q: asyncio.Queue[DriverEvent] = asyncio.Queue(maxsize=self._QUEUE_BOUND)
        self._subscribers.append(q)
        self._last_seen_at = datetime.now(timezone.utc)
        return q

    def unsubscribe(self, q: asyncio.Queue[DriverEvent]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    # ---------------------------------- emit ----------------------------- #

    def emit(
        self,
        event: str,
        project_id: str,
        *,
        task_id: str | None = None,
        job_id: str | None = None,
        force: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event to all current subscribers.

        ``force=True`` skips the autopilot_mode gate — used for ``mode_off``
        (we deliberately want one last wake-up before suppression) and
        ``reconcile_now`` (sent at subscribe time even though the project's
        mode just flipped to ``on``).
        """
        if not self._subscribers:
            return
        if not force and not self._mode_on(project_id):
            return
        evt = DriverEvent(
            event=event,
            project_id=project_id,
            task_id=task_id,
            job_id=job_id,
            payload=payload or {},
        )
        for q in list(self._subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                log.warning(
                    "driver subscriber queue full; dropping event %s for %s",
                    event,
                    project_id,
                )

    def _mode_on(self, project_id: str) -> bool:
        with session_scope() as s:
            row = s.execute(
                select(models.Project.autopilot_mode).where(
                    models.Project.id == project_id
                )
            ).first()
        return bool(row and row[0] == "on")


_BUS: DriverEventBus | None = None


def get_bus() -> DriverEventBus:
    """Return the process-wide singleton bus, instantiating on first call."""
    global _BUS
    if _BUS is None:
        _BUS = DriverEventBus()
    return _BUS


def reset_bus() -> None:
    """For tests: drop the cached singleton so the next get_bus is fresh."""
    global _BUS
    _BUS = None
