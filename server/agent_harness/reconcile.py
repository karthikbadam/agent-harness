"""Startup reconciliation for jobs that were running when we died.

On process restart we can't reattach to a child's stdout — that pipe died with
the parent. So policy for v1: if the orphan process is still alive, send
SIGTERM (then SIGKILL after 5s) and mark the turn `stopped`. If it's already
dead, mark `stopped` directly. Either way the job's status is set to
`stopped` and `ended_at` is recorded.

We also emit a synthetic `job_status` event into the jsonl log so that anyone
who opens the job in the UI sees the terminal state.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone

from sqlalchemy import select

from .broadcaster import BroadcasterRegistry, make_status_event
from .db import session_scope
from . import models

log = logging.getLogger(__name__)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _kill(pid: int, term_wait: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + term_wait
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


async def reconcile_jobs(
    broadcasters: BroadcasterRegistry, job_manager: object | None = None
) -> list[str]:
    """Idempotent: scan DB for running jobs, kill orphans, mark stopped, emit
    a synthetic job_status event.

    For task-bound orphans we also run the normal finalize path
    (``task_runner.on_job_finalized`` with status ``stopped``) so the owning
    Task's status propagates instead of being stranded in ``running``. This is
    what lets a **loop survive a restart**: the interrupted iteration finalizes,
    ``advance_loop`` runs, and the next iteration is spawned. When a
    ``job_manager`` is provided we kick those follow-on tasks so the loop
    actually resumes (otherwise it would just sit ``ready``).

    Returns the list of job ids reconciled.
    """
    with session_scope() as s:
        running_jobs = (
            s.execute(select(models.Job).where(models.Job.status == "running")).scalars().all()
        )
        job_ids = [j.id for j in running_jobs]

    reconciled: list[str] = []
    autorun_ids: list[str] = []
    for job_id in job_ids:
        try:
            turn_idx = _stop_orphan(job_id)
            b = broadcasters.get(job_id)
            b.start_turn(turn_idx)
            await b.publish(make_status_event(job_id, turn_idx, "stopped"))
            reconciled.append(job_id)
            with session_scope() as s:
                jb = s.get(models.Job, job_id)
                is_task_bound = jb is not None and jb.task_id is not None
            if is_task_bound:
                from .services import task_runner

                try:
                    ids = task_runner.on_job_finalized(job_id, "stopped", log_dir=b.log_dir)
                    autorun_ids.extend(ids or [])
                except Exception:  # noqa: BLE001
                    log.exception("finalize-on-reconcile failed for %s", job_id)
        except Exception as e:  # noqa: BLE001
            log.exception("reconcile failed for %s: %s", job_id, e)

    # Resume follow-on work (e.g. a loop's next iteration) now that the manager
    # exists. Without a manager (some tests) the tasks are left ``ready``.
    if job_manager is not None and autorun_ids:
        from .services import task_runner

        for tid in autorun_ids:
            try:
                await task_runner.kickoff_first_phase(tid, job_manager)
            except Exception:  # noqa: BLE001
                log.exception("reconcile autorun kickoff failed for %s", tid)
    return reconciled


def _stop_orphan(job_id: str) -> int:
    """Kill orphan if alive, mark stopped in DB, return latest turn idx."""
    with session_scope() as s:
        turn = (
            s.execute(
                select(models.Turn)
                .where(models.Turn.job_id == job_id)
                .order_by(models.Turn.idx.desc())
            )
            .scalars()
            .first()
        )
        pid = turn.pid if turn else None
        turn_idx = turn.idx if turn else 0

    if pid and _alive(pid):
        log.warning("reconcile: pid %d still alive for job %s; killing", pid, job_id)
        _kill(pid)

    now = datetime.now(timezone.utc)
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        if job is not None:
            job.status = "stopped"
            job.ended_at = job.ended_at or now
        turn = (
            s.execute(
                select(models.Turn)
                .where(models.Turn.job_id == job_id)
                .order_by(models.Turn.idx.desc())
            )
            .scalars()
            .first()
        )
        if turn is not None and turn.status in {"queued", "running"}:
            turn.status = "stopped"
            turn.ended_at = turn.ended_at or now
            turn.exit_code = -1
    return turn_idx
