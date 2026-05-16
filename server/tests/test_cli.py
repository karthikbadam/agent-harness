from __future__ import annotations

from pathlib import Path

from agent_harness import cli, config


def test_init_creates_dirs_and_db(ah_home: Path) -> None:
    assert cli.main(["init"]) == 0
    s = config.get_settings()
    assert s.db_path is not None and s.db_path.exists()
    assert s.logs_dir is not None and (s.logs_dir / "jobs").is_dir()


def test_gen_token_is_idempotent(ah_home: Path) -> None:
    assert cli.main(["init"]) == 0
    assert cli.main(["gen-token"]) == 0
    token1 = config.load_toml()["auth_token"]
    assert cli.main(["gen-token"]) == 0
    token2 = config.load_toml()["auth_token"]
    assert token1 == token2
    assert cli.main(["gen-token", "--force"]) == 0
    token3 = config.load_toml()["auth_token"]
    assert token3 != token1


def test_gen_openapi_dumps_spec(ah_home: Path, tmp_path: Path) -> None:
    out = tmp_path / "spec.json"
    assert cli.main(["gen-openapi", str(out)]) == 0
    assert out.exists()
    text = out.read_text()
    assert '"openapi"' in text
    assert "/healthz" in text
