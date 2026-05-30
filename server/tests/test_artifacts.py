from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_harness import config
from agent_harness.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SHIM = FIXTURES / "fake_claude.sh"
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
async def app_client(initdb: Path, monkeypatch: pytest.MonkeyPatch):
    config.write_toml({"auth_token": "test-token", "claude_path": str(SHIM)})
    config.reset_settings_cache()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _make_project_and_task(client, project_path: str) -> tuple[str, str]:
    r = await client.post(
        "/api/projects",
        headers=AUTH,
        json={"name": "p", "path": project_path},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/tasks",
        headers=AUTH,
        json={"title": "t", "prompt": "do", "mode": "one_shot"},
    )
    assert r.status_code == 201, r.text
    return pid, r.json()["id"]


async def test_register_list_download_artifact(app_client, tmp_path: Path) -> None:
    client, _ = app_client
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()
    # Agent wrote a progress graph in the project dir.
    (proj_dir / "progress.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    _, tid = await _make_project_and_task(client, str(proj_dir))

    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "graph", "path": "progress.png", "meta": {"iteration": 3}},
    )
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["kind"] == "graph"
    assert art["name"] == "progress.png"
    assert art["meta"]["iteration"] == 3
    assert art["download_url"].endswith("/download")

    # List shows it.
    r = await client.get(f"/api/tasks/{tid}/artifacts", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Download returns the bytes.
    r = await client.get(art["download_url"], headers=AUTH)
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


async def test_svg_without_extension_serves_image_type(app_client, tmp_path: Path) -> None:
    """Agents register graphs with a display name and no extension. The download
    must sniff the bytes and serve image/svg+xml so <img> renders it."""
    client, _ = app_client
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()
    (proj_dir / "chart.out").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    )
    _, tid = await _make_project_and_task(client, str(proj_dir))
    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "graph", "path": "chart.out", "name": "Progress graph"},
    )
    assert r.status_code == 201, r.text
    r = await client.get(r.json()["download_url"], headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")


async def test_reregister_same_name_updates_in_place(app_client, tmp_path: Path) -> None:
    client, _ = app_client
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()
    png = proj_dir / "progress.png"

    _, tid = await _make_project_and_task(client, str(proj_dir))

    png.write_bytes(b"\x89PNGv1")
    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "graph", "path": "progress.png", "meta": {"iteration": 1}},
    )
    assert r.status_code == 201

    png.write_bytes(b"\x89PNGv2-longer")
    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "graph", "path": "progress.png", "meta": {"iteration": 2}},
    )
    assert r.status_code == 201

    r = await client.get(f"/api/tasks/{tid}/artifacts", headers=AUTH)
    arts = r.json()
    assert len(arts) == 1, "re-register should update in place, not duplicate"
    assert arts[0]["meta"]["iteration"] == 2

    r = await client.get(arts[0]["download_url"], headers=AUTH)
    assert r.content == b"\x89PNGv2-longer"


async def test_path_traversal_rejected(app_client, tmp_path: Path) -> None:
    client, _ = app_client
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()
    # A secret outside the project tree.
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")

    _, tid = await _make_project_and_task(client, str(proj_dir))

    # Relative traversal out of the tree.
    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "file", "path": "../secret.txt"},
    )
    assert r.status_code == 400, r.text

    # Absolute path outside the tree.
    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "file", "path": str(secret)},
    )
    assert r.status_code == 400, r.text


async def test_missing_source_400(app_client, tmp_path: Path) -> None:
    client, _ = app_client
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()
    _, tid = await _make_project_and_task(client, str(proj_dir))
    r = await client.post(
        f"/api/tasks/{tid}/artifacts",
        headers=AUTH,
        json={"kind": "file", "path": "nope.txt"},
    )
    assert r.status_code == 400
