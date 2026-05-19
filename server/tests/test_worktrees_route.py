from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from agent_harness import config, models
from agent_harness.db import session_scope
from agent_harness.main import create_app
from agent_harness.services import worktrees


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True
    )
    (path / "f.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--no-gpg-sign", "-q", "-m", "init"],
        check=True,
    )


@pytest.fixture
async def app_client(initdb: Path):
    config.write_toml({"auth_token": "test-token"})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_list_worktrees_returns_main_and_task_with_task_id(
    app_client: httpx.AsyncClient, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    with session_scope() as s:
        proj = models.Project(name="r", path=str(repo))
        s.add(proj)
        s.flush()
        t = models.Task(project_id=proj.id, title="t", prompt="p", status="ready")
        s.add(t)
        s.flush()
        path, branch = worktrees.create(proj, t)
        t.worktree_path = path
        t.worktree_branch = branch
        pid, tid = proj.id, t.id

    r = await app_client.get(
        f"/api/projects/{pid}/worktrees",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    paths = [row["path"] for row in rows]
    assert str(repo) in paths
    task_paths = [row for row in rows if row["task_id"] == tid]
    assert len(task_paths) == 1
    assert task_paths[0]["branch"] is not None


async def test_list_worktrees_unknown_project_returns_404(
    app_client: httpx.AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/projects/nope/worktrees",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 404
