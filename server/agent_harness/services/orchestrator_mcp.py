"""Orchestrator MCP server.

Exposes the agent-harness API as a typed MCP tool surface. Two transports:

- **HTTP** mounted under ``/mcp`` on the running FastAPI app
  (see ``main.py``). Reuses the existing bearer-token auth.
- **Stdio** binary ``agent-harness-mcp`` (see ``pyproject.toml`` scripts entry)
  for clients that spawn an MCP server as a subprocess (Claude Code, MCP
  Inspector, etc.). The stdio binary proxies every call to the running
  harness over HTTP, so the running harness is the single source of truth.

Tools are typed (no freeform "orchestrate this" prompts) and 1:1 with REST
endpoints — adding capability means adding both a route and a tool.

Note: this server is **not** auto-injected into harness jobs. To drive the
loop from an agent, point a separate Claude session at this MCP transport
from outside the harness.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

from ..config import ah_home, load_toml


def _base_url() -> str:
    return os.environ.get("AGENT_HARNESS_URL") or "http://127.0.0.1:8765"


def _auth_token() -> str | None:
    tok = os.environ.get("AGENT_HARNESS_TOKEN")
    if tok:
        return tok
    try:
        return load_toml().get("auth_token")  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001
        return None


def _client() -> httpx.Client:
    headers: dict[str, str] = {}
    tok = _auth_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return httpx.Client(base_url=_base_url(), headers=headers, timeout=30.0)


def _ok(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        raise RuntimeError(f"{r.request.method} {r.request.url}: {r.status_code} {r.text}")
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def build_mcp() -> Any:
    """Construct the FastMCP server with all tools registered.

    Kept as a function so tests can spin up a fresh server, and so the HTTP
    mount in ``main.py`` can reuse the same definitions.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name="agent-harness",
        instructions=(
            "Typed orchestration surface for agent-harness. Use list_tasks / "
            "list_outcomes / get_project to read state; plan_ask to draft "
            "tasks from a high-level ask; split_task / merge_tasks to reshape "
            "the DAG; run_task to kick a ready task; ack_plan to advance a "
            "task past its planning gate; integrate to merge a wave of "
            "completed worktrees back into the project branch."
        ),
    )

    @mcp.tool()
    def list_projects() -> list[dict[str, Any]]:
        """List every project the harness knows about."""
        with _client() as c:
            return _ok(c.get("/api/projects"))

    @mcp.tool()
    def get_project(project_id: str) -> dict[str, Any]:
        """Fetch a single project by id."""
        with _client() as c:
            return _ok(c.get(f"/api/projects/{project_id}"))

    @mcp.tool()
    def update_project_context(
        project_id: str,
        instructions: str | None = None,
        skills: list[str] | None = None,
        context_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update the project's instructions/skills/context_paths.

        Any argument left as None is left unchanged on the server.
        """
        body: dict[str, Any] = {}
        if instructions is not None:
            body["instructions"] = instructions
        if skills is not None:
            body["skills"] = skills
        if context_paths is not None:
            body["context_paths"] = context_paths
        with _client() as c:
            return _ok(c.patch(f"/api/projects/{project_id}", json=body))

    @mcp.tool()
    def plan_ask(project_id: str, ask: str) -> dict[str, Any]:
        """Run the planner against an ask. Returns the drafted task ids."""
        with _client() as c:
            return _ok(c.post(f"/api/projects/{project_id}/plan", json={"ask": ask}))

    @mcp.tool()
    def list_tasks(project_id: str, status: str | None = None) -> list[dict[str, Any]]:
        """List tasks in a project, optionally filtered by status."""
        with _client() as c:
            tasks = _ok(c.get(f"/api/projects/{project_id}/tasks"))
        if status:
            return [t for t in tasks if t.get("status") == status]
        return tasks

    @mcp.tool()
    def update_task(
        task_id: str,
        title: str | None = None,
        prompt: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any]:
        """Edit a task. Title, prompt, and dependencies are individually optional."""
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if prompt is not None:
            body["prompt"] = prompt
        if depends_on is not None:
            body["depends_on"] = depends_on
        with _client() as c:
            return _ok(c.patch(f"/api/tasks/{task_id}", json=body))

    @mcp.tool()
    def split_task(
        task_id: str,
        new_tasks: list[dict[str, str]],
        inherit_deps_in: bool = True,
        link_in_series: bool = True,
    ) -> list[dict[str, Any]]:
        """Replace a pending/ready task with N new tasks (DAG surgery)."""
        body = {
            "new_tasks": new_tasks,
            "inherit_deps_in": inherit_deps_in,
            "link_in_series": link_in_series,
        }
        with _client() as c:
            return _ok(c.post(f"/api/tasks/{task_id}/split", json=body))

    @mcp.tool()
    def merge_tasks(task_ids: list[str], title: str, prompt: str) -> dict[str, Any]:
        """Collapse N pending tasks into one (DAG surgery)."""
        with _client() as c:
            return _ok(
                c.post(
                    "/api/tasks/merge",
                    json={"task_ids": task_ids, "title": title, "prompt": prompt},
                )
            )

    @mcp.tool()
    def run_task(task_id: str) -> dict[str, Any]:
        """Kick a ready task. Returns the resulting job."""
        with _client() as c:
            return _ok(c.post(f"/api/tasks/{task_id}/run"))

    @mcp.tool()
    def ack_plan(job_id: str, notes: str = "") -> dict[str, Any]:
        """Advance a job from awaiting_ack to executing.

        Equivalent to POSTing a followup on an ``awaiting_ack`` job; ``notes``
        is appended to the execute-turn prompt as guidance.
        """
        with _client() as c:
            return _ok(
                c.post(f"/api/jobs/{job_id}/followup", json={"prompt": notes})
            )

    @mcp.tool()
    def cancel_task(task_id: str) -> dict[str, Any]:
        """Cancel a task (stops its running job, if any)."""
        with _client() as c:
            return _ok(c.post(f"/api/tasks/{task_id}/cancel"))

    @mcp.tool()
    def list_outcomes(
        project_id: str | None = None,
        task_id: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """List outcomes by project or task; filter by kind (plan/execute/integrate)."""
        if task_id and project_id:
            raise ValueError("specify exactly one of task_id or project_id")
        if not task_id and not project_id:
            raise ValueError("specify task_id or project_id")
        with _client() as c:
            if task_id:
                rows = _ok(c.get(f"/api/tasks/{task_id}/outcomes"))
            else:
                rows = _ok(c.get(f"/api/projects/{project_id}/outcomes"))
        if kind:
            rows = [o for o in rows if o.get("kind") == kind]
        return rows

    @mcp.tool()
    def integrate(
        project_id: str,
        task_ids: list[str],
        target_branch: str | None = None,
    ) -> dict[str, Any]:
        """Create a synthetic integration task that will merge the given
        tasks' worktree branches into ``target_branch`` (defaults to the
        project's current HEAD branch). Returns the synthetic task; call
        ``run_task`` on its id to execute it.
        """
        body: dict[str, Any] = {"task_ids": task_ids}
        if target_branch is not None:
            body["target_branch"] = target_branch
        with _client() as c:
            return _ok(c.post(f"/api/projects/{project_id}/integrate", json=body))

    @mcp.tool()
    def list_worktrees(project_id: str) -> list[dict[str, Any]]:
        """List the project's outstanding git worktrees (raw porcelain entries)."""
        with _client() as c:
            return _ok(c.get(f"/api/projects/{project_id}/worktrees"))

    @mcp.tool()
    def set_autopilot(project_id: str, mode: str) -> dict[str, Any]:
        """Toggle a project's driver mode. ``mode`` is 'off' or 'on'.

        Turning on without a connected driver process returns 409 (the
        harness may auto-spawn one, but if that fails the call rejects).
        """
        if mode not in ("off", "on"):
            raise ValueError("mode must be 'off' or 'on'")
        with _client() as c:
            return _ok(c.patch(f"/api/projects/{project_id}/driver", json={"mode": mode}))

    @mcp.tool()
    def get_driver_state(project_id: str) -> dict[str, Any]:
        """Get per-project driver state: mode, connected driver, open notes."""
        with _client() as c:
            return _ok(c.get(f"/api/projects/{project_id}/driver"))

    @mcp.tool()
    def get_suggestions(project_id: str) -> list[dict[str, Any]]:
        """Driver's recommended next actions for this project (copilot view).

        Each suggestion includes ``rest_verb`` / ``rest_path`` / ``payload``
        so the caller can dispatch it directly via the REST surface.
        """
        with _client() as c:
            return _ok(c.get(f"/api/projects/{project_id}/driver/suggestions"))

    @mcp.tool()
    def list_driver_notes(
        project_id: str,
        severity: str | None = None,
        acknowledged: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List driver notes for a project; filter by severity / acked."""
        params: dict[str, Any] = {}
        if severity:
            params["severity"] = severity
        if acknowledged is not None:
            params["acknowledged"] = "true" if acknowledged else "false"
        with _client() as c:
            return _ok(c.get(f"/api/projects/{project_id}/driver/notes", params=params))

    @mcp.tool()
    def acknowledge_note(note_id: str) -> dict[str, Any]:
        """Dismiss a driver note."""
        with _client() as c:
            return _ok(c.post(f"/api/driver/notes/{note_id}/acknowledge"))

    @mcp.tool()
    def tail_job(job_id: str, max_lines: int = 200) -> list[dict[str, Any]]:
        """One-shot read of a job's recent stream events from the log files."""
        import json

        logs_dir = ah_home() / "logs" / "jobs" / job_id
        if not logs_dir.is_dir():
            return []
        events: list[dict[str, Any]] = []
        for f in sorted(logs_dir.glob("turn-*.jsonl")):
            try:
                with f.open("rb") as fh:
                    for raw in fh:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line:
                            continue
                        try:
                            events.append(json.loads(line))
                        except Exception:  # noqa: BLE001
                            continue
            except Exception:  # noqa: BLE001
                continue
        return events[-max_lines:]

    return mcp


def main() -> None:
    """Stdio entrypoint installed as ``agent-harness-mcp``."""
    mcp = build_mcp()
    mcp.run()


if __name__ == "__main__":
    main()
    sys.exit(0)
