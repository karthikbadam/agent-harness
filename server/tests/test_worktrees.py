from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.services import worktrees


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True)
    (path / "f.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--no-gpg-sign", "-q", "-m", "init"],
        check=True,
    )


def _proj_and_task(
    repo: Path, task_id: str = "tabc123def456"
) -> tuple[models.Project, models.Task]:
    proj = models.Project(name="r", path=str(repo))
    task = models.Task(id=task_id, project_id="p", title="t", prompt="p")
    return proj, task


def test_create_makes_worktree_and_branch(ah_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    proj, task = _proj_and_task(repo)

    path, branch = worktrees.create(proj, task)

    assert Path(path).exists()
    assert Path(path) == ah_home / "worktrees" / task.id
    assert branch == f"task/{task.id}"
    # branch exists on the source repo
    out = subprocess.check_output(["git", "-C", str(repo), "branch", "--list", branch]).decode()
    assert branch in out


def test_create_rejects_existing_path(ah_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    proj, task = _proj_and_task(repo)
    worktrees.worktree_path_for(task.id).mkdir(parents=True)
    with pytest.raises(FileExistsError):
        worktrees.create(proj, task)


def test_remove_cleans_worktree_and_branch(ah_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    proj, task = _proj_and_task(repo)
    path, branch = worktrees.create(proj, task)
    task.worktree_path = path
    task.worktree_branch = branch

    worktrees.remove(proj, task)

    assert not Path(path).exists()
    out = (
        subprocess.check_output(["git", "-C", str(repo), "branch", "--list", branch])
        .decode()
        .strip()
    )
    assert out == ""


def test_remove_is_best_effort_when_already_gone(ah_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    proj, task = _proj_and_task(repo)
    task.worktree_path = str(tmp_path / "nope")
    task.worktree_branch = "task/does-not-exist"
    # Should not raise.
    worktrees.remove(proj, task)


def test_list_outstanding_includes_main_and_task(ah_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    proj, task = _proj_and_task(repo)
    worktrees.create(proj, task)

    entries = worktrees.list_outstanding(proj)

    paths = [e.get("worktree", "") for e in entries]
    assert any(p == str(repo) for p in paths)
    assert any(p.endswith(task.id) for p in paths)


def test_list_outstanding_returns_empty_for_non_repo(ah_home: Path, tmp_path: Path) -> None:
    notrepo = tmp_path / "notrepo"
    notrepo.mkdir()
    proj = models.Project(name="r", path=str(notrepo))
    assert worktrees.list_outstanding(proj) == []
