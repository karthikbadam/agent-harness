"""Startup bootstrap: ensure a default project exists.

Keeps the UI dead-simple: a brand-new install can submit a job with just a
prompt — the harness already has a `__default` project pointing at
`~/agent-harness-workspace`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from . import models
from .db import session_scope

log = logging.getLogger(__name__)

DEFAULT_PROJECT_NAME = "__default"
DEFAULT_PROJECT_PATH = Path.home() / "agent-harness-workspace"


def ensure_default_project() -> str:
    """Make sure exactly one project is flagged default. Return its id.

    The default project runs with `dangerously_skip=True` so jobs can do real
    work without per-tool approval prompts that the harness has no UI to
    answer. This is intentional for the single-user, on-your-own-Mac model;
    rename `__default` if you want a stricter project.
    """
    with session_scope() as s:
        existing = s.execute(
            select(models.Project).where(models.Project.is_default.is_(True))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.name == DEFAULT_PROJECT_NAME and not existing.dangerously_skip:
                existing.dangerously_skip = True
                log.info("upgraded existing __default to dangerously_skip=True")
            return existing.id

        DEFAULT_PROJECT_PATH.mkdir(parents=True, exist_ok=True)
        proj = models.Project(
            name=DEFAULT_PROJECT_NAME,
            path=str(DEFAULT_PROJECT_PATH),
            is_default=True,
            dangerously_skip=True,
        )
        s.add(proj)
        s.flush()
        log.info("bootstrapped default project %s at %s", proj.id, DEFAULT_PROJECT_PATH)
        return proj.id
