"""Task runner: record outcomes and propagate task status when jobs finalize.

This is a small *status reconciler*, not an executor. It does not auto-enqueue
new jobs — the user must call `POST /api/tasks/{id}/run` for each ready task.

Responsibilities:
- `on_job_finalized(job_id, status)` is called by `JobManager._finalize_turn`
  for jobs that have a `task_id` set. It captures the project's git HEAD,
  records an `Outcome` row, flips the task status to `done`/`failed`, and
  re-evaluates downstream tasks (`pending` → `ready` when deps are done).
- `reconcile_on_startup()` flips orphaned task statuses (e.g. a `running`
  task whose job ended via the reconciler) and ensures pending/ready states
  reflect current dep statuses.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from .. import models
from ..db import session_scope
from . import driver_bus, worktrees

log = logging.getLogger(__name__)

# A plan auto-runs only if it's simple: no loop nodes and fewer than this many
# tasks. Anything heavier parks as a reviewable draft (gate + steer + confirm).
PLAN_AUTORUN_MAX = 10


def _git_head(project_path: str) -> tuple[str | None, str | None]:
    """Return (commit_sha, branch) for the project's working dir, or (None, None)."""
    pdir = Path(project_path)
    if not pdir.is_dir() or not (pdir / ".git").exists():
        return None, None
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(pdir), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        sha = None
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(pdir), "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        branch = None
    return sha, branch


