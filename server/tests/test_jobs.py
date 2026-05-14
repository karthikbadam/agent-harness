from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.db import session_scope
from agent_harness.jobs import JobManager

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


def _make_project(name: str = "book", path: str = "/tmp") -> str:
    with session_scope() as s:
        p = models.Project(name=name, path=path)
        s.add(p)
        s.flush()
        return p.id


def _make_manager(ah_home: Path, fixture: str, max_concurrent: int = 2) -> JobManager:
    reg = BroadcasterRegistry(ah_home / "logs")
    mgr = JobManager(reg, max_concurrent=max_concurrent, claude_path=str(SHIM))
    # Inject the fake_claude fixture via env in a one-shot monkey-patch helper.
    mgr._fixture_env = {"FAKE_CLAUDE_FIXTURE": str(FIXTURES / "stream" / fixture)}  # type: ignore[attr-defined]
    # Wrap _run_turn_inner so the fake shim is fed the right fixture.
    real = mgr._run_turn_inner  # type: ignore[attr-defined]

    async def patched(job_id: str, turn_idx: int) -> None:
        # Patch the runner's env right before it spawns.
        original = JobManager._run_turn_inner  # type: ignore[attr-defined]

        # We monkey-patch ClaudeRunner via attribute on the mgr; simpler: inject
        # env at the OS level for the duration of the call.
        import os

        env_keys = list(mgr._fixture_env.keys())  # type: ignore[attr-defined]
        saved = {k: os.environ.get(k) for k in env_keys}
        for k, v in mgr._fixture_env.items():  # type: ignore[attr-defined]
            os.environ[k] = v
        try:
            await real(job_id, turn_idx)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    mgr._run_turn_inner = patched  # type: ignore[attr-defined]
    return mgr


async def test_job_run_persists_session_id_and_events(initdb: Path) -> None:
    pid = _make_project()
    mgr = _make_manager(initdb, "tool_use_ok.jsonl")
    jid = mgr.create_job(pid, prompt="hi")
    await mgr.start(jid)
    await mgr.wait(jid)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "done"
        assert job.session_id == "sess_xyz"
        turn = job.turns[0]
        assert turn.status == "done"
        assert turn.exit_code == 0
        assert turn.cost_usd == 0.0099
        assert turn.log_path is not None and Path(turn.log_path).exists()

    log_text = Path(job.turns[0].log_path).read_text()  # type: ignore[arg-type]
    assert log_text.count("\n") >= 5  # at least the 5 parsed events
    assert '"type":"job_status"' in log_text


async def test_followup_resumes_with_session_id(initdb: Path) -> None:
    pid = _make_project()
    mgr = _make_manager(initdb, "tool_use_ok.jsonl")
    jid = mgr.create_job(pid, prompt="first")
    await mgr.start(jid)
    await mgr.wait(jid)

    captured_resume: list[str | None] = []
    real_init = __import__("agent_harness.claude", fromlist=["ClaudeRunner"]).ClaudeRunner.__post_init__

    def spy_init(self):  # type: ignore[no-untyped-def]
        captured_resume.append(self.resume_session_id)
        return real_init(self)

    import agent_harness.claude as cmod

    cmod.ClaudeRunner.__post_init__ = spy_init  # type: ignore[method-assign]
    try:
        idx = await mgr.followup(jid, "second")
        await mgr.wait(jid)
    finally:
        cmod.ClaudeRunner.__post_init__ = real_init  # type: ignore[method-assign]

    assert idx == 1
    # Second turn should have seen the captured session_id.
    assert captured_resume[-1] == "sess_xyz"

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert len(job.turns) == 2
        assert job.turns[1].status == "done"


async def test_blocked_tool_failure_marks_failed(initdb: Path) -> None:
    pid = _make_project()
    mgr = _make_manager(initdb, "tool_blocked.jsonl")
    jid = mgr.create_job(pid, prompt="run pytest")
    await mgr.start(jid)
    await mgr.wait(jid)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "failed"
        assert job.turns[0].exit_code == 1

    log_path = Path(initdb) / "logs" / "jobs" / jid / "turn-0.jsonl"
    text = log_path.read_text()
    assert '"type":"tool_blocked"' in text
    assert '"suggested_rule":"Bash(pytest:*)"' in text


async def test_stop_kills_running_turn(initdb: Path) -> None:
    pid = _make_project()
    mgr = _make_manager(initdb, "text_only.jsonl")
    # Make the shim hang after streaming.
    mgr._fixture_env["FAKE_CLAUDE_HANG"] = "1"  # type: ignore[attr-defined]

    jid = mgr.create_job(pid, prompt="hi")
    await mgr.start(jid)

    # Wait briefly for the shim to stream its initial events.
    deadline = asyncio.get_event_loop().time() + 5
    while asyncio.get_event_loop().time() < deadline:
        if mgr.runner_for(jid) and mgr.runner_for(jid).pid:  # type: ignore[union-attr]
            break
        await asyncio.sleep(0.1)
    assert mgr.runner_for(jid) is not None

    assert await mgr.stop(jid) is True
    await mgr.wait(jid)

    with session_scope() as s:
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status in {"stopped", "failed"}  # SIGTERM exits non-zero


async def test_allowlist_rules_passed_to_runner(initdb: Path) -> None:
    pid = _make_project()
    with session_scope() as s:
        s.add(models.AllowlistRule(rule="Bash(npm test:*)"))
        s.add(models.AllowlistRule(project_id=pid, rule="Edit(**/*.py)"))

    captured_allowed: list[list[str]] = []
    import agent_harness.claude as cmod

    real_init = cmod.ClaudeRunner.__post_init__

    def spy_init(self):  # type: ignore[no-untyped-def]
        captured_allowed.append(list(self.allowed_tools))
        return real_init(self)

    cmod.ClaudeRunner.__post_init__ = spy_init  # type: ignore[method-assign]
    try:
        mgr = _make_manager(initdb, "text_only.jsonl")
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await mgr.wait(jid)
    finally:
        cmod.ClaudeRunner.__post_init__ = real_init  # type: ignore[method-assign]

    assert captured_allowed
    rules = sorted(captured_allowed[0])
    assert rules == sorted(["Bash(npm test:*)", "Edit(**/*.py)"])


async def test_dangerously_skip_clears_allowed_and_mode(initdb: Path) -> None:
    with session_scope() as s:
        p = models.Project(name="trusted", path="/tmp", dangerously_skip=True)
        s.add(p)
        s.flush()
        pid = p.id
        s.add(models.AllowlistRule(rule="Bash(*)"))

    captured_argv: list[list[str]] = []
    import agent_harness.claude as cmod

    real_argv = cmod.ClaudeRunner.build_argv

    def spy_argv(self):  # type: ignore[no-untyped-def]
        argv = real_argv(self)
        captured_argv.append(list(argv))
        return argv

    cmod.ClaudeRunner.build_argv = spy_argv  # type: ignore[method-assign]
    try:
        mgr = _make_manager(initdb, "text_only.jsonl")
        jid = mgr.create_job(pid, prompt="hi")
        await mgr.start(jid)
        await mgr.wait(jid)
    finally:
        cmod.ClaudeRunner.build_argv = real_argv  # type: ignore[method-assign]

    argv = captured_argv[0]
    assert "--dangerously-skip-permissions" in argv
    assert "--permission-mode" not in argv
    assert "--allowed-tools" not in argv
