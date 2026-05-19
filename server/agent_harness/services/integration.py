"""Synthetic integration tasks.

After a wave of execute turns produces per-task worktree branches, the
orchestrator (or human) submits the wave for integration. We model this as
another agent task — a *synthetic* one — whose prompt instructs the agent to
merge those branches into the project's target branch and resolve conflicts.

The integration agent runs in ``project.path`` (NOT a worktree); when its job
finishes successfully the input tasks' worktrees and branches are removed and
their ``integration_status`` is flipped to ``integrated``.
"""

from __future__ import annotations

import subprocess

from sqlalchemy import select

from .. import models
from ..db import session_scope


def _current_branch(repo: str) -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def _build_prompt(
    branches: list[tuple[str, str | None]], target_branch: str
) -> str:
    lines = [
        f"Integrate the following task branches into '{target_branch}':",
        "",
    ]
    for br, summary in branches:
        lines.append(f"- {br}")
        if summary:
            for s in summary.splitlines()[:3]:
                lines.append(f"    {s}")
    lines += [
        "",
        "Steps:",
        f"1. Ensure you are on '{target_branch}'.",
        f"2. Merge each branch listed above into '{target_branch}'. Use "
        "`git merge` or `git rebase` as appropriate. Resolve conflicts inline.",
        "3. Commit the merge(s).",
        "4. If you cannot resolve a conflict, stop and explain. "
        "Do not delete branches.",
    ]
    return "\n".join(lines)


def create_integration_task(
    project_id: str,
    task_ids: list[str],
    target_branch: str | None = None,
) -> str:
    """Create a synthetic integration Task. Returns its id.

    The task is created in ``status='ready'`` (its deps are already ``done``)
    so the caller can immediately ``POST /api/tasks/{id}/run`` it.
    """
    if not task_ids:
        raise ValueError("task_ids must be non-empty")
    with session_scope() as s:
        project = s.get(models.Project, project_id)
        if project is None:
            raise ValueError(f"unknown project {project_id}")
        inputs: list[models.Task] = []
        for tid in task_ids:
            t = s.get(models.Task, tid)
            if t is None:
                raise ValueError(f"unknown task {tid}")
            if t.project_id != project_id:
                raise ValueError(f"task {tid} not in project {project_id}")
            if t.status != "done":
                raise ValueError(
                    f"task {tid} status={t.status!r}; only 'done' tasks integrate"
                )
            if not t.worktree_branch:
                raise ValueError(
                    f"task {tid} has no worktree_branch (one-shot or not built)"
                )
            inputs.append(t)
        tb = target_branch or _current_branch(project.path) or "main"

        branch_summaries: list[tuple[str, str | None]] = []
        for t in inputs:
            row = s.execute(
                select(models.Outcome.summary)
                .where(
                    models.Outcome.task_id == t.id,
                    models.Outcome.kind == "execute",
                )
                .order_by(models.Outcome.created_at.desc())
            ).first()
            branch_summaries.append((t.worktree_branch, row[0] if row else None))

        prompt = _build_prompt(branch_summaries, tb)
        titles = ", ".join(t.title for t in inputs)
        synth = models.Task(
            project_id=project_id,
            title=f"integrate: {titles}"[:256],
            prompt=prompt,
            status="ready",
            source="manual",
            mode="one_shot",
            synthetic=True,
        )
        s.add(synth)
        s.flush()
        for t in inputs:
            s.add(models.TaskDependency(task_id=synth.id, depends_on_id=t.id))
        s.flush()
        synth_id = synth.id
    from . import driver_bus

    driver_bus.get_bus().emit("task_ready", project_id, task_id=synth_id)
    return synth_id
