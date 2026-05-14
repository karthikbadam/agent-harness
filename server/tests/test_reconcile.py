from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.db import session_scope
from agent_harness.reconcile import _alive, reconcile_jobs


def _project_and_job(status: str = "running", pid: int | None = None) -> tuple[str, str]:
    with session_scope() as s:
        p = models.Project(name="x", path="/tmp")
        s.add(p)
        s.flush()
        j = models.Job(project_id=p.id, status=status)
        s.add(j)
        s.flush()
        t = models.Turn(job_id=j.id, idx=0, prompt="hi", status="running", pid=pid)
        s.add(t)
        s.flush()
        return p.id, j.id


def test_alive_check_for_dead_pid() -> None:
    assert _alive(1234567) is False


async def test_reconcile_marks_dead_job_stopped(initdb: Path) -> None:
    reg = BroadcasterRegistry(initdb / "logs")
    _, jid = _project_and_job(status="running", pid=1234567)

    reconciled = await reconcile_jobs(reg)
    assert jid in reconciled

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "stopped"
        assert job.ended_at is not None
        turn = job.turns[0]
        assert turn.status == "stopped"
        assert turn.exit_code == -1

    log = initdb / "logs" / "jobs" / jid / "turn-0.jsonl"
    assert log.exists()
    assert '"type":"job_status"' in log.read_text()
    assert '"status":"stopped"' in log.read_text()


async def test_reconcile_kills_orphan_then_marks_stopped(initdb: Path) -> None:
    reg = BroadcasterRegistry(initdb / "logs")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        _, jid = _project_and_job(status="running", pid=proc.pid)
        assert _alive(proc.pid) is True

        await reconcile_jobs(reg)

        # `_alive` returns true for zombies; poll() returns the exit status.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "stopped"


async def test_reconcile_idempotent(initdb: Path) -> None:
    reg = BroadcasterRegistry(initdb / "logs")
    _, jid = _project_and_job(status="running", pid=1234567)

    once = await reconcile_jobs(reg)
    twice = await reconcile_jobs(reg)
    assert jid in once
    assert twice == []  # nothing left in running state
