"""Runtime configuration for agent-harness.

Layered configuration:
1. Defaults defined on `Settings`.
2. `~/.agent-harness/config.toml` (or `$AH_HOME/config.toml`).
3. Environment variables prefixed `AH_` (override file).

The TOML file holds secrets and install-time values (auth token, VAPID keys,
TLS cert paths). Env vars are mostly for tests / one-off overrides.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import tomli_w


def ah_home() -> Path:
    override = os.environ.get("AH_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agent-harness"


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8765
    db_path: Path | None = None
    logs_dir: Path | None = None
    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None
    auth_token: str | None = None
    vapid_private_key: str | None = None
    vapid_public_key: str | None = None
    vapid_subject: str = "mailto:user@localhost"
    claude_path: str | None = None
    default_claude_args: list[str] = field(default_factory=list)
    max_concurrent_jobs: int = 2
    idle_timeout_seconds: int = 600
    log_retention_days: int = 30
    web_dist: Path | None = None

    @property
    def home(self) -> Path:
        return ah_home()

    def resolve_paths(self) -> None:
        home = self.home
        home.mkdir(parents=True, exist_ok=True)
        if self.db_path is None:
            self.db_path = home / "harness.db"
        if self.logs_dir is None:
            self.logs_dir = home / "logs"
        (self.logs_dir / "jobs").mkdir(parents=True, exist_ok=True)
        if self.ssl_certfile is None:
            cert = home / "server.pem"
            self.ssl_certfile = cert if cert.exists() else None
        if self.ssl_keyfile is None:
            key = home / "server-key.pem"
            self.ssl_keyfile = key if key.exists() else None


def config_path() -> Path:
    return ah_home() / "config.toml"


def load_toml() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    return tomllib.loads(p.read_text())


def write_toml(data: dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(tomli_w.dumps(data))
    p.chmod(0o600)


def _env(name: str) -> str | None:
    return os.environ.get(f"AH_{name.upper()}")


def _int_env(name: str) -> int | None:
    v = _env(name)
    return int(v) if v else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    toml = load_toml()
    s = Settings(
        host=_env("host") or toml.get("host", "0.0.0.0"),
        port=_int_env("port") or int(toml.get("port", 8765)),
        auth_token=_env("auth_token") or toml.get("auth_token"),
        vapid_private_key=toml.get("vapid_private_key"),
        vapid_public_key=toml.get("vapid_public_key"),
        vapid_subject=toml.get("vapid_subject", "mailto:user@localhost"),
        claude_path=_env("claude_path") or toml.get("claude_path"),
        default_claude_args=list(toml.get("default_claude_args") or []),
        max_concurrent_jobs=_int_env("max_concurrent_jobs")
        or int(toml.get("max_concurrent_jobs", 2)),
        idle_timeout_seconds=_int_env("idle_timeout_seconds")
        or int(toml.get("idle_timeout_seconds", 600)),
        log_retention_days=_int_env("log_retention_days")
        or int(toml.get("log_retention_days", 30)),
    )
    s.resolve_paths()
    return s


def reset_settings_cache() -> None:
    """For tests: clear the cached settings so AH_HOME changes take effect."""
    get_settings.cache_clear()
