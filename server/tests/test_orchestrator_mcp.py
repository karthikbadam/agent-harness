from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from agent_harness.services import orchestrator_mcp


def test_build_mcp_registers_expected_tools() -> None:
    """Adding a new MCP tool means it must be a typed entry here too."""
    mcp = orchestrator_mcp.build_mcp()
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_projects",
        "get_project",
        "update_project_context",
        "plan_ask",
        "list_tasks",
        "update_task",
        "split_task",
        "merge_tasks",
        "run_task",
        "ack_plan",
        "cancel_task",
        "list_outcomes",
        "integrate",
        "list_worktrees",
        "set_autopilot",
        "get_driver_state",
        "get_suggestions",
        "list_driver_notes",
        "acknowledge_note",
        "tail_job",
    }


class _StubClient:
    """Minimal httpx.Client stand-in that records calls + returns canned JSON."""

    def __init__(self, canned: dict[tuple[str, str], Any]):
        self._canned = canned
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __enter__(self) -> "_StubClient":
        return self

    def __exit__(self, *exc: Any) -> None:  # noqa: ANN401
        return None

    def _respond(self, method: str, url: str, body: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append((method, url, body))
        payload = self._canned.get((method, url), [])
        req = httpx.Request(method, url)
        return httpx.Response(200, json=payload, request=req)

    def get(self, url: str, **_: Any) -> httpx.Response:  # noqa: ANN401
        return self._respond("GET", url)

    def post(self, url: str, json: dict[str, Any] | None = None, **_: Any) -> httpx.Response:  # noqa: ANN401
        return self._respond("POST", url, json)

    def patch(self, url: str, json: dict[str, Any] | None = None, **_: Any) -> httpx.Response:  # noqa: ANN401
        return self._respond("PATCH", url, json)


def test_list_tasks_filters_locally_by_status() -> None:
    stub = _StubClient(
        canned={
            (
                "GET",
                "/api/projects/p1/tasks",
            ): [
                {"id": "a", "title": "x", "status": "ready"},
                {"id": "b", "title": "y", "status": "pending"},
            ]
        }
    )
    mcp = orchestrator_mcp.build_mcp()
    with patch.object(orchestrator_mcp, "_client", lambda: stub):
        res = asyncio.run(mcp.call_tool("list_tasks", {"project_id": "p1", "status": "ready"}))
    payload = res[0] if isinstance(res, list) else res
    # FastMCP returns (content, structured) tuples in newer versions; coerce.
    assert any(
        isinstance(item, dict) and item.get("status") == "ready"
        for item in (payload if isinstance(payload, list) else _items_from(res))
    )


def _items_from(res: Any) -> list[dict[str, Any]]:
    # call_tool returns (content_list, structured_payload) or a CallToolResult.
    structured = getattr(res, "structuredContent", None) or (
        res[1] if isinstance(res, tuple) and len(res) > 1 else None
    )
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if isinstance(structured, list):
        return structured
    return []


def test_list_outcomes_requires_exactly_one_of_task_or_project() -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    stub = _StubClient(canned={})
    mcp = orchestrator_mcp.build_mcp()
    with patch.object(orchestrator_mcp, "_client", lambda: stub):
        with pytest.raises(ToolError, match="task_id or project_id"):
            asyncio.run(mcp.call_tool("list_outcomes", {}))
        with pytest.raises(ToolError, match="exactly one"):
            asyncio.run(mcp.call_tool("list_outcomes", {"task_id": "x", "project_id": "y"}))


def test_integrate_calls_project_integrate_route() -> None:
    stub = _StubClient(canned={("POST", "/api/projects/p1/integrate"): {"id": "synth-1"}})
    mcp = orchestrator_mcp.build_mcp()
    with patch.object(orchestrator_mcp, "_client", lambda: stub):
        asyncio.run(
            mcp.call_tool(
                "integrate",
                {"project_id": "p1", "task_ids": ["t1", "t2"], "target_branch": "main"},
            )
        )
    assert stub.calls == [
        ("POST", "/api/projects/p1/integrate", {"task_ids": ["t1", "t2"], "target_branch": "main"})
    ]


def test_ack_plan_uses_task_ack_route() -> None:
    stub = _StubClient(canned={("POST", "/api/tasks/tx/ack?notes=go"): {"id": "jx"}})
    mcp = orchestrator_mcp.build_mcp()
    with patch.object(orchestrator_mcp, "_client", lambda: stub):
        asyncio.run(mcp.call_tool("ack_plan", {"task_id": "tx", "notes": "go"}))
    assert stub.calls == [("POST", "/api/tasks/tx/ack?notes=go", None)]


def test_ack_plan_bare_uses_clean_path() -> None:
    stub = _StubClient(canned={("POST", "/api/tasks/tx/ack"): {"id": "jx"}})
    mcp = orchestrator_mcp.build_mcp()
    with patch.object(orchestrator_mcp, "_client", lambda: stub):
        asyncio.run(mcp.call_tool("ack_plan", {"task_id": "tx"}))
    assert stub.calls == [("POST", "/api/tasks/tx/ack", None)]
