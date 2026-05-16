"""Pytest fixtures. Every test gets a fresh AH_HOME under tmp_path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_harness import config, db


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
