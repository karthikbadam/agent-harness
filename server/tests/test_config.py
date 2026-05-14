from __future__ import annotations

from pathlib import Path

from agent_harness import config


def test_ah_home_env_override(ah_home: Path) -> None:
    assert config.ah_home() == ah_home


def test_settings_resolves_paths(ah_home: Path) -> None:
    s = config.get_settings()
    assert s.db_path == ah_home / "harness.db"
    assert s.logs_dir == ah_home / "logs"
    assert (ah_home / "logs" / "jobs").is_dir()


def test_write_and_read_toml(ah_home: Path) -> None:
    config.write_toml({"auth_token": "abc", "port": 9000})
    loaded = config.load_toml()
    assert loaded["auth_token"] == "abc"
    assert loaded["port"] == 9000
    config.reset_settings_cache()
    s = config.get_settings()
    assert s.auth_token == "abc"
    assert s.port == 9000
