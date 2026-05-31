from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.db import session_scope
from agent_harness.jobs import JobManager

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


async def test_watchdog_stops_silent_turn(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="p", path="/tmp", idle_timeout_seconds=1)
        s.add(proj)
        s.flush()
        pid = proj.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM), default_idle_timeout_seconds=1)

    # Use a fixture with no events but make the shim hang.
    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "silent.jsonl")
    os.environ["FAKE_CLAUDE_HANG"] = "1"
    try:
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await asyncio.wait_for(mgr.wait(jid), timeout=15)
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)
        os.environ.pop("FAKE_CLAUDE_HANG", None)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "stopped"


async def test_completed_turn_that_lingers_finalizes_done(initdb: Path) -> None:
    """A turn that delivers its result and then lingers (a tool left a
    backgrounded child holding the pipe) must finalize as ``done`` — not be
    reaped by the idle watchdog and mis-recorded as stopped/failed."""
    with session_scope() as s:
        proj = models.Project(name="p", path="/tmp", idle_timeout_seconds=1)
        s.add(proj)
        s.flush()
        pid = proj.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM), default_idle_timeout_seconds=1)

    # Fixture ends with a `result` event; HANG makes the process linger after.
    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "tool_use_ok.jsonl")
    os.environ["FAKE_CLAUDE_HANG"] = "1"
    try:
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await asyncio.wait_for(mgr.wait(jid), timeout=15)
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)
        os.environ.pop("FAKE_CLAUDE_HANG", None)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "done", f"expected done, got {job.status}"


async def test_watchdog_does_not_kill_active_turn(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="p", path="/tmp", idle_timeout_seconds=30)
        s.add(proj)
        s.flush()
        pid = proj.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM), default_idle_timeout_seconds=30)

    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "tool_use_ok.jsonl")
    try:
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await asyncio.wait_for(mgr.wait(jid), timeout=10)
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "done"


async def test_watchdog_pauses_during_inflight_tool_call(initdb: Path) -> None:
    """A long Bash tool call (no events while it runs) must not trip the
    watchdog. Fixture emits tool_use, then the shim sleeps 3s before
    emitting tool_result. Idle timeout is 1s — without the heartbeat fix
    the job would be SIGTERM'd; with the fix it completes cleanly."""
    with session_scope() as s:
        proj = models.Project(name="p", path="/tmp", idle_timeout_seconds=1)
        s.add(proj)
        s.flush()
        pid = proj.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM), default_idle_timeout_seconds=1)

    # 3s pause between every line — far exceeds the 1s idle budget — but
    # the tool_use → tool_result span is "in flight" so the watchdog must
    # not kill the job.
    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "slow_tool.jsonl")
    try:
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await asyncio.wait_for(mgr.wait(jid), timeout=15)
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "done", f"expected done, got {job.status}"


async def test_task_override_wins_over_project_and_global(initdb: Path) -> None:
    """A task-level idle_timeout_seconds=1 stops a silent hang even though the
    project (600) and global default (600) would not. Proves the resolution
    order task → project → global."""
    with session_scope() as s:
        proj = models.Project(name="p", path="/tmp", idle_timeout_seconds=600)
        s.add(proj)
        s.flush()
        pid = proj.id
        task = models.Task(
            project_id=pid,
            title="train",
            prompt="train",
            status="running",
            mode="execute_only",
            idle_timeout_seconds=1,
        )
        s.add(task)
        s.flush()
        tid = task.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM), default_idle_timeout_seconds=600)

    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "silent.jsonl")
    os.environ["FAKE_CLAUDE_HANG"] = "1"
    try:
        jid = mgr.create_job(pid, prompt="train", task_id=tid, kind="execute")
        await mgr.start(jid)
        await asyncio.wait_for(mgr.wait(jid), timeout=15)
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)
        os.environ.pop("FAKE_CLAUDE_HANG", None)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "stopped", f"expected stopped, got {job.status}"


async def test_project_override_wins_over_global_default(initdb: Path) -> None:
    """Project with idle_timeout_seconds=1 stops; same scenario with default 60 wouldn't."""
    with session_scope() as s:
        proj = models.Project(name="p", path="/tmp", idle_timeout_seconds=1)
        s.add(proj)
        s.flush()
        pid = proj.id

    reg = BroadcasterRegistry(initdb / "logs")
    mgr = JobManager(reg, claude_path=str(SHIM), default_idle_timeout_seconds=600)

    os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "silent.jsonl")
    os.environ["FAKE_CLAUDE_HANG"] = "1"
    try:
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await asyncio.wait_for(mgr.wait(jid), timeout=15)
    finally:
        os.environ.pop("FAKE_CLAUDE_FIXTURE", None)
        os.environ.pop("FAKE_CLAUDE_HANG", None)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "stopped"
