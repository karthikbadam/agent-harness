from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_harness import schemas
from agent_harness.claude import AttachedFile
from agent_harness.codex import CodexRunner, resolve_codex_path

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_codex.sh"


def _runner_with_fixture(fixture: str, **kwargs: object) -> CodexRunner:
    env = {"FAKE_CODEX_FIXTURE": str(FIXTURES / "codex" / fixture)}
    extra_env = kwargs.pop("env", None)
    if isinstance(extra_env, dict):
        env.update({str(k): str(v) for k, v in extra_env.items()})
    return CodexRunner(
        job_id="j1",
        turn=0,
        prompt="hello",
        cwd="/tmp",
        codex_path=str(SHIM),
        env=env,
        **kwargs,  # type: ignore[arg-type]
    )


def test_argv_fresh_shape() -> None:
    r = CodexRunner(
        job_id="j", turn=0, prompt="do it", cwd="/repo", codex_path=str(SHIM), sandbox="read-only"
    )
    argv = r.build_argv()
    assert argv[0] == str(SHIM)
    # Approval + sandbox are globals before `exec`.
    assert argv[1:5] == ["-a", "never", "-s", "read-only"]
    assert "--cd" in argv and argv[argv.index("--cd") + 1] == "/repo"
    assert "exec" in argv
    assert "resume" not in argv
    assert "--json" in argv and "--skip-git-repo-check" in argv
    # Prompt is the trailing positional.
    assert argv[-1] == "do it"
    # Approval flag precedes exec.
    assert argv.index("-a") < argv.index("exec")


def test_argv_resume_uses_subcommand_with_session_id() -> None:
    r = CodexRunner(
        job_id="j",
        turn=1,
        prompt="more",
        cwd="/repo",
        codex_path=str(SHIM),
        resume_session_id="019ecd2b-abcd",
    )
    argv = r.build_argv()
    i = argv.index("exec")
    assert argv[i + 1] == "resume"
    assert argv[i + 2] == "019ecd2b-abcd"
    assert argv[-1] == "more"


def test_argv_workspace_write_for_execute() -> None:
    r = CodexRunner(
        job_id="j", turn=0, prompt="x", cwd="/repo", codex_path=str(SHIM), sandbox="workspace-write"
    )
    argv = r.build_argv()
    assert argv[argv.index("-s") + 1] == "workspace-write"


def test_argv_dangerously_skip_bypasses_sandbox_and_approval() -> None:
    r = CodexRunner(
        job_id="j", turn=0, prompt="x", cwd="/repo", codex_path=str(SHIM), dangerously_skip=True
    )
    argv = r.build_argv()
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "-a" not in argv
    assert "-s" not in argv


def test_argv_model_and_image_attachments() -> None:
    r = CodexRunner(
        job_id="j",
        turn=0,
        prompt="x",
        cwd="/repo",
        codex_path=str(SHIM),
        model="o3",
        attachments=[
            AttachedFile(path="/tmp/a.png", filename="a.png", mime_type="image/png"),
            AttachedFile(path="/tmp/notes.txt", filename="notes.txt", mime_type="text/plain"),
        ],
    )
    argv = r.build_argv()
    assert argv[argv.index("-m") + 1] == "o3"
    # Image attachment passed via -i; non-image goes into the prompt text.
    assert "-i" in argv and argv[argv.index("-i") + 1] == "/tmp/a.png"
    assert "notes.txt" in argv[-1]
    assert "/tmp/notes.txt" in argv[-1]


def test_resolve_codex_path_override_wins(tmp_path: Path) -> None:
    fake = tmp_path / "x"
    fake.write_text("")
    assert resolve_codex_path(str(fake)) == str(fake)


def test_resolve_codex_path_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "y"
    fake.write_text("")
    monkeypatch.setenv("AH_CODEX_PATH", str(fake))
    assert resolve_codex_path() == str(fake)


def test_resolve_codex_path_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AH_CODEX_PATH", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-dir-xyz")
    with pytest.raises(FileNotFoundError):
        resolve_codex_path()


async def _collect(r: CodexRunner) -> list[schemas.StreamEvent]:
    out: list[schemas.StreamEvent] = []
    async for ev in r.run():
        out.append(ev)
    return out


async def test_runner_streams_events_and_captures_session_id() -> None:
    r = _runner_with_fixture("fresh.jsonl")
    events = await _collect(r)
    assert [e.type for e in events] == [
        "tool_use",
        "tool_result",
        "assistant_text",
        "turn_done",
    ]
    assert r.session_id == "019ecd2b-adc5-7fe2-8e7f-526fc9c89879"
    assert r.returncode == 0


async def test_runner_stop_sends_sigterm_and_exits() -> None:
    r = _runner_with_fixture("fresh.jsonl", env={"FAKE_CODEX_HANG": "1"})
    received: list[schemas.StreamEvent] = []

    async def consume() -> None:
        async for ev in r.run():
            received.append(ev)

    task = asyncio.create_task(consume())
    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(e.type == "turn_done" for e in received):
            break
    assert any(e.type == "turn_done" for e in received), "fixture should stream first"
    await r.stop()
    await asyncio.wait_for(task, timeout=10)
    assert r.returncode is not None
    assert r.returncode != 0


async def test_runner_stop_when_not_started_is_noop() -> None:
    r = CodexRunner(job_id="j", turn=0, prompt="hi", cwd="/tmp", codex_path=str(SHIM))
    await r.stop()  # no exception
