"""Pytest fixtures. Every test gets a fresh AH_HOME under tmp_path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_harness import config, db


# Disable the MCP streamable-http mount in tests. Its anyio task-group
# lifespan doesn't survive httpx.ASGITransport's task topology, causing
# "Attempted to exit cancel scope in a different task" on fixture teardown.
# MCP itself is exercised via the stdio binary and an external HTTP probe
# in the e2e suite — not the unit tests.
os.environ.setdefault("AH_DISABLE_MCP", "1")


@pytest.fixture
def ah_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ah"
    home.mkdir()
    monkeypatch.setenv("AH_HOME", str(home))
    config.reset_settings_cache()
    db.reset_engine()
    yield home
    config.reset_settings_cache()
    db.reset_engine()


@pytest.fixture
def settings(ah_home: Path):
    s = config.get_settings()
    return s


@pytest.fixture
def initdb(ah_home: Path):
    db.init_db()
    return ah_home
