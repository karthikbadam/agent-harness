"""Per-job pub/sub with replay-then-live semantics.

Each job has its own `Broadcaster`. It persists every event to a jsonl log
(`<logs>/jobs/<job-id>/turn-N.jsonl`, one event per line) before fanning it
out to live subscribers. On subscribe, replay events from disk first, then
attach to a live queue. Events carry a monotonic `seq` (1-based, per job,
across turns), which is the value to send as SSE `id:` and to honor on
`Last-Event-ID` reconnect.

Crash semantics: we write one event per line and `flush()`. On restart, the
new broadcaster recomputes `seq` from the line count of existing files.

This module is pure — no FastAPI, no DB. JobManager owns the broadcasters and
calls `start_turn()` / `publish()`. The SSE route calls `subscribe()`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

from pydantic import TypeAdapter

from .schemas import JobStatusEvent, StreamEvent

log = logging.getLogger(__name__)

_TURN_RE = re.compile(r"turn-(\d+)\.jsonl$")
_StreamAdapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)


class Broadcaster:
    def __init__(self, job_id: str, log_dir: Path) -> None:
        self.job_id = job_id
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self._seq: int = self._scan_max_seq()
        self._current_turn: int | None = None
        self._current_path: Path | None = None
        self._subs: set[asyncio.Queue[StreamEvent]] = set()
        self._lock = asyncio.Lock()

    @property
    def seq(self) -> int:
        return self._seq

    def _scan_max_seq(self) -> int:
        n = 0
        for p in sorted(self.log_dir.glob("turn-*.jsonl")):
            with p.open("rb") as fh:
                for _ in fh:
                    n += 1
        return n

    def turn_path(self, turn: int) -> Path:
        return self.log_dir / f"turn-{turn}.jsonl"

    def start_turn(self, turn: int) -> None:
        self._current_turn = turn
        self._current_path = self.turn_path(turn)
        # Ensure file exists so replay finds it even if we publish nothing.
        self._current_path.touch(exist_ok=True)

    async def publish(self, event: StreamEvent) -> None:
        async with self._lock:
            self._seq += 1
            ev = event.model_copy(update={"seq": self._seq})
            path = self._current_path or self.turn_path(ev.turn)
            with path.open("a") as fh:
                fh.write(ev.model_dump_json() + "\n")
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    log.warning("subscriber queue full on job %s; dropping", self.job_id)

    def _replay(self, since: int, up_to: int) -> Iterator[StreamEvent]:
        """Yield persisted events whose seq is in (since, up_to]."""
        for p in sorted(self.log_dir.glob("turn-*.jsonl"), key=_turn_idx):
            with p.open("rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        ev = _StreamAdapter.validate_json(line)
                    except Exception as e:  # noqa: BLE001
                        log.warning("bad event in %s: %s", p, e)
                        continue
                    if ev.seq <= since:
                        continue
                    if ev.seq > up_to:
                        return
                    yield ev

    @asynccontextmanager
    async def _attach(self) -> AsyncIterator[tuple[asyncio.Queue[StreamEvent], int]]:
        q: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subs.add(q)
            snapshot_seq = self._seq
        try:
            yield q, snapshot_seq
        finally:
            self._subs.discard(q)

    async def subscribe(self, last_event_id: int = 0) -> AsyncIterator[StreamEvent]:
        async with self._attach() as (q, snapshot_seq):
            cursor = last_event_id
            for ev in self._replay(since=cursor, up_to=snapshot_seq):
                yield ev
                cursor = ev.seq
            while True:
                ev = await q.get()
                if ev.seq <= cursor:
                    continue
                yield ev
                cursor = ev.seq


def _turn_idx(p: Path) -> int:
    m = _TURN_RE.search(p.name)
    return int(m.group(1)) if m else 0


# ------------------------------ Registry ----------------------------------- #


class BroadcasterRegistry:
    def __init__(self, logs_root: Path) -> None:
        self.logs_root = logs_root
        self._by_job: dict[str, Broadcaster] = {}

    def get(self, job_id: str) -> Broadcaster:
        b = self._by_job.get(job_id)
        if b is None:
            b = Broadcaster(job_id, self.logs_root / "jobs" / job_id)
            self._by_job[job_id] = b
        return b

    def known(self, job_id: str) -> bool:
        return job_id in self._by_job


def make_status_event(job_id: str, turn: int, status: str) -> JobStatusEvent:
    from datetime import datetime, timezone

    return JobStatusEvent(
        job_id=job_id,
        turn=turn,
        ts=datetime.now(timezone.utc),
        status=status,  # type: ignore[arg-type]
    )
