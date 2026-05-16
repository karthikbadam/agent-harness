from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_harness import config, models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.claude import ClaudeRunner
from agent_harness.db import session_scope
from agent_harness.jobs import JobManager
from agent_harness.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"


def test_runner_appends_extra_args_after_permission_flags() -> None:
    r = ClaudeRunner(
        job_id="j",
        turn=0,
        prompt="hi",
        cwd="/tmp",
        claude_path=str(SHIM),
        extra_args=["--model", "claude-opus-4-7", "--add-dir", "/extra"],
    )
    argv = r.build_argv()
    # extras appear after the permission block, before nothing
    perm_idx = argv.index("--permission-mode")
    model_idx = argv.index("--model")
    add_dir_idx = argv.index("--add-dir")
    assert model_idx > perm_idx
    assert add_dir_idx > model_idx
    assert argv[model_idx + 1] == "claude-opus-4-7"
    assert argv[add_dir_idx + 1] == "/extra"


async def test_extra_args_flow_global_then_project(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(
            name="p",
            path="/tmp",
            extra_claude_args=["--model", "claude-opus-4-7"],
        )
        s.add(proj)
        s.flush()
        pid = proj.id

    captured: list[list[str]] = []
    import agent_harness.claude as cmod

    real = cmod.ClaudeRunner.build_argv

    def spy(self):  # type: ignore[no-untyped-def]
        argv = real(self)
        captured.append(list(argv))
        return argv

    cmod.ClaudeRunner.build_argv = spy  # type: ignore[method-assign]
    try:
        reg = BroadcasterRegistry(initdb / "logs")
        mgr = JobManager(
            reg,
            claude_path=str(SHIM),
            default_extra_args=["--add-dir", "/global"],
        )
        # Inject fake-claude fixture via env.
        import os

        os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "stream" / "text_only.jsonl")
        try:
            jid = mgr.create_job(pid, prompt="hi")
            await mgr.start(jid)
            await mgr.wait(jid)
        finally:
            os.environ.pop("FAKE_CLAUDE_FIXTURE", None)
    finally:
        cmod.ClaudeRunner.build_argv = real  # type: ignore[method-assign]

    argv = captured[0]
    # global args come first, then project args
    assert "--add-dir" in argv and "/global" in argv
    assert "--model" in argv and "claude-opus-4-7" in argv
    assert argv.index("--add-dir") < argv.index("--model")


async def test_project_route_accepts_and_returns_extra_args(initdb: Path) -> None:
    config.write_toml({"auth_token": "T"})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            r = await client.post(
                "/api/projects",
                json={
                    "name": "p",
                    "path": "/tmp",
                    "extra_claude_args": ["--model", "claude-opus-4-7"],
                },
                headers={"Authorization": "Bearer T"},
            )
            assert r.status_code == 201
            assert r.json()["extra_claude_args"] == ["--model", "claude-opus-4-7"]
            pid = r.json()["id"]

            r = await client.patch(
                f"/api/projects/{pid}",
                json={"extra_claude_args": ["--add-dir", "/d"]},
                headers={"Authorization": "Bearer T"},
            )
            assert r.json()["extra_claude_args"] == ["--add-dir", "/d"]


def test_settings_loads_default_claude_args(ah_home: Path) -> None:
    config.write_toml({"auth_token": "T", "default_claude_args": ["--model", "x"]})
    config.reset_settings_cache()
    s = config.get_settings()
    assert s.default_claude_args == ["--model", "x"]
