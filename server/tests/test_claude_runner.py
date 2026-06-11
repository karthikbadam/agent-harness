from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_harness import schemas
from agent_harness.claude import ClaudeRunner, resolve_claude_path


FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


def _runner_with_fixture(fixture: str, **kwargs: object) -> ClaudeRunner:
    env = {"FAKE_CLAUDE_FIXTURE": str(FIXTURES / "stream" / fixture)}
    extra_env = kwargs.pop("env", None)
    if isinstance(extra_env, dict):
        env.update({str(k): str(v) for k, v in extra_env.items()})
    return ClaudeRunner(
        job_id="j1",
        turn=0,
        prompt="hello",
        cwd="/tmp",
        claude_path=str(SHIM),
        env=env,
        **kwargs,  # type: ignore[arg-type]
    )


def test_argv_default_includes_accept_edits() -> None:
    r = ClaudeRunner(job_id="j", turn=0, prompt="hi", cwd="/tmp", claude_path=str(SHIM))
    argv = r.build_argv()
    assert argv[0] == str(SHIM)
    assert argv[1] == "-p"
    assert argv[2] == "hi"
    assert "--output-format" in argv and "stream-json" in argv
    assert "--verbose" in argv
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_argv_with_resume_and_allowed_tools() -> None:
    r = ClaudeRunner(
        job_id="j",
        turn=1,
        prompt="hi",
        cwd="/tmp",
        claude_path=str(SHIM),
        resume_session_id="sess_1",
        allowed_tools=["Bash(npm test:*)", "Edit(**/*.py)"],
    )
    argv = r.build_argv()
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "sess_1"
    assert "--allowed-tools" in argv
    assert argv[argv.index("--allowed-tools") + 1] == "Bash(npm test:*),Edit(**/*.py)"


def test_argv_dangerously_skip_overrides_permission_flags() -> None:
    r = ClaudeRunner(
        job_id="j",
        turn=0,
        prompt="hi",
        cwd="/tmp",
        claude_path=str(SHIM),
        dangerously_skip=True,
        allowed_tools=["Bash(*)"],
    )
    argv = r.build_argv()
    assert "--dangerously-skip-permissions" in argv
    assert "--permission-mode" not in argv
    assert "--allowed-tools" not in argv


def test_resolve_claude_path_override_wins(tmp_path: Path) -> None:
    fake = tmp_path / "x"
    fake.write_text("")
    assert resolve_claude_path(str(fake)) == str(fake)


def test_resolve_claude_path_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "y"
    fake.write_text("")
    monkeypatch.setenv("AH_CLAUDE_PATH", str(fake))
    assert resolve_claude_path() == str(fake)


def test_resolve_claude_path_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AH_CLAUDE_PATH", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-dir-xyz")
    with pytest.raises(FileNotFoundError):
        resolve_claude_path()


async def _collect(r: ClaudeRunner) -> list[schemas.StreamEvent]:
    out: list[schemas.StreamEvent] = []
    async for ev in r.run():
        out.append(ev)
    return out


async def test_runner_streams_events_and_captures_session_id() -> None:
    r = _runner_with_fixture("tool_use_ok.jsonl")
    events = await _collect(r)
    assert [e.type for e in events] == [
        "assistant_text",
        "tool_use",
        "tool_result",
        "assistant_text",
        "turn_done",
    ]
    assert r.session_id == "sess_xyz"
    assert r.returncode == 0


async def test_runner_handles_unknown_event_gracefully() -> None:
    r = _runner_with_fixture("mixed.jsonl")
    events = await _collect(r)
    assert "turn_done" in [e.type for e in events]


async def test_runner_stop_sends_sigterm_and_exits() -> None:
    r = _runner_with_fixture("text_only.jsonl", env={"FAKE_CLAUDE_HANG": "1"})

    received: list[schemas.StreamEvent] = []

    async def consume() -> None:
        async for ev in r.run():
            received.append(ev)

    task = asyncio.create_task(consume())
    # Wait until we've drained the fixture's events.
    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(e.type == "turn_done" for e in received):
            break
    assert any(e.type == "turn_done" for e in received), "fixture should stream first"

    await r.stop()
    await asyncio.wait_for(task, timeout=10)
    assert r.returncode is not None
    assert r.returncode != 0  # killed


async def test_runner_stop_when_not_started_is_noop() -> None:
    r = ClaudeRunner(job_id="j", turn=0, prompt="hi", cwd="/tmp", claude_path=str(SHIM))
    await r.stop()  # no exception
