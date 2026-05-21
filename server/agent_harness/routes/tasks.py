"""Task routes.

Tasks live inside a project and form a DAG via `TaskDependency`. Status flow:

    pending  ──(deps satisfied)─▶  ready
    ready    ──(POST /run)──────▶  running        (a job is enqueued)
    running  ──(job finalize)───▶  done | failed  (an Outcome is recorded)
    any      ──(POST /cancel)──▶   canceled

There is no auto-run: the user explicitly kicks each ready task via
`POST /api/tasks/{id}/run`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..jobs import JobManager
from ..schemas import JobOut, MergeIn, SplitIn, TaskCreate, TaskOut, TaskUpdate
from ..routes.jobs import _to_out as _job_to_out

router = APIRouter(tags=["tasks"], dependencies=[Depends(require_auth)])


def _manager(request: Request) -> JobManager:
    mgr = getattr(request.app.state, "job_manager", None)
    if mgr is None:
        raise HTTPException(503, "job manager not initialised")
    return mgr


def _deps_of(s: Session, task_id: str) -> list[str]:
    rows = s.execute(
        select(models.TaskDependency.depends_on_id).where(
            models.TaskDependency.task_id == task_id
        )
    ).all()
    return [r[0] for r in rows]


def _latest_outcome_id(s: Session, task_id: str) -> str | None:
    row = s.execute(
        select(models.Outcome.id)
        .where(models.Outcome.task_id == task_id)
        .order_by(models.Outcome.created_at.desc())
    ).first()
    return row[0] if row is not None else None


def _to_out(s: Session, t: models.Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        project_id=t.project_id,
        title=t.title,
        prompt=t.prompt,
        status=t.status,
        phase=t.phase,
        source=t.source,
        order_idx=t.order_idx,
        mode=t.mode,
        worktree_path=t.worktree_path,
        worktree_branch=t.worktree_branch,
        integration_status=t.integration_status,
        synthetic=t.synthetic,
        depends_on=_deps_of(s, t.id),
        latest_outcome_id=_latest_outcome_id(s, t.id),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _all_deps_done(s: Session, task_id: str) -> bool:
    dep_ids = _deps_of(s, task_id)
    if not dep_ids:
        return True
    rows = (
        s.execute(select(models.Task.status).where(models.Task.id.in_(dep_ids))).all()
    )
    statuses = [r[0] for r in rows]
    if len(statuses) != len(dep_ids):
        return False  # one or more deps missing
    return all(st == "done" for st in statuses)


def _initial_status(s: Session, task_id: str) -> str:
    return "ready" if _all_deps_done(s, task_id) else "pending"


def _reevaluate_downstream(s: Session, task_id: str) -> None:
    """Flip `pending` downstream tasks to `ready` when their deps are all done."""
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


def _validate_deps(s: Session, project_id: str, dep_ids: list[str]) -> None:
    if not dep_ids:
        return
    rows = s.execute(
        select(models.Task.id, models.Task.project_id).where(
            models.Task.id.in_(dep_ids)
        )
    ).all()
    found = {r[0]: r[1] for r in rows}
    for d in dep_ids:
        if d not in found:
            raise HTTPException(400, f"unknown task dependency {d}")
        if found[d] != project_id:
            raise HTTPException(400, f"dependency {d} is not in the same project")


def _detect_cycle(s: Session, task_id: str, new_deps: list[str]) -> None:
    """Reject if adding these deps would create a cycle reaching back to task_id."""
    if not new_deps:
        return
    visited: set[str] = set()
    stack: list[str] = list(new_deps)
    while stack:
        cur = stack.pop()
        if cur == task_id:
            raise HTTPException(400, "dependency cycle detected")
        if cur in visited:
            continue
        visited.add(cur)
        rows = s.execute(
            select(models.TaskDependency.depends_on_id).where(
                models.TaskDependency.task_id == cur
            )
        ).all()
        stack.extend(r[0] for r in rows)


# ----------------------------- project-scoped --------------------------- #


@router.post(
    "/api/projects/{project_id}/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    project_id: str,
    body: TaskCreate,
    request: Request,
    run: bool = False,
    s: Session = Depends(get_session),
) -> TaskOut:
    """Create a manual Task. Pass ``?run=true`` to immediately spawn its first
    Job if the task is ``ready`` after creation (i.e. has no unsatisfied deps).
    ``mode`` defaults to ``plan_then_execute`` if omitted; pass ``"one_shot"``
    for ad-hoc tasks that should skip the planning gate.
    """
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    _validate_deps(s, project_id, body.depends_on)
    task_kwargs: dict[str, object] = dict(
        project_id=project_id,
        title=body.title,
        prompt=body.prompt,
        order_idx=body.order_idx,
        source="manual",
        status="pending",
    )
    if body.mode is not None:
        task_kwargs["mode"] = body.mode
    t = models.Task(**task_kwargs)
    s.add(t)
    s.flush()
    _detect_cycle(s, t.id, body.depends_on)
    for d in body.depends_on:
        s.add(models.TaskDependency(task_id=t.id, depends_on_id=d))
    s.flush()
    t.status = _initial_status(s, t.id)
    s.commit()
    s.refresh(t)
    if t.status == "ready":
        from ..services import driver_bus

        driver_bus.get_bus().emit("task_ready", project_id, task_id=t.id)
        if run:
            # Fire the first phase Job immediately. Mirrors run_task but inline
            # so the response still carries the (now running) Task state.
            from ..services import task_runner

            await task_runner.kickoff_first_phase(t.id, _manager(request))
            s.refresh(t)
    return _to_out(s, t)


@router.get("/api/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(project_id: str, s: Session = Depends(get_session)) -> list[TaskOut]:
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    rows = (
        s.query(models.Task)
        .filter(models.Task.project_id == project_id)
        .order_by(models.Task.order_idx, models.Task.created_at)
        .all()
    )
    return [_to_out(s, t) for t in rows]


@router.get("/api/tasks", response_model=list[TaskOut])
def list_all_tasks(s: Session = Depends(get_session)) -> list[TaskOut]:
    """All tasks across all projects, newest first. Used by the Projects
    page to show per-project task counts without N+1 fetches.
    """
    rows = s.query(models.Task).order_by(models.Task.created_at.desc()).all()
    return [_to_out(s, t) for t in rows]


# ----------------------------- task-scoped ------------------------------ #


@router.get("/api/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, s: Session = Depends(get_session)) -> TaskOut:
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    return _to_out(s, t)


@router.patch("/api/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: str, body: TaskUpdate, s: Session = Depends(get_session)
) -> TaskOut:
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    if t.status in {"running"}:
        raise HTTPException(409, "cannot edit a running task")
    if body.title is not None:
        t.title = body.title
    if body.prompt is not None:
        t.prompt = body.prompt
    if body.order_idx is not None:
        t.order_idx = body.order_idx
    if body.mode is not None:
        t.mode = body.mode
    if body.depends_on is not None:
        _validate_deps(s, t.project_id, body.depends_on)
        _detect_cycle(s, t.id, body.depends_on)
        s.query(models.TaskDependency).filter(
            models.TaskDependency.task_id == t.id
        ).delete()
        s.flush()
        for d in body.depends_on:
            s.add(models.TaskDependency(task_id=t.id, depends_on_id=d))
        s.flush()
    # Re-evaluate status in pre-run states so PATCH also serves as "confirm a
    # planner draft" — any edit (or empty body) promotes pending→ready when
    # the task's deps are satisfied.
    if t.status in {"pending", "ready"}:
        t.status = _initial_status(s, t.id)
    s.commit()
    s.refresh(t)
    return _to_out(s, t)


@router.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, s: Session = Depends(get_session)) -> None:
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    has_jobs = (
        s.execute(select(models.Job.id).where(models.Job.task_id == task_id).limit(1))
    ).first()
    if has_jobs is not None:
        raise HTTPException(409, "task has jobs; cancel instead")
    # Remove this task as a dependency on any downstream tasks.
    s.query(models.TaskDependency).filter(
        (models.TaskDependency.task_id == task_id)
        | (models.TaskDependency.depends_on_id == task_id)
    ).delete()
    s.delete(t)
    s.commit()


@router.post("/api/tasks/{task_id}/run", response_model=JobOut)
async def run_task(
    task_id: str, request: Request, s: Session = Depends(get_session)
) -> JobOut:
    """Kick a ready Task. Spawns the first phase's Job:
    - plan / plan_then_execute → Plan Job (kind=plan, cwd=project.path)
    - research / one_shot non-synthetic → Execute Job (kind=execute, cwd=project.path)
    - synthetic (integration) → Integrate Job (kind=integrate, cwd=project.path)
    """
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    if t.status != "ready":
        raise HTTPException(409, f"task status is {t.status!r}; only 'ready' may run")
    project = s.get(models.Project, t.project_id)
    if project is None:
        raise HTTPException(500, "project missing")
    mgr = _manager(request)
    title = f"[task] {t.title}"[:256]
    if t.synthetic:
        phase = "integrating"
        kind = "integrate"
    elif t.mode in ("plan", "plan_then_execute"):
        phase = "planning"
        kind = "plan"
    else:
        phase = "executing"
        kind = "execute"
    t.status = "running"
    t.phase = phase
    s.commit()
    jid = mgr.create_job(
        t.project_id,
        t.prompt,
        title=title,
        task_id=t.id,
        kind=kind,
        cwd=project.path,
    )
    await mgr.start(jid)
    s.expire_all()
    job = s.get(models.Job, jid)
    assert job is not None
    return _job_to_out(job)


@router.post("/api/tasks/{task_id}/ack", response_model=JobOut)
async def ack_task(
    task_id: str, request: Request, s: Session = Depends(get_session)
) -> JobOut:
    """Advance a Task from ``awaiting_ack`` to ``executing``.

    Creates the per-task git worktree (if not already present), flips
    ``task.phase='executing'``, and spawns a NEW Execute Job in the worktree.
    The Plan Job stays as historical record of the planning conversation.
    Optional addendum can be sent via ``?notes=...`` (or just hit without one
    for a bare ack).
    """
    from ..services import task_runner

    notes = request.query_params.get("notes", "") or ""
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    mgr = _manager(request)
    try:
        spawn = task_runner.advance_to_executing(task_id, prompt_addendum=notes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        jid = mgr.create_job(
            project_id=spawn.project_id,
            prompt=spawn.prompt,
            title=spawn.title,
            task_id=spawn.task_id,
            kind="execute",
            cwd=spawn.cwd,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await mgr.start(jid)
    s.expire_all()
    job = s.get(models.Job, jid)
    assert job is not None
    return _job_to_out(job)


@router.post("/api/tasks/{task_id}/split", response_model=list[TaskOut])
def split_task(
    task_id: str, body: SplitIn, s: Session = Depends(get_session)
) -> list[TaskOut]:
    """Replace ``task_id`` with N new tasks. Pure DAG surgery — no jobs touched.

    Allowed only when the task is ``pending`` or ``ready``. If
    ``inherit_deps_in``, the first new task picks up the original's incoming
    deps. If ``link_in_series``, the new tasks form a chain. The original
    task's outgoing dependents are rewired to depend on the last new task.
    The original task row is deleted.
    """
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    if t.status not in {"pending", "ready"}:
        raise HTTPException(
            409, f"can only split pending/ready tasks (status={t.status!r})"
        )
    if not body.new_tasks:
        raise HTTPException(400, "new_tasks must be non-empty")

    incoming = _deps_of(s, task_id) if body.inherit_deps_in else []
    outgoing = [
        r[0]
        for r in s.execute(
            select(models.TaskDependency.task_id).where(
                models.TaskDependency.depends_on_id == task_id
            )
        ).all()
    ]
    project_id = t.project_id
    base_idx = t.order_idx

    # Drop deps that involve the original.
    s.query(models.TaskDependency).filter(
        (models.TaskDependency.task_id == task_id)
        | (models.TaskDependency.depends_on_id == task_id)
    ).delete()
    s.flush()
    s.delete(t)
    s.flush()

    new_ids: list[str] = []
    for i, item in enumerate(body.new_tasks):
        nt = models.Task(
            project_id=project_id,
            title=item.title,
            prompt=item.prompt,
            order_idx=base_idx + i,
            source="manual",
            status="pending",
        )
        s.add(nt)
        s.flush()
        new_ids.append(nt.id)

    if incoming:
        for d in incoming:
            s.add(models.TaskDependency(task_id=new_ids[0], depends_on_id=d))
    if body.link_in_series and len(new_ids) > 1:
        for prev, nxt in zip(new_ids, new_ids[1:]):
            s.add(models.TaskDependency(task_id=nxt, depends_on_id=prev))
    last_new = new_ids[-1]
    for downstream in outgoing:
        s.add(models.TaskDependency(task_id=downstream, depends_on_id=last_new))
    s.flush()

    for nid in new_ids:
        nt = s.get(models.Task, nid)
        assert nt is not None
        nt.status = _initial_status(s, nid)
    # Downstream tasks may have become unblocked or newly blocked.
    for downstream in outgoing:
        ds = s.get(models.Task, downstream)
        if ds is None:
            continue
        if ds.status in {"pending", "ready"}:
            ds.status = _initial_status(s, downstream)
    s.commit()

    out_tasks = [s.get(models.Task, nid) for nid in new_ids]
    from ..services import driver_bus

    bus = driver_bus.get_bus()
    for nt in out_tasks:
        if nt is not None and nt.status == "ready":
            bus.emit("task_ready", nt.project_id, task_id=nt.id)
    return [_to_out(s, nt) for nt in out_tasks if nt is not None]


@router.post("/api/tasks/merge", response_model=TaskOut)
def merge_tasks(body: MergeIn, s: Session = Depends(get_session)) -> TaskOut:
    """Collapse N tasks into one. Pure DAG surgery — no jobs touched.

    All inputs must be ``pending`` and in the same project. The merged task
    inherits the union of inputs' incoming deps; downstream tasks of any
    input are rewired to depend on the merged task. Inputs are deleted.
    Rejected if any input lies on a path through the input set (would
    collapse a real dependency).
    """
    if not body.task_ids:
        raise HTTPException(400, "task_ids must be non-empty")
    tasks = [s.get(models.Task, tid) for tid in body.task_ids]
    if any(t is None for t in tasks):
        raise HTTPException(404, "one or more tasks not found")
    project_ids = {t.project_id for t in tasks}
    if len(project_ids) != 1:
        raise HTTPException(400, "all tasks must be in the same project")
    for t in tasks:
        if t.status != "pending":
            raise HTTPException(
                409, f"task {t.id} status={t.status!r}; only pending tasks may merge"
            )
    # Reject if any input has a path to another input through the DAG.
    id_set = set(body.task_ids)
    for t in tasks:
        seen: set[str] = set()
        stack = list(_deps_of(s, t.id))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in id_set:
                raise HTTPException(
                    400, f"task {t.id} depends on {cur}; cannot collapse"
                )
            stack.extend(_deps_of(s, cur))

    project_id = next(iter(project_ids))
    incoming: set[str] = set()
    outgoing: set[str] = set()
    base_idx = min(t.order_idx for t in tasks)
    for t in tasks:
        for d in _deps_of(s, t.id):
            if d not in id_set:
                incoming.add(d)
        rows = s.execute(
            select(models.TaskDependency.task_id).where(
                models.TaskDependency.depends_on_id == t.id
            )
        ).all()
        for (down,) in rows:
            if down not in id_set:
                outgoing.add(down)

    for t in tasks:
        s.query(models.TaskDependency).filter(
            (models.TaskDependency.task_id == t.id)
            | (models.TaskDependency.depends_on_id == t.id)
        ).delete()
        s.flush()
        s.delete(t)
    s.flush()

    merged = models.Task(
        project_id=project_id,
        title=body.title,
        prompt=body.prompt,
        order_idx=base_idx,
        source="manual",
        status="pending",
    )
    s.add(merged)
    s.flush()
    for d in incoming:
        s.add(models.TaskDependency(task_id=merged.id, depends_on_id=d))
    for down in outgoing:
        s.add(models.TaskDependency(task_id=down, depends_on_id=merged.id))
    s.flush()
    merged.status = _initial_status(s, merged.id)
    for down in outgoing:
        ds = s.get(models.Task, down)
        if ds is None:
            continue
        if ds.status in {"pending", "ready"}:
            ds.status = _initial_status(s, down)
    s.commit()
    s.refresh(merged)
    if merged.status == "ready":
        from ..services import driver_bus

        driver_bus.get_bus().emit("task_ready", merged.project_id, task_id=merged.id)
    return _to_out(s, merged)


@router.post("/api/tasks/{task_id}/retry", response_model=JobOut)
async def retry_task(
    task_id: str, request: Request, s: Session = Depends(get_session)
) -> JobOut:
    """Retry a failed Task. Replays from the failed phase, not from scratch.

    Behavior depends on the failing phase:
    - ``phase=planning``/``awaiting_ack`` (or task never ran): start over —
      drop worktree (if any), reset to ready, spawn fresh Plan Job.
    - ``phase=executing``: the worktree carries the partial work; keep it,
      flip the Task back to ``awaiting_ack``, and spawn a fresh Execute Job
      via the ack path. (Re-plans only when explicit via /restart.)
    - ``phase=integrating``: drop the Task's own state and re-run as a fresh
      Integrate Job; input task branches are not touched.

    To wipe everything and replay from pending, use ``POST /tasks/{id}/restart``.
    """
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    if t.status != "failed":
        raise HTTPException(409, f"task status is {t.status!r}; only 'failed' may retry")
    mgr = _manager(request)
    title = f"[task] {t.title}"[:256]
    failed_phase = t.phase
    project = s.get(models.Project, t.project_id)
    if project is None:
        raise HTTPException(500, "project missing")
    t.retries = (t.retries or 0) + 1

    if failed_phase == "executing" and t.worktree_path:
        # Keep the worktree and re-run the execute Job only.
        from ..services import task_runner

        t.phase = "awaiting_ack"
        t.status = "running"
        t.integration_status = None
        s.commit()
        try:
            spawn = task_runner.advance_to_executing(task_id, prompt_addendum="")
        except ValueError as e:
            raise HTTPException(400, str(e))
        jid = mgr.create_job(
            project_id=spawn.project_id,
            prompt=spawn.prompt,
            title=spawn.title,
            task_id=spawn.task_id,
            kind="execute",
            cwd=spawn.cwd,
        )
        await mgr.start(jid)
        s.expire_all()
        job = s.get(models.Job, jid)
        assert job is not None
        return _job_to_out(job)

    # Default: clear worktree and replay from the first phase.
    if t.worktree_path or t.worktree_branch:
        from ..services import worktrees

        worktrees.remove(project, t)
        t.worktree_path = None
        t.worktree_branch = None
    t.integration_status = None
    if t.synthetic:
        t.phase = "integrating"
        kind = "integrate"
    elif t.mode in ("plan", "plan_then_execute"):
        t.phase = "planning"
        kind = "plan"
    else:
        t.phase = "executing"
        kind = "execute"
    t.status = "running"
    s.commit()
    jid = mgr.create_job(
        t.project_id,
        t.prompt,
        title=title,
        task_id=t.id,
        kind=kind,
        cwd=project.path,
    )
    await mgr.start(jid)
    s.expire_all()
    job = s.get(models.Job, jid)
    assert job is not None
    return _job_to_out(job)


@router.post("/api/tasks/{task_id}/restart", response_model=TaskOut)
async def restart_task(
    task_id: str, request: Request, s: Session = Depends(get_session)
) -> TaskOut:
    """Full reset: stop any running Job, drop the worktree, clear phase, set
    status back to ``pending`` (or ``ready`` if deps are satisfied). Old Jobs
    and Outcomes are kept as history; the next ``POST /run`` will start fresh.
    """
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    mgr = _manager(request)
    if t.status == "running":
        job_row = s.execute(
            select(models.Job.id)
            .where(models.Job.task_id == task_id)
            .order_by(models.Job.created_at.desc())
        ).first()
        if job_row is not None:
            await mgr.stop(job_row[0])
    if t.worktree_path or t.worktree_branch:
        from ..services import worktrees

        project = s.get(models.Project, t.project_id)
        if project is not None:
            worktrees.remove(project, t)
        t.worktree_path = None
        t.worktree_branch = None
    t.phase = None
    t.integration_status = None
    t.status = _initial_status(s, task_id)
    s.commit()
    s.refresh(t)
    return _to_out(s, t)


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
    task_id: str, request: Request, s: Session = Depends(get_session)
) -> TaskOut:
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    if t.status == "running":
        # Stop the most recent task-bound job.
        job_row = s.execute(
            select(models.Job.id)
            .where(models.Job.task_id == task_id)
            .order_by(models.Job.created_at.desc())
        ).first()
        if job_row is not None:
            mgr = _manager(request)
            await mgr.stop(job_row[0])
    # GC the worktree if one was created for this task.
    if t.worktree_path or t.worktree_branch:
        from ..services import worktrees

        project = s.get(models.Project, t.project_id)
        if project is not None:
            worktrees.remove(project, t)
        t.worktree_path = None
        t.worktree_branch = None
    t.status = "canceled"
    s.commit()
    s.refresh(t)
    return _to_out(s, t)
