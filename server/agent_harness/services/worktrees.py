"""Per-task git worktrees.

When a plan-then-execute task transitions from planning to executing, the
runner creates a worktree at ``<AH_HOME>/worktrees/<task_id>`` on a new branch
``task/<task_id>`` off the project's current HEAD. The execute turn runs there,
isolated from other concurrent executes that share the project repo.

After integration succeeds the worktree + branch are removed. Orphaned
worktrees (server killed mid-execute, manual filesystem mess) can be listed
via :func:`list_outstanding` and removed manually.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .. import models
from ..config import ah_home

log = logging.getLogger(__name__)


def _worktrees_root() -> Path:
    root = ah_home() / "worktrees"
    root.mkdir(parents=True, exist_ok=True)
    return root


def worktree_path_for(task_id: str) -> Path:
    return _worktrees_root() / task_id


def branch_name_for(task_id: str) -> str:
    return f"task/{task_id}"


def create(project: models.Project, task: models.Task) -> tuple[str, str]:
    """Create a git worktree for ``task`` off ``project.path``.

    Returns ``(path, branch)``. Raises ``FileExistsError`` if the target
    directory already exists, and ``subprocess.CalledProcessError`` if git
    refuses (e.g. the branch already exists, repo is not a git work tree).
    """
    path = worktree_path_for(task.id)
    branch = branch_name_for(task.id)
    if path.exists():
        raise FileExistsError(f"worktree path already exists: {path}")
    subprocess.run(
        [
            "git",
            "-C",
            project.path,
            "worktree",
            "add",
            str(path),
            "-b",
            branch,
        ],
        check=True,
        capture_output=True,
    )
    return str(path), branch


def remove(project: models.Project, task: models.Task) -> None:
    """Remove the task's worktree and force-delete its branch. Best-effort.

    Any git error is logged and swallowed so cleanup never blocks the
    integration finalize path. The caller should clear the task's
    ``worktree_path`` / ``worktree_branch`` fields after a successful call.
    """
    if task.worktree_path:
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    project.path,
                    "worktree",
                    "remove",
                    "--force",
                    task.worktree_path,
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            log.warning(
                "worktree remove failed for %s: %s",
                task.id,
                e.stderr.decode("utf-8", "replace").strip(),
            )
    if task.worktree_branch:
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    project.path,
                    "branch",
                    "-D",
                    task.worktree_branch,
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            log.warning(
                "branch -D failed for %s: %s",
                task.id,
                e.stderr.decode("utf-8", "replace").strip(),
            )


def list_outstanding(project: models.Project) -> list[dict[str, str]]:
    """Return parsed ``git worktree list --porcelain`` entries.

    Each entry has the keys git emits: ``worktree``, ``HEAD``, ``branch``,
    ``bare``, ``detached``, etc. Returns ``[]`` on any error (not a git repo,
    git not installed, etc.).
    """
    try:
        out = subprocess.check_output(
            ["git", "-C", project.path, "worktree", "list", "--porcelain"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if " " in line:
            k, v = line.split(" ", 1)
            current[k] = v
        else:
            current[line] = ""
    if current:
        entries.append(current)
    return entries
