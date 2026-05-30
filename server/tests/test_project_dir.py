from __future__ import annotations

import subprocess
from pathlib import Path

from agent_harness.routes.projects import _ensure_project_dir


def test_ensure_project_dir_creates_git_repo(tmp_path: Path) -> None:
    d = tmp_path / "fresh-project"
    _ensure_project_dir(str(d))
    assert d.is_dir()
    assert (d / ".git").exists()
    # An initial commit means HEAD exists (loops branch from it).
    sha = (
        subprocess.check_output(["git", "-C", str(d), "rev-parse", "HEAD"])
        .decode()
        .strip()
    )
    assert len(sha) == 40
    assert (d / ".gitignore").exists()
    # Idempotent: running again on an existing repo is a no-op, no error.
    _ensure_project_dir(str(d))
    sha2 = (
        subprocess.check_output(["git", "-C", str(d), "rev-parse", "HEAD"])
        .decode()
        .strip()
    )
    assert sha2 == sha


def test_ensure_project_dir_nested_path(tmp_path: Path) -> None:
    d = tmp_path / "a" / "b" / "c"
    _ensure_project_dir(str(d))
    assert d.is_dir() and (d / ".git").exists()
