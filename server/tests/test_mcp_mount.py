from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_harness import config
from agent_harness.main import create_app


@pytest.fixture
async def app_client(initdb: Path, monkeypatch: pytest.MonkeyPatch):
    config.write_toml({"auth_token": "test-token"})
    config.reset_settings_cache()
    # Enable MCP mount for this test file (conftest disables it globally).
    monkeypatch.delenv("AH_DISABLE_MCP", raising=False)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # We don't run the FastAPI lifespan here — the bearer-guard tests
        # only need to exercise the auth middleware, not the inner MCP
        # session manager. Skipping the lifespan avoids the anyio
        # cross-task cancel-scope issue during teardown.
        yield c


async def test_mcp_mount_rejects_missing_token(app_client) -> None:
    # No Authorization header → 401, regardless of method/body.
    r = await app_client.get("/mcp/")
    assert r.status_code == 401


async def test_mcp_mount_rejects_wrong_token(app_client) -> None:
    r = await app_client.get(
        "/mcp/", headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401


async def test_mcp_mount_passes_with_correct_token(app_client) -> None:
    # We don't fully handshake MCP here — just verify the guard lets the
    # request through to the inner app, which then responds (often with a
    # 4xx/5xx for a malformed JSON-RPC body, NOT a 401).
    r = await app_client.get(
        "/mcp/", headers={"Authorization": "Bearer test-token"}
    )
    assert r.status_code != 401
