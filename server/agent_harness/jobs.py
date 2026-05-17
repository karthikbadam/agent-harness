"""JobManager: orchestrates ClaudeRunner instances, persists state, broadcasts.

Concurrency model:
- Global `asyncio.Semaphore(max_concurrent_jobs)` bounds how many jobs run at
  once.
- Per-job `asyncio.Lock()` serializes turns on the same job. Followups wait for
  the current turn to finish.

DB writes use a sync `session_scope()`; in this single-user app, SQLite + WAL
is comfortable with that pattern from async code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import select

from .broadcaster import BroadcasterRegistry, make_status_event
from .claude import ClaudeRunner
from .config import get_settings
from .db import session_scope
from . import models
from .schemas import StreamEvent, TurnDoneEvent

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_PLANNING_PREFIX = (
    "You are in the PLANNING phase. Do not modify files. Read what you need, "
    "then produce a numbered plan of the changes you will make as your final "
    "assistant message. A separate execute turn will run after a human "
    "acknowledges this plan."
)

_EXECUTE_PREFIX_TEMPLATE = (
    "You are in the EXECUTE phase. The previously approved plan was:\n\n"
    "{plan}\n\n"
    "Implement it now in this working directory. Commit your changes when "
    "complete."
)


def _last_plan_summary(s, task_id: str | None) -> str | None:
    if not task_id:
        return None
    row = s.execute(
        select(models.Outcome.summary)
        .where(models.Outcome.task_id == task_id, models.Outcome.kind == "plan")
        .order_by(models.Outcome.created_at.desc())
    ).first()
    return row[0] if row is not None else None


def _augment_prompt_for_phase(s, job: "models.Job", prompt: str) -> str:
    """Prepend a phase-specific instruction to the user's prompt.

    Planning turns get a read-only contract. Execute turns get the prior plan
    summary so the agent has continuity even if the conversation was resumed.
    All other phases (NULL, integrating, awaiting_ack-as-followup) pass through
    unchanged.
    """
    if job.phase == "planning":
        return f"{_PLANNING_PREFIX}\n\n{prompt}"
    if job.phase == "executing":
        plan = _last_plan_summary(s, job.task_id)
        return _EXECUTE_PREFIX_TEMPLATE.format(plan=plan or "(plan not recorded)") + "\n\n" + prompt
    return prompt


def _gather_allowlist(project_id: str) -> list[str]:
    """Return the merged allowlist for a job: global + project rules + Skill().

    Project `skills` are auto-allowed (`Skill(<name>)`) without the user
    having to add them as explicit rules.
    """
    with session_scope() as s:
        rule_rows = s.execute(
            select(models.AllowlistRule.rule).where(
                (models.AllowlistRule.project_id.is_(None))
                | (models.AllowlistRule.project_id == project_id)
            )
        ).all()
        proj = s.get(models.Project, project_id)
        skills = list(proj.skills or []) if proj is not None else []
    rules = [r[0] for r in rule_rows]
    skill_rules = [f"Skill({name})" for name in skills if isinstance(name, str) and name]
    # Dedupe but preserve order: rules first, then skill rules.
    seen: set[str] = set()
    out: list[str] = []
    for r in rules + skill_rules:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


class JobManager:
    def __init__(
        self,
        broadcasters: BroadcasterRegistry,
        max_concurrent: int = 2,
        claude_path: str | None = None,
        default_extra_args: list[str] | None = None,
        default_idle_timeout_seconds: int = 600,
    ) -> None:
        self.broadcasters = broadcasters
        self.sem = asyncio.Semaphore(max_concurrent)
        self.claude_path = claude_path
        self.default_extra_args = list(default_extra_args or [])
        self.default_idle_timeout_seconds = default_idle_timeout_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._runners: dict[str, ClaudeRunner] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ---------------------------- public api ------------------------------- #

    def create_job(
        self,
        project_id: str,
        prompt: str,
        title: str = "",
        schedule_id: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Create Job + first Turn rows. Return job_id. Does not start running.

        When the job is bound to a plan-then-execute task, the initial phase is
        ``planning``; one-shot and ad-hoc jobs leave ``phase`` NULL (v1
        semantics — finalize-by-status, no ack gate).
        """
        with session_scope() as s:
            proj = s.get(models.Project, project_id)
            if proj is None:
                raise ValueError(f"unknown project {project_id}")
            phase: str | None = None
            if task_id is not None:
                task = s.get(models.Task, task_id)
                if task is not None:
                    if task.synthetic:
                        phase = "integrating"
                    elif task.mode == "plan_then_execute":
                        phase = "planning"
            job = models.Job(
                project_id=project_id,
                title=title or prompt[:80],
                status="queued",
                schedule_id=schedule_id,
                task_id=task_id,
                phase=phase,
            )
            s.add(job)
            s.flush()
            turn = models.Turn(job_id=job.id, idx=0, prompt=prompt, status="queued")
            s.add(turn)
            s.flush()
            return job.id

    async def start(self, job_id: str) -> asyncio.Task[None]:
        """Spawn the first turn. Returns the background task."""
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            return task
        coro = self._run_turn(job_id, turn_idx=0)
        task = asyncio.create_task(coro, name=f"job-{job_id}-turn-0")
        self._tasks[job_id] = task
        return task

    async def followup(self, job_id: str, prompt: str) -> int:
        """Create a new Turn, kick off run. Returns the new turn's idx."""
        with session_scope() as s:
            job = s.get(models.Job, job_id)
            if job is None:
                raise ValueError(f"unknown job {job_id}")
            idx = (
                s.execute(
                    select(models.Turn.idx)
                    .where(models.Turn.job_id == job_id)
                    .order_by(models.Turn.idx.desc())
                ).first()
            )
            next_idx = (idx[0] + 1) if idx else 0
            turn = models.Turn(job_id=job_id, idx=next_idx, prompt=prompt, status="queued")
            s.add(turn)
            s.flush()
        coro = self._run_turn(job_id, turn_idx=next_idx)
        task = asyncio.create_task(coro, name=f"job-{job_id}-turn-{next_idx}")
        self._tasks[job_id] = task
        return next_idx

    async def stop(self, job_id: str) -> bool:
        """Signal the running turn to stop. Returns True if a runner was alive."""
        runner = self._runners.get(job_id)
        if runner is None:
            return False
        await runner.stop()
        return True

    def runner_for(self, job_id: str) -> ClaudeRunner | None:
        return self._runners.get(job_id)

    async def wait(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            await task

    # ---------------------------- internals -------------------------------- #

    def _lock(self, job_id: str) -> asyncio.Lock:
        lk = self._locks.get(job_id)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[job_id] = lk
        return lk

    async def _run_turn(self, job_id: str, turn_idx: int) -> None:
        async with self._lock(job_id):
            async with self.sem:
                await self._run_turn_inner(job_id, turn_idx)

    async def _run_turn_inner(self, job_id: str, turn_idx: int) -> None:
        broadcaster = self.broadcasters.get(job_id)
        # Resolve everything we need from the DB before we leave the sync block.
        with session_scope() as s:
            job = s.get(models.Job, job_id)
            if job is None:
                log.error("job %s vanished before run", job_id)
                return
            turn = s.execute(
                select(models.Turn).where(
                    models.Turn.job_id == job_id, models.Turn.idx == turn_idx
                )
            ).scalar_one()
            project = s.get(models.Project, job.project_id)
            assert project is not None
            cwd = job.cwd_override or project.path
            prompt = _augment_prompt_for_phase(s, job, turn.prompt)
            permission_mode = project.permission_mode
            dangerously_skip = project.dangerously_skip
            resume_session_id = job.session_id  # set after first turn
            project_id = project.id
            project_extra = list(project.extra_claude_args or [])
            idle_timeout = (
                project.idle_timeout_seconds
                if project.idle_timeout_seconds is not None
                else self.default_idle_timeout_seconds
            )

        allowed = _gather_allowlist(project_id)
        broadcaster.start_turn(turn_idx)
        await broadcaster.publish(make_status_event(job_id, turn_idx, "running"))

        runner = ClaudeRunner(
            job_id=job_id,
            turn=turn_idx,
            prompt=prompt,
            cwd=cwd,
            resume_session_id=resume_session_id,
            permission_mode=permission_mode if not dangerously_skip else None,
            allowed_tools=allowed if not dangerously_skip else [],
            dangerously_skip=dangerously_skip,
            extra_args=self.default_extra_args + project_extra,
            claude_path=self.claude_path,
        )
        self._runners[job_id] = runner
        start_ts = utcnow()
        self._mark_turn_running(job_id, turn_idx, pid=None, started_at=start_ts)
        # Stream → broadcaster (with idle watchdog)
        last_event: Optional[StreamEvent] = None
        last_event_at = time.monotonic()
        timed_out = False

        async def watchdog() -> None:
            nonlocal timed_out
            if idle_timeout <= 0:
                return
            check_every = min(5.0, max(0.1, idle_timeout / 4))
            while True:
                await asyncio.sleep(check_every)
                if runner.returncode is not None:
                    return
                idle = time.monotonic() - last_event_at
                if idle >= idle_timeout:
                    log.warning(
                        "idle watchdog: %ds since last event for %s/turn-%d; stopping",
                        int(idle),
                        job_id,
                        turn_idx,
                    )
                    timed_out = True
                    await runner.stop()
                    return

        watch_task = asyncio.create_task(watchdog(), name=f"watchdog-{job_id}-{turn_idx}")
        try:
            async for ev in runner.run():
                if runner.pid and last_event is None:
                    self._mark_turn_running(job_id, turn_idx, pid=runner.pid, started_at=start_ts)
                await broadcaster.publish(ev)
                last_event = ev
                last_event_at = time.monotonic()
        except Exception as e:  # noqa: BLE001
            log.exception("runner failed for %s/turn-%d: %s", job_id, turn_idx, e)
        finally:
            watch_task.cancel()
            try:
                await watch_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._runners.pop(job_id, None)

        await self._finalize_turn(job_id, turn_idx, runner, last_event, timed_out=timed_out)

    def _mark_turn_running(
        self, job_id: str, turn_idx: int, pid: int | None, started_at: datetime
    ) -> None:
        with session_scope() as s:
            turn = s.execute(
                select(models.Turn).where(
                    models.Turn.job_id == job_id, models.Turn.idx == turn_idx
                )
            ).scalar_one()
            turn.status = "running"
            turn.started_at = turn.started_at or started_at
            if pid is not None:
                turn.pid = pid
            job = s.get(models.Job, job_id)
            assert job is not None
            if job.status != "running":
                job.status = "running"

    async def _finalize_turn(
        self,
        job_id: str,
        turn_idx: int,
        runner: ClaudeRunner,
        last_event: StreamEvent | None,
        timed_out: bool = False,
    ) -> None:
        broadcaster = self.broadcasters.get(job_id)
        exit_code = runner.returncode if runner.returncode is not None else 1
        cost = None
        duration = None
        if isinstance(last_event, TurnDoneEvent):
            exit_code = last_event.exit_code
            cost = last_event.cost_usd
            duration = last_event.duration_ms

        if timed_out or runner.stop_requested:
            status = "stopped"
        elif exit_code == 0:
            status = "done"
        else:
            status = "failed"

        # Persist DB state.
        with session_scope() as s:
            turn = s.execute(
                select(models.Turn).where(
                    models.Turn.job_id == job_id, models.Turn.idx == turn_idx
                )
            ).scalar_one()
            turn.status = status
            turn.exit_code = exit_code
            turn.cost_usd = cost
            turn.ended_at = utcnow()
            turn.log_path = str(broadcaster.turn_path(turn_idx))
            job = s.get(models.Job, job_id)
            assert job is not None
            if runner.session_id and not job.session_id:
                job.session_id = runner.session_id
            job.status = status
            # Phase transition: a clean planning turn parks the job at
            # awaiting_ack instead of advancing to executing. The execute turn
            # itself is enqueued only when the next followup arrives.
            if job.phase == "planning" and status == "done":
                job.phase = "awaiting_ack"
            elif job.phase in ("executing", "integrating") and status != "running":
                job.phase = "done"
            if status != "running":
                job.ended_at = utcnow()

        # Emit final job_status. UI listens for this to invalidate lists.
        await broadcaster.publish(make_status_event(job_id, turn_idx, status))
        _ = duration  # currently surfaced via turn_done.duration_ms only
        _ = cost

        # Record an outcome + propagate task status if this job belongs to a task.
        try:
            from .services import task_runner

            task_runner.on_job_finalized(
                job_id, status, log_dir=broadcaster.log_dir
            )
        except Exception:  # noqa: BLE001
            log.exception("task_runner.on_job_finalized failed for %s", job_id)