def _branch_tip(project_path: str, branch: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", project_path, "rev-parse", branch],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _commit_reachable_from_other_branch(
    project_path: str, sha: str, exclude_branch: str
) -> bool:
    """Return True iff ``sha`` is reachable from some local branch other than
    ``exclude_branch``. Used to verify an integration actually moved each input
    task's work somewhere persistent before we delete its task branch."""
    try:
        names = subprocess.check_output(
            [
                "git",
                "-C",
                project_path,
                "branch",
                "--contains",
                sha,
                "--format=%(refname:short)",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().splitlines()
    except Exception:  # noqa: BLE001
        return False
    return any(n.strip() and n.strip() != exclude_branch for n in names)


def _integration_actually_landed(project_path: str, input_tasks) -> bool:
    """Defensive check at integration finalize: for every input task whose
    worktree branch carries commits past the base, those commits must now be
    reachable from another local branch (the merge target). If any task's tip
    is only reachable via its own task/<id> branch, the integration didn't
    actually merge — the agent gave up or hit a permission wall. We refuse to
    declare success and wipe the input branches in that case."""
    if not project_path:
        return False
    for dep in input_tasks:
        branch = dep.worktree_branch
        if not branch:
            continue
        tip = _branch_tip(project_path, branch)
        if tip is None:
            continue
        if not _commit_reachable_from_other_branch(project_path, tip, branch):
            return False
    return True


def _commit_dirty_worktree(cwd: str, message: str) -> bool:
    """If ``cwd`` is a git worktree with uncommitted changes, stage and commit
    them with ``message``. Returns True if a commit was created. Used as a
    backstop after the execute turn so the worktree branch always carries the
    agent's work — even if the agent forgot to commit or got stuck on
    permission gates.
    """
    pdir = Path(cwd)
    if not pdir.is_dir() or not (pdir / ".git").exists():
        return False
    try:
        status = subprocess.check_output(
            ["git", "-C", str(pdir), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
    except Exception:  # noqa: BLE001
        return False
    if not status.strip():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(pdir), "add", "-A"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        subprocess.run(
            ["git", "-C", str(pdir), "commit", "-m", message],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("auto-commit failed in %s: %s", cwd, e)
        return False
    log.info("auto-committed dirty worktree at %s", cwd)
    return True


def _last_assistant_text(log_dir: Path) -> str | None:
    """Scan turn-*.jsonl for the most recent assistant_text payload."""
    import json

    if not log_dir.is_dir():
        return None
    files = sorted(log_dir.glob("turn-*.jsonl"))
    last: str | None = None
    for f in files:
        try:
            with f.open("rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or '"assistant_text"' not in line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if payload.get("type") != "assistant_text":
                        continue
                    text = payload.get("text")
                    if isinstance(text, str) and text.strip():
                        last = text
        except Exception:  # noqa: BLE001
            continue
    return last


def _deps_of(s, task_id: str) -> list[str]:
    rows = s.execute(
        select(models.TaskDependency.depends_on_id).where(
            models.TaskDependency.task_id == task_id
        )
    ).all()
    return [r[0] for r in rows]


def _all_deps_done(s, task_id: str) -> bool:
    dep_ids = _deps_of(s, task_id)
    if not dep_ids:
        return True
    rows = s.execute(
        select(models.Task.status).where(models.Task.id.in_(dep_ids))
    ).all()
    statuses = [r[0] for r in rows]
    if len(statuses) != len(dep_ids):
        return False
    return all(st == "done" for st in statuses)


def _reevaluate_downstream(s, task_id: str) -> list[tuple[str, str]]:
    """Flip eligible downstream tasks to 'ready'. Returns (task_id, project_id)
    pairs for tasks that transitioned, so the caller can emit driver events.
    """
    transitions: list[tuple[str, str]] = []
    rows = s.execute(
        select(models.TaskDependency.task_id).where(
            models.TaskDependency.depends_on_id == task_id
        )
    ).all()
    for (downstream_id,) in rows:
        ds = s.get(models.Task, downstream_id)
        if ds is None:
            continue
        if ds.status == "pending" and _all_deps_done(s, downstream_id):
            ds.status = "ready"
            transitions.append((ds.id, ds.project_id))
    return transitions


def on_job_finalized(
    job_id: str,
    job_status: str,
    log_dir: Path | None = None,
) -> list[str]:
    """Record an Outcome and advance the owning Task's phase.

    Returns the list of task IDs that just transitioned to ``ready`` and are
    eligible for auto-kickoff (planner-sourced, non-synthetic). The caller is
    responsible for actually awaiting :func:`kickoff_first_phase` on each.

    No-op if the job has no ``task_id``. The Job's ``kind`` selects the path:

    - ``kind='plan'``: on clean exit, park the Task at ``phase='awaiting_ack'``
      with an ``Outcome(kind='plan')``; on failure flip the Task to ``failed``.
      Downstream tasks are NOT re-evaluated (execute hasn't happened yet).
    - ``kind='execute'``: backstop-commit any leftover dirty state in the
      worktree, record ``Outcome(kind='execute')``, flip the Task to
      ``done``/``failed`` and propagate readiness to dependents. For
      plan_then_execute Tasks the worktree carries the work for integration.
    - ``kind='integrate'`` (synthetic Task): verify the input branches'
      tips are reachable from another branch; on success cleanup input
      worktrees and mark them ``integrated``; on failure leave them and
      mark ``conflict``.
    """
    bus = driver_bus.get_bus()
    pending_events: list[tuple[str, dict]] = []  # collected, emitted after commit
    autorun_ids: list[str] = []
    with session_scope() as s:
        job = s.get(models.Job, job_id)
        if job is None or job.task_id is None:
            return autorun_ids
        task = s.get(models.Task, job.task_id)
        if task is None:
            log.warning("task %s missing for job %s; outcome skipped", job.task_id, job_id)
            return autorun_ids
        project = s.get(models.Project, job.project_id)
        project_id = job.project_id

        if job.kind == "plan":
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
            # Top-level planner task: decompose the ask into child tasks and
            # mark itself done. Distinct from the per-task planning phase
            # (mode='plan_then_execute') which parks at awaiting_ack.
            if task.mode == "plan":
                # The planner job decomposed the ask into a task GRAPH. Three
                # outcomes: failed; first plan (insert + gate auto-run vs draft);
                # or a steering re-plan (a followup turn while parked at
                # awaiting_ack → replace the draft list, stay parked).
                from . import planner as _planner

                if job_status != "done":
                    task.status = "failed"
                    task.phase = "failed"
                    task.last_failed_at = datetime.now(timezone.utc)
                    s.add(models.Outcome(
                        task_id=task.id, job_id=job.id, commit_sha=None, branch=None,
                        summary=summary, status="failed", kind="plan",
                    ))
                    pending_events.append((
                        "task_failed",
                        {"project_id": project_id, "task_id": task.id, "job_id": job.id},
                    ))
                    _emit_after(bus, pending_events)
                    return autorun_ids

                # A followup on an already-finalized (auto-ran) plan is a no-op.
                if task.status == "done":
                    s.add(models.Outcome(
                        task_id=task.id, job_id=job.id, commit_sha=None, branch=None,
                        summary=summary, status="success", kind="plan",
                    ))
                    _emit_after(bus, pending_events)
                    return autorun_ids

                is_steering = task.phase == "awaiting_ack"
                child_ids: list[str] = []
                try:
                    if is_steering:
                        child_ids = _planner.replace_drafts_from_log_dir(
                            project_id, task.id, log_dir
                        )
                    elif log_dir is not None:
                        child_ids = _planner.parse_and_insert_from_log_dir(
                            project_id, task.id, log_dir, task.prompt
                        )
                except Exception:  # noqa: BLE001
                    log.exception("planner insert/replace failed for task %s", task.id)

                # Gate: auto-run only simple plans (no loops, < PLAN_AUTORUN_MAX
                # tasks). A steering re-plan always re-parks for review.
                has_loop = False
                n = 0
                for cid in child_ids:
                    ds = s.get(models.Task, cid)
                    if ds is None:
                        continue
                    n += 1
                    if ds.mode == "loop":
                        has_loop = True
                auto_run = (not has_loop) and (n < PLAN_AUTORUN_MAX)

                if not is_steering and auto_run:
                    task.status = "done"
                    task.phase = "done"
                    event = "task_done"
                    for cid in child_ids:
                        ds = s.get(models.Task, cid)
                        if ds is not None and ds.status == "ready" and ds.source == "planner":
                            autorun_ids.append(cid)
                else:
                    # Park as a reviewable draft: the plan stays running at
                    # awaiting_ack. Hold ALL drafts uniformly at 'pending'
                    # (override the no-dep→ready promotion in _insert_drafts) so
                    # steer-replace and confirm operate on a uniform set.
                    task.status = "running"
                    task.phase = "awaiting_ack"
                    event = "plan_ready"
                    for cid in child_ids:
                        ds = s.get(models.Task, cid)
                        if ds is not None and ds.source == "planner":
                            ds.status = "pending"

                verb = "Revised" if is_steering else "Drafted"
                plan_summary = (
                    f"{verb} {n} task{'s' if n != 1 else ''}." if n else summary
                )
                s.add(models.Outcome(
                    task_id=task.id, job_id=job.id, commit_sha=None, branch=None,
                    summary=plan_summary, status="success", kind="plan",
                ))
                s.flush()
                pending_events.append((
                    event,
                    {"project_id": project_id, "task_id": task.id, "job_id": job.id},
                ))
                _emit_after(bus, pending_events)
                return autorun_ids
            # Per-task plan phase (mode='plan_then_execute'): park at
            # awaiting_ack so the user/driver can ack into the execute phase.
            if job_status == "done":
                task.phase = "awaiting_ack"
                s.add(
                    models.Outcome(
                        task_id=task.id,
                        job_id=job.id,
                        commit_sha=None,
                        branch=None,
                        summary=summary,
                        status="success",
                        kind="plan",
                    )
                )
                pending_events.append(
                    (
                        "plan_ready",
                        {"project_id": project_id, "task_id": task.id, "job_id": job.id},
                    )
                )
            else:
                task.phase = "failed"
                task.status = "failed"
                task.last_failed_at = datetime.now(timezone.utc)
                s.add(
                    models.Outcome(
                        task_id=task.id,
                        job_id=job.id,
                        commit_sha=None,
                        branch=None,
                        summary=summary,
                        status="failed",
                        kind="plan",
                    )
                )
                pending_events.append(
                    (
                        "task_failed",
                        {"project_id": project_id, "task_id": task.id, "job_id": job.id},
                    )
                )
            _emit_after(bus, pending_events)
            return autorun_ids

        if job.kind == "integrate":
            cwd = job.cwd or (project.path if project else None)
            sha, branch = (_git_head(cwd) if cwd else (None, None))
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
            dep_rows = s.execute(
                select(models.TaskDependency.depends_on_id).where(
                    models.TaskDependency.task_id == task.id
                )
            ).all()
            input_tasks = [s.get(models.Task, dep_id) for (dep_id,) in dep_rows]
            input_tasks = [t for t in input_tasks if t is not None]
            merge_landed = job_status == "done" and _integration_actually_landed(
                cwd or "", input_tasks
            )
            outcome_status = "success" if merge_landed else "failed"
            if job_status == "done" and not merge_landed:
                log.warning(
                    "integration job %s exited clean but no input branch was merged; "
                    "marking outcome failed and preserving task branches",
                    job.id,
                )
            s.add(
                models.Outcome(
                    task_id=task.id,
                    job_id=job.id,
                    commit_sha=sha,
                    branch=branch,
                    summary=summary,
                    status=outcome_status,
                    kind="integrate",
                )
            )
            task.phase = "done" if merge_landed else "failed"
            task.status = "done" if merge_landed else "failed"
            if not merge_landed:
                task.last_failed_at = datetime.now(timezone.utc)
            for dep in input_tasks:
                if merge_landed:
                    if project is not None and (dep.worktree_path or dep.worktree_branch):
                        worktrees.remove(project, dep)
                        dep.worktree_path = None
                        dep.worktree_branch = None
                    dep.integration_status = "integrated"
                else:
                    dep.integration_status = "conflict"
            s.flush()
            transitions = _reevaluate_downstream(s, task.id)
            event = "integration_done" if merge_landed else "integration_conflict"
            pending_events.append(
                (event, {"project_id": project_id, "task_id": task.id, "job_id": job.id})
            )
            for tid, pid in transitions:
                pending_events.append(("task_ready", {"project_id": pid, "task_id": tid}))
                ds = s.get(models.Task, tid)
                if ds is not None and ds.source == "planner" and not ds.synthetic:
                    autorun_ids.append(tid)
            # Fall through to the common post-commit block. We used to call
            # try_autodisable_autopilot here, inside the still-uncommitted
            # session — its inner session_scope opened a fresh DB read and
            # never saw the integrate task as done, so it always returned
            # False at the moment the wave actually completed.
        else:
            # Execute / one-shot / ad-hoc (kind='execute' or 'ad_hoc'). The
            # job's cwd is the worktree path for plan-then-execute tasks;
            # project.path for one-shot tasks; whatever the caller
            # specified for ad-hoc.
            cwd = job.cwd or (project.path if project else None)
            summary = _last_assistant_text(log_dir) if log_dir is not None else None
            in_worktree = bool(
                cwd and project and cwd != project.path
            )
            if job_status == "done" and in_worktree and not task.synthetic:
                commit_msg = (task.title or "agent commit")[:72]
                _commit_dirty_worktree(cwd, commit_msg)
            sha, branch = (_git_head(cwd) if cwd else (None, None))
            outcome_status = "success" if job_status == "done" else "failed"

            # Loop iteration: hand the finished iteration to the planner's
            # iterate strategy, which parses the result, updates the parent's
            # loop_state, and decides whether to spawn the next iteration or
            # stop. Done BEFORE we add this iteration's Outcome so the separate
            # session writes don't contend (same ordering the decompose path
            # uses). The returned result is stored on the Outcome's meta.
            loop_result: dict = {}
            loop_next_child: str | None = None
            loop_stop_reason: str | None = None
            if task.source == "loop" and task.parent_task_id:
                from . import planner as _planner
                cost = _job_cost_usd(s, job.id)
                try:
                    loop_result, loop_next_child, loop_stop_reason = (
                        _planner.advance_loop(
                            task.parent_task_id, job_status, cwd or "", cost, log_dir
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.exception("advance_loop failed for iteration %s", task.id)
                # Retitle the finished iteration with what it tried, so the task
                # list reads like the experiment log it is.
                desc = (loop_result or {}).get("description")
                if isinstance(desc, str) and desc.strip():
                    n = (loop_result or {}).get("iteration")
                    prefix = f"Iteration {n}" if n is not None else (task.title or "Iteration")
                    task.title = f"{prefix} · {desc.strip()}"[:256]

            s.add(
                models.Outcome(
                    task_id=task.id,
                    job_id=job.id,
                    commit_sha=sha,
                    branch=branch,
                    summary=summary,
                    status=outcome_status,
                    kind="execute",
                    meta=loop_result or {},
                )
            )
            task.status = "done" if job_status == "done" else "failed"
            task.phase = "done" if job_status == "done" else "failed"
            if task.status == "failed":
                task.last_failed_at = datetime.now(timezone.utc)
            if (
                task.status == "done"
                and not task.synthetic
                and task.mode in ("plan_then_execute", "execute_only")
            ):
                task.integration_status = "pending"

            # Loop bookkeeping: spawn the next iteration (autorun), or, when the
            # loop ended, record a final checkpoint Outcome on the parent.
            if task.source == "loop" and task.parent_task_id:
                if loop_next_child is not None:
                    autorun_ids.append(loop_next_child)
                elif loop_stop_reason is not None:
                    _record_loop_finish(
                        s, task.parent_task_id, job.id, loop_stop_reason
                    )
                    pending_events.append(
                        (
                            "task_done",
                            {
                                "project_id": project_id,
                                "task_id": task.parent_task_id,
                                "job_id": job.id,
                            },
                        )
                    )
            s.flush()
            transitions = _reevaluate_downstream(s, task.id)
            event = "task_done" if task.status == "done" else "task_failed"
            pending_events.append(
                (event, {"project_id": project_id, "task_id": task.id, "job_id": job.id})
            )
            for tid, pid in transitions:
                pending_events.append(("task_ready", {"project_id": pid, "task_id": tid}))
                ds = s.get(models.Task, tid)
                # Planner-sourced tasks auto-run as soon as deps land.
                # Synthetic integrate tasks the planner emitted are included
                # so the wave shape flows without manual clicks. Manual
                # tasks are not.
                if ds is not None and ds.source == "planner":
                    autorun_ids.append(tid)
    # Outer session committed. Emit driver events and consider autodisable.
    _emit_after(bus, pending_events)
    # Conservative: only flips autopilot off when nothing the driver could
    # do remains — no pending, ready, running, or failed tasks.
    try:
        try_autodisable_autopilot(project_id)
    except Exception:  # noqa: BLE001
        log.exception("autodisable autopilot failed for %s", project_id)
    return autorun_ids


async def kickoff_first_phase(task_id: str, job_manager) -> str | None:
    """Spawn the first-phase Job for a ``ready`` task.

    Idempotent: returns ``None`` if the task is missing, not ready, or its
    project no longer exists. Callers — the planner's autorun loop, the
    task_ready autorun hook, and the manual ``run_task`` route — converge
    here so that every "start the work" path uses the same phase/kind/cwd
    rules.

    Mode → first-phase mapping:
      - ``synthetic`` (integrate task) → integrate job at project path.
      - ``plan`` → plan job at project path. The planner decomposes the ask
        into child tasks; no worktree, no commits.
      - ``plan_then_execute`` → plan job at project path (worktree comes
        later on ack).
      - ``execute_only`` → execute job in a fresh worktree, skipping the
        plan/ack handshake. The task still gets a per-task branch and can
        be integrated.
      - ``research`` → execute job at project path, no worktree, no commits.
        The agent's final assistant message is the deliverable.
      - ``one_shot`` → execute job at project path, no worktree, cannot
        be integrated. Used for narrow one-off edits that should not
        produce a branch.
      - ``loop`` → the parent runs NO Claude job. ``planner.start_loop``
        seeds iteration #1 (a ``source='loop'`` one_shot child) and flips the
        parent to ``running``; we then kick that child. Each iteration's
        finalize spawns the next via ``planner.advance_loop`` until a stop
        condition (see ``on_job_finalized``).
    """
    # Loop parent: delegate to the planner's iterate strategy and kick the
    # first iteration child instead of spawning a job for the parent itself.
    with session_scope() as s:
        peek = s.get(models.Task, task_id)
        if peek is None or peek.status != "ready":
            return None
        is_loop = peek.mode == "loop"
    if is_loop:
        from . import planner as _planner
        child_id = _planner.start_loop(task_id)
        if child_id is None:
            return None
        return await kickoff_first_phase(child_id, job_manager)

    with session_scope() as s:
        t = s.get(models.Task, task_id)
        if t is None or t.status != "ready":
            return None
        project = s.get(models.Project, t.project_id)
        if project is None:
            return None
        cwd: str
        if t.synthetic:
            phase, kind = "integrating", "integrate"
            cwd = project.path
        elif t.mode == "plan":
            phase, kind = "planning", "plan"
            cwd = project.path
        elif t.mode == "plan_then_execute":
            phase, kind = "planning", "plan"
            cwd = project.path
        elif t.mode == "execute_only":
            phase, kind = "executing", "execute"
            if t.worktree_path and t.worktree_branch:
                cwd = t.worktree_path
            else:
                base_ref = _resolve_base_ref(s, t)
                try:
                    wt_path, wt_branch = worktrees.create(
                        project, t, base_ref=base_ref
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "execute_only worktree create failed for task %s; "
                        "falling back to project path",
                        task_id,
                    )
                    cwd = project.path
                else:
                    t.worktree_path = wt_path
                    t.worktree_branch = wt_branch
                    t.integration_status = "pending"
                    cwd = wt_path
        else:
            # research and one_shot: execute at project path, no worktree.
            phase, kind = "executing", "execute"
            cwd = project.path
        t.status = "running"
        t.phase = phase
        s.commit()
        project_id = t.project_id
        prompt = t.prompt
        title = f"[task] {t.title}"[:256]
    jid = job_manager.create_job(
        project_id, prompt, title=title, task_id=task_id,
        kind=kind, cwd=cwd,
    )
    await job_manager.start(jid)
    return jid


def _emit_after(bus: "driver_bus.DriverEventBus", events: list[tuple[str, dict]]) -> None:
    for event, kw in events:
        bus.emit(event, **kw)


def _job_cost_usd(s, job_id: str) -> float:
    """Total cost across a job's turns — fed into the loop's spend budget."""
    row = s.execute(
        select(func.coalesce(func.sum(models.Turn.cost_usd), 0.0)).where(
            models.Turn.job_id == job_id
        )
    ).first()
    return float(row[0]) if row and row[0] is not None else 0.0


def _record_loop_finish(s, parent_id: str, job_id: str, stop_reason: str) -> None:
    """Write a final checkpoint Outcome on the loop parent when it ends.

    ``advance_loop`` already flipped the parent to ``done`` (in its own
    session); this records the human-readable summary + best result so the
    parent has a terminal Outcome row like every other task.
    """
    parent = s.get(models.Task, parent_id)
    if parent is None:
        return
    state = dict(parent.loop_state or {})
    spec = parent.loop_spec or {}
    metric_name = spec.get("metric_name", "metric")
    best = state.get("best_metric")
    iters = state.get("iteration")
    summary = (
        f"Loop finished ({stop_reason}). Best {metric_name}={best} "
        f"at {state.get('best_commit')} after {iters} iteration(s)."
    )
    s.add(
        models.Outcome(
            task_id=parent_id,
            job_id=job_id,
            commit_sha=state.get("best_commit"),
            branch=None,
            summary=summary,
            status="success",
            kind="loop",
            meta={"stop_reason": stop_reason, **state},
        )
    )


def try_autodisable_autopilot(project_id: str) -> bool:
    """Flip ``project.autopilot_mode`` to ``off`` when the project has no
    actionable work left. Returns True if a flip happened.

    "Actionable" means anything the driver could legitimately do something
    with: a task in ``pending``/``ready``/``running``, or a ``failed`` task
    that the retry policy might still re-fire. When the project is purely
    ``done`` / ``canceled``, autopilot would just sit there — turn it off so
    it doesn't appear as if the agent is still working.
    """
    bus = driver_bus.get_bus()
    with session_scope() as s:
        p = s.get(models.Project, project_id)
        if p is None or p.autopilot_mode != "on":
            return False
        leftover = s.execute(
            select(models.Task.id).where(
                models.Task.project_id == project_id,
                models.Task.status.in_(
                    ["pending", "ready", "running", "failed"]
                ),
            )
        ).first()
        if leftover:
            return False
        p.autopilot_mode = "off"
    # Wake the driver one last time with the mode_off signal so it stops
    # polling this project. The driver process itself stays alive — killing
    # it requires app.state access from the route layer. If you want the
    # process gone, toggle autopilot off from the UI on any remaining
    # project (or restart the harness).
    bus.emit("mode_off", project_id, force=True)
    return True


class ExecuteSpawn:
    """Inputs ``JobManager.create_job`` needs to spawn the execute Job after
    a plan was acked. ``advance_to_executing`` returns this rather than
    spawning the Job itself, so the route layer keeps the manager dependency.
    """

    __slots__ = ("task_id", "project_id", "prompt", "cwd", "title")

    def __init__(
        self,
        task_id: str,
        project_id: str,
        prompt: str,
        cwd: str,
        title: str,
    ) -> None:
        self.task_id = task_id
        self.project_id = project_id
        self.prompt = prompt
        self.cwd = cwd
        self.title = title


def _resolve_base_ref(s, task: models.Task) -> str | None:
    """Pick the git ref a new task worktree should branch from.

    Precedence:
      1. If any direct dep is a synthetic integrate task whose merge landed on
         a real branch — use that branch. This is the "wave shape" the planner
         is encouraged to produce: foundation tasks → integrate task →
         dependents. The dependents share the integration tip as their base.
      2. Else if the task has exactly one direct dep, use that dep's
         ``worktree_branch`` tip. This handles ``A → B`` chains where B should
         see A's files even without an explicit integrate in between.
      3. Else (no deps, or multi-dep with no integrate ancestor) — return
         ``None`` and let the worktree fork from project HEAD. Multi-dep
         without an integrate is ambiguous; the planner prompt warns against
         this shape.
    """
    dep_ids = _deps_of(s, task.id)
    if not dep_ids:
        return None
    deps = [s.get(models.Task, did) for did in dep_ids]
    deps = [d for d in deps if d is not None]
    # Rule 1: integrate-task ancestor.
    for d in deps:
        if d.synthetic and d.status == "done":
            # Synthetic integrate task: its execute outcome's ``branch`` is the
            # merge target (e.g. main, harness-test/foo). That target now
            # carries all merged work.
            target = _last_integrate_target(s, d.id)
            if target:
                return target
    # Rule 2: single dep tip.
    if len(deps) == 1 and deps[0].worktree_branch and deps[0].status == "done":
        return deps[0].worktree_branch
    return None


def _last_integrate_target(s, synth_task_id: str) -> str | None:
    """The branch the synthetic integrate task merged onto (its outcome's
    ``branch`` field). Returns None if no successful integrate outcome found.
    """
    row = s.execute(
        select(models.Outcome.branch)
        .where(
            models.Outcome.task_id == synth_task_id,
            models.Outcome.kind == "integrate",
            models.Outcome.status == "success",
        )
        .order_by(models.Outcome.created_at.desc())
    ).first()
    return row[0] if row else None


def advance_to_executing(task_id: str, prompt_addendum: str = "") -> ExecuteSpawn:
    """Transition a Task from ``awaiting_ack`` to ``executing``.

    Creates the per-task git worktree, records its path + branch on the task,
    flips ``task.phase='executing'``. Returns an :class:`ExecuteSpawn` the
    route layer feeds into ``JobManager.create_job`` to start the execute
    Job (its own conversation, born at the worktree cwd — no resume from the
    plan Job).

    The worktree's base ref is chosen by :func:`_resolve_base_ref` so that
    tasks with deps see their predecessors' work in their starting tree.
    """
    with session_scope() as s:
        task = s.get(models.Task, task_id)
        if task is None:
            raise ValueError(f"unknown task {task_id}")
        if task.phase != "awaiting_ack":
            raise ValueError(
                f"task {task_id} is not awaiting ack (phase={task.phase!r})"
            )
        project = s.get(models.Project, task.project_id)
        if project is None:
            raise ValueError(f"project {task.project_id} for task {task_id} not found")
        if task.worktree_path and task.worktree_branch:
            # A previous ack already created the worktree (e.g. retry of just
            # the execute Job). Reuse it.
            path, branch = task.worktree_path, task.worktree_branch
        else:
            base_ref = _resolve_base_ref(s, task)
            path, branch = worktrees.create(project, task, base_ref=base_ref)
            task.worktree_path = path
            task.worktree_branch = branch
        task.phase = "executing"
        base = task.prompt
        full_prompt = f"{base}\n\n{prompt_addendum}" if prompt_addendum else base
        title = f"[task] {task.title}"[:256]
        return ExecuteSpawn(
            task_id=task_id,
            project_id=project.id,
            prompt=full_prompt,
            cwd=path,
            title=title,
        )


def reconcile_on_startup() -> None:
    """Idempotent: refresh pending/ready statuses based on current dep states.

    A task that was `running` when the server died is left as-is — the job
    reconciler will mark its job `stopped`, and the next finalize call (or
    a user re-run) will pick it up. We only flip pending↔ready here.
    """
    with session_scope() as s:
        rows = (
            s.query(models.Task)
            .filter(models.Task.status.in_(["pending", "ready"]))
            .all()
        )
        for t in rows:
            new = "ready" if _all_deps_done(s, t.id) else "pending"
            if t.status != new:
                t.status = new
