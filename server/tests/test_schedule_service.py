from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.db import session_scope
from agent_harness.jobs import JobManager
from agent_harness.schedule_service import ScheduleService, parse_cron

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


def test_parse_cron_valid() -> None:
    parse_cron("0 9 * * *")
    parse_cron("*/5 * * * *")


def test_parse_cron_invalid_raises() -> None:
    with pytest.raises(Exception):
        parse_cron("garbage")


async def test_upsert_and_remove(initdb: Path) -> None:
    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM))
    svc = ScheduleService(mgr)
    svc.scheduler.start()
    try:
        svc.upsert("s1", "0 9 * * *", "p1", "do stuff", enabled=True)
        assert svc.scheduler.get_job("s1") is not None
        svc.upsert("s1", "0 10 * * *", "p1", "do stuff", enabled=False)
        assert svc.scheduler.get_job("s1") is None  # disabled removes the job
        svc.upsert("s1", "0 10 * * *", "p1", "do stuff", enabled=True)
        assert svc.scheduler.get_job("s1") is not None
        svc.remove("s1")
        assert svc.scheduler.get_job("s1") is None
    finally:
        svc.shutdown()


async def test_start_loads_enabled_from_db(initdb: Path) -> None:
    with session_scope() as s:
        p = models.Project(name="p", path="/tmp")
        s.add(p)
        s.flush()
        s.add(
            models.Schedule(project_id=p.id, name="on", cron="0 9 * * *", prompt="x", enabled=True)
        )
        s.add(
            models.Schedule(
                project_id=p.id, name="off", cron="0 10 * * *", prompt="y", enabled=False
            )
        )

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM))
    svc = ScheduleService(mgr)
    try:
        svc.start()
        with session_scope() as s:
            schedules = s.query(models.Schedule).all()
            on = next(x for x in schedules if x.name == "on")
            off = next(x for x in schedules if x.name == "off")
        assert svc.scheduler.get_job(on.id) is not None
        assert svc.scheduler.get_job(off.id) is None
    finally:
        svc.shutdown()


async def test_fire_creates_and_starts_job(initdb: Path) -> None:
    with session_scope() as s:
        p = models.Project(name="p", path="/tmp")
        s.add(p)
        s.flush()
        sched = models.Schedule(
            project_id=p.id, name="t", cron="* * * * *", prompt="hi", enabled=True
        )
        s.add(sched)
        s.flush()
        pid = p.id
        sid = sched.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM))
    svc = ScheduleService(mgr)

    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "text_only.jsonl")
    try:
        await svc._fire(sid, pid, "hi")
        # find the job that was created
        with session_scope() as s:
            jobs = s.query(models.Job).all()
            assert len(jobs) == 1
            assert jobs[0].schedule_id == sid
            jid = jobs[0].id
        await mgr.wait(jid)
        with session_scope() as s:
            j = s.get(models.Job, jid)
            assert j is not None and j.status == "done"
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)
