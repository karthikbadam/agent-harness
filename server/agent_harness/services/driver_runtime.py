"""``agent-harness-driver``: the external process that drives autopilot.

Listens on the harness's ``/api/driver/events`` SSE stream. On every event,
asks the harness "what should I do for this project right now" via
``GET /driver/suggestions`` and dispatches each returned action over REST,
posting DriverNotes afterwards.

Stateless across restarts — all state lives in the harness DB. Reconnect
triggers a server-side ``reconcile_now`` per mode=on project, so missed
events during a disconnect are caught up automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any

import httpx

from ..config import load_toml

log = logging.getLogger("agent-harness-driver")


_NOTE_KIND = {
    "ack": "acked",
    "retry": "retried",
    "integrate": "integrated",
    "run": "ran",
}


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


class DriverRuntime:
    """The driver process's main loop, factored out for testability."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1.0
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self.headers, timeout=timeout
        ) as client:
            while not self._stop.is_set():
                try:
                    await self._consume(client)
                    backoff = 1.0
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 409:
                        log.error("another driver is already connected; backing off")
                    else:
                        log.error("event stream %d: %s", e.response.status_code, e)
                except (httpx.RemoteProtocolError, httpx.ConnectError) as e:
                    log.warning("event stream disconnected: %s", e)
                except Exception as e:  # noqa: BLE001
                    log.exception("driver loop error: %s", e)
                if self._stop.is_set():
                    return
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _consume(self, client: httpx.AsyncClient) -> None:
        async with client.stream("GET", "/api/driver/events") as resp:
            resp.raise_for_status()
            log.info("connected to driver event stream at %s", self.base_url)
            event_type: str | None = None
            async for line in resp.aiter_lines():
                if self._stop.is_set():
                    return
                if line == "":
                    event_type = None
                    continue
                if line.startswith(":"):
                    continue  # SSE comment / heartbeat
                if line.startswith("event: "):
                    event_type = line[len("event: ") :].strip()
                    continue
                if line.startswith("data: "):
                    raw = line[len("data: ") :]
                    try:
                        evt = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    await self._handle(client, event_type or evt.get("event", ""), evt)

    async def _handle(
        self, client: httpx.AsyncClient, event_type: str, evt: dict[str, Any]
    ) -> None:
        if event_type == "mode_off":
            # No in-memory state to drop in v1; reconcile catches the rest.
            return
        project_id = evt.get("project_id")
        if not project_id:
            return
        await self._react(client, project_id)

    async def _react(self, client: httpx.AsyncClient, project_id: str) -> None:
        try:
            r = await client.get(f"/api/projects/{project_id}/driver/suggestions")
            r.raise_for_status()
            actions = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("fetching suggestions for %s failed: %s", project_id, e)
            return
        for a in actions:
            if self._stop.is_set():
                return
            await self._dispatch(client, a)

    async def _dispatch(self, client: httpx.AsyncClient, a: dict[str, Any]) -> None:
        verb = (a.get("rest_verb") or "POST").upper()
        path = a.get("rest_path", "")
        try:
            r = await client.request(verb, path, json=a.get("payload"))
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # 409 on run/ack is a benign race — the human or another event
            # already advanced state. Log info, no escalate note.
            if e.response.status_code == 409:
                log.info("%s %s 409 (raced): %s", verb, path, e.response.text[:120])
                return
            log.warning("%s %s failed: %s", verb, path, e)
            await self._post_note(
                client,
                project_id=a.get("project_id"),
                severity="warn",
                kind="stuck",
                message=f"{a.get('kind')} failed: {e.response.status_code} {e.response.text[:200]}",
                task_id=a.get("task_id"),
                job_id=a.get("job_id"),
            )
            return
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s exception: %s", verb, path, e)
            await self._post_note(
                client,
                project_id=a.get("project_id"),
                severity="warn",
                kind="stuck",
                message=f"{a.get('kind')} exception: {e}",
                task_id=a.get("task_id"),
                job_id=a.get("job_id"),
            )
            return

        await self._post_note(
            client,
            project_id=a.get("project_id"),
            severity="info",
            kind=_NOTE_KIND.get(a.get("kind", ""), a.get("kind", "info")),
            message=a.get("reason", ""),
            task_id=a.get("task_id"),
            job_id=a.get("job_id"),
        )

    async def _post_note(
        self,
        client: httpx.AsyncClient,
        *,
        project_id: str | None,
        severity: str,
        kind: str,
        message: str,
        task_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        if not project_id:
            return
        try:
            await client.post(
                "/api/driver/notes",
                json={
                    "project_id": project_id,
                    "severity": severity,
                    "kind": kind,
                    "message": message,
                    "task_id": task_id,
                    "job_id": job_id,
                },
            )
        except Exception as e:  # noqa: BLE001
            log.warning("post note failed: %s", e)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("AGENT_HARNESS_DRIVER_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = _auth_token()
    if not token:
        log.error(
            "no auth token; set AGENT_HARNESS_TOKEN or configure ~/.agent-harness/config.toml"
        )
        return 1
    runtime = DriverRuntime(base_url=_base_url(), token=token)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _on_signal(*_: Any) -> None:
        loop.create_task(runtime.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(runtime.run())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
