"""APScheduler integration.

The DB `schedules` table is the source of truth. On startup we read it and
populate an in-memory APScheduler. CRUD endpoints mutate both. On fire, we
call `JobManager.create_job(..., schedule_id=...)` and `start()`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from . import models
from .db import session_scope
from .notify import PushPayload, get_notifier

if TYPE_CHECKING:
    from .jobs import JobManager

log = logging.getLogger(__name__)


def parse_cron(expr: str) -> CronTrigger:
    return CronTrigger.from_crontab(expr.strip())


class ScheduleService:
    def __init__(self, job_manager: "JobManager") -> None:
        self.job_manager = job_manager
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        self.scheduler.start()
        with session_scope() as s:
            rows = (
                s.execute(select(models.Schedule).where(models.Schedule.enabled == True))  # noqa: E712
                .scalars()
                .all()
            )
            for sched in rows:
                try:
                    self._register(sched.id, sched.cron, sched.project_id, sched.prompt)
                except Exception as e:  # noqa: BLE001
                    log.warning("skipping schedule %s on startup: %s", sched.id, e)

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ---------------------------- mutations ------------------------------- #

    def upsert(
        self, schedule_id: str, cron: str, project_id: str, prompt: str, enabled: bool = True
    ) -> None:
        self.remove(schedule_id)
        if enabled:
            self._register(schedule_id, cron, project_id, prompt)

    def remove(self, schedule_id: str) -> None:
        try:
            self.scheduler.remove_job(schedule_id)
        except Exception:  # noqa: BLE001
            pass  # not registered or already gone

    # ----------------------------- internals ------------------------------ #

    def _register(self, schedule_id: str, cron: str, project_id: str, prompt: str) -> None:
        trigger = parse_cron(cron)
        self.scheduler.add_job(
            self._fire,
            trigger=trigger,
            id=schedule_id,
            args=[schedule_id, project_id, prompt],
            misfire_grace_time=3600,
            replace_existing=True,
        )

    async def _fire(self, schedule_id: str, project_id: str, prompt: str) -> None:
        try:
            title = f"[scheduled] {prompt[:60]}"
            jid = self.job_manager.create_job(
                project_id, prompt, title=title, schedule_id=schedule_id
            )
            await self.job_manager.start(jid)
            try:
                get_notifier().send_to_all(
                    PushPayload(
                        title="Scheduled job started",
                        body=title,
                        job_id=jid,
                        url=f"/jobs/{jid}",
                    )
                )
            except Exception as e:  # noqa: BLE001
                log.warning("schedule fire push failed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.exception("schedule %s fire failed: %s", schedule_id, e)
