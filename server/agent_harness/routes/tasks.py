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
from ..schemas import JobOut, TaskCreate, TaskOut, TaskUpdate
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
def create_task(
    project_id: str, body: TaskCreate, s: Session = Depends(get_session)
) -> TaskOut:
    if s.get(models.Project, project_id) is None:
        raise HTTPException(404, "unknown project")
    _validate_deps(s, project_id, body.depends_on)
    t = models.Task(
        project_id=project_id,
        title=body.title,
        prompt=body.prompt,
        order_idx=body.order_idx,
        source="manual",
        status="pending",
    )
    s.add(t)
    s.flush()
    _detect_cycle(s, t.id, body.depends_on)
    for d in body.depends_on:
        s.add(models.TaskDependency(task_id=t.id, depends_on_id=d))
    s.flush()
    t.status = _initial_status(s, t.id)
    s.commit()
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
        # Recompute status if task is still in pre-run states.
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
    t = s.get(models.Task, task_id)
    if t is None:
        raise HTTPException(404, "not found")
    if t.status != "ready":
        raise HTTPException(409, f"task status is {t.status!r}; only 'ready' may run")
    mgr = _manager(request)
    title = f"[task] {t.title}"[:256]
    jid = mgr.create_job(t.project_id, t.prompt, title=title, task_id=t.id)
    t.status = "running"
    s.commit()
    await mgr.start(jid)
    s.expire_all()
    job = s.get(models.Job, jid)
    assert job is not None
    return _job_to_out(job)


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
    t.status = "canceled"
    s.commit()
    s.refresh(t)
    return _to_out(s, t)
