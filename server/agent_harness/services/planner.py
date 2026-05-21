"""Planner: turn a high-level ask into a list of child tasks.

The planner runs as a first-class Task (``mode='plan'``) — created by
``POST /api/projects/{id}/plan`` and kicked via ``task_runner.kickoff_first_phase``.
Its job spawns a Claude conversation with :data:`PLANNER_INSTRUCTIONS` prepended;
when that job finishes, ``task_runner.on_job_finalized`` calls
:func:`parse_and_insert_from_log_dir` here to read the assistant_text events,
parse the JSON task array, and insert the child tasks.

This module owns the prompt and the parsing/insert helpers. The orchestration
lives in ``task_runner`` so plan tasks share the same lifecycle plumbing as
every other task (status, phase, outcomes, autorun, driver events).
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from pathlib import Path

from .. import models
from ..db import session_scope

log = logging.getLogger(__name__)


PLANNER_INSTRUCTIONS = """\
You decompose a user's ask into a parallel-friendly task list.

Each task you emit runs as its own Claude session, in its own git worktree on
a `task/<id>` branch. Tasks with `mode: "plan_then_execute"` get a planning
turn first — that's where the agent audits its area of the codebase before
editing. Your scopes need to be crisp, but you don't need to audit the repo
here; each task will look into the files it needs.

## Output contract

Write a brief findings paragraph (3–10 lines) naming WHAT each task should
investigate or change (not the answers). Then emit a strict JSON array
(Markdown fence allowed but not required):

  [
    {
      "title": "<imperative title, <= 80 chars>",
      "prompt": "<the exact prompt the agent will run when this task executes — name specific files and the concrete change>",
      "depends_on_titles": ["<earlier task title>", ...],
      "kind": "task" | "integrate",                                    // optional, default "task"
      "mode": "plan_then_execute" | "execute_only" | "research"        // optional, default "plan_then_execute"
    },
    ...
  ]

## Modes

- `plan_then_execute` (default) — agent plans, you ack, then it executes in
  a worktree. Use for non-trivial code changes.
- `execute_only` — narrow, well-specified code change. Still gets a worktree
  + branch, just skips the plan/ack handshake.
- `research` — answer-only task. Runs at the project root with no worktree
  and makes no commits. The final assistant message is the deliverable shown
  to the user. Use when the ask is a question, an explanation, or any
  non-code investigation.

## Question-shaped asks

If the user's ask is a question or research request (e.g. "what does this
repo do?", "explain how X works"), the right shape is exactly one `research`
task whose prompt is the question itself. The task owns the answer.

## Always emit at least one task

The project page expects a task to track every ask. If you cannot decompose
further, emit one task whose prompt is the user's ask verbatim.

## Wave shape (when many tasks share a foundation)

Each task lives on its own branch, so sibling tasks (run in parallel) don't
see each other's files. If multiple downstream tasks need to build on the
same foundation, plan a wave:

```
[T1, T2, T3]   ← siblings, foundation work (no deps)
     ↓
 [Integrate]   ← kind: "integrate", merges T1+T2+T3 into a shared branch
     ↓
[T4, T5, T6]   ← siblings, build on integrated foundation
```

An integrate entry needs `title`, `kind: "integrate"`, `depends_on_titles`
(every task whose work should be merged), and `target_branch` (a fresh
branch name like `feat/<slug>-foundation`). The harness builds the merge
prompt automatically — leave `prompt` off integrate entries.

Use waves only when the foundation is actually shared. If the ask is small
or the tasks are truly independent (disjoint files), one or a few siblings
with no deps is best — the harness runs them in parallel.

## Task-shaping rules

- 1 to 16 tasks. Concrete and parallel beats long and serial.
- Disjoint-file tasks MUST have empty `depends_on_titles` so they parallelize.
- Each `prompt` names specific files/paths and the concrete change —
  "Update PlotSection.tsx to render plot and controls in a responsive Stack
  (column on base, row on md+)", not "implement the layout".
- Don't include a separate "audit" or "verify" task. Audits happen inside
  each task's planning phase; the harness verifies on integration.
- Each task should be small enough to checkpoint with one git commit.

The user's ask follows.
"""


# Modes the planner is allowed to emit on child tasks. "plan" is excluded —
# nested plans don't make sense; the top-level planner already owns this run.
_ALLOWED_CHILD_MODES = ("plan_then_execute", "execute_only", "one_shot", "research")


def _extract_json_array(text: str) -> list[dict] | None:
    """Best-effort JSON-array extractor. Returns None on failure."""
    if not text:
        return None
    # Direct parse first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    # Strip code fences.
    fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:  # noqa: BLE001
            pass
    # Fall back to the first bracketed array.
    m = re.search(r"(\[[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    return None


def _all_assistant_text(log_dir: Path) -> str:
    if not log_dir.is_dir():
        return ""
    pieces: list[str] = []
    for f in sorted(log_dir.glob("turn-*.jsonl")):
        try:
            with f.open("rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or '"assistant_text"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if ev.get("type") != "assistant_text":
                        continue
                    t = ev.get("text")
                    if isinstance(t, str):
                        pieces.append(t)
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(pieces)


def parse_and_insert_from_log_dir(
    project_id: str, plan_task_id: str, log_dir: Path, ask: str
) -> list[str]:
    """Read assistant_text from ``log_dir``, parse the JSON task array, and
    insert child tasks.

    Always returns the ids of the inserted child tasks. If the planner output
    can't be parsed into a non-empty task array, inserts a single fallback
    ``research`` task whose prompt is the original ask — the "always emit at
    least one task" contract — so the project page never ends up empty after
    a plan run.
    """
    raw = _all_assistant_text(log_dir)
    parsed = _extract_json_array(raw)
    if parsed and isinstance(parsed, list):
        ids = _insert_drafts(project_id, parsed)
        if ids:
            return ids
    log.info(
        "planner emitted no usable tasks for %s; inserting fallback research task",
        plan_task_id,
    )
    return _insert_fallback_research(project_id, ask)


def _insert_fallback_research(project_id: str, ask: str) -> list[str]:
    """Insert a single ``research`` task carrying the original ask.

    Used when the planner failed to produce a task array. Lands ``ready`` so
    the autorun path picks it up immediately.
    """
    title = (ask.strip().splitlines()[0] if ask.strip() else "Research")[:80]
    with session_scope() as s:
        t = models.Task(
            project_id=project_id,
            title=title or "Research",
            prompt=ask,
            status="ready",
            source="planner",
            order_idx=0,
            mode="research",
        )
        s.add(t)
        s.flush()
        return [t.id]


def _insert_drafts(project_id: str, parsed: list[dict]) -> list[str]:
    """Insert planner-drafted tasks, resolving depends_on_titles to ids.

    Drafts land as ``source='planner'``. Tasks with no deps are inserted as
    ``ready`` so they auto-run; tasks with deps stay ``pending`` until their
    predecessors finish. Skips entries missing a title or prompt.

    Entries with ``kind: "integrate"`` are created as synthetic, one-shot
    integrate tasks. Their prompt is auto-built from the dep ``task/<id>``
    branch names so the integrate runs after the deps finish.
    """
    from . import integration, worktrees

    created_ids: list[str] = []
    with session_scope() as s:
        title_to_id: dict[str, str] = {}
        title_to_kind: dict[str, str] = {}
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if not isinstance(title, str):
                continue
            kind = item.get("kind") if isinstance(item.get("kind"), str) else "task"
            if kind == "integrate":
                target_branch = item.get("target_branch")
                if not isinstance(target_branch, str) or not target_branch.strip():
                    log.warning(
                        "planner integrate entry %r missing target_branch; skipping",
                        title,
                    )
                    continue
                # Prompt is built in pass 2 (we need dep IDs first); insert
                # placeholder for now.
                t = models.Task(
                    project_id=project_id,
                    title=title[:256],
                    prompt="(integrate prompt — built after deps resolve)",
                    status="pending",
                    source="planner",
                    order_idx=idx,
                    mode="one_shot",
                    synthetic=True,
                )
                s.add(t)
                s.flush()
                title_to_id[title] = t.id
                title_to_kind[title] = "integrate"
                # Stash target on a side dict for pass 2.
                t.prompt = _integrate_prompt_placeholder(target_branch)
                created_ids.append(t.id)
                continue
            prompt = item.get("prompt")
            if not isinstance(prompt, str):
                continue
            mode = item.get("mode")
            if mode not in _ALLOWED_CHILD_MODES:
                mode = "plan_then_execute"
            t = models.Task(
                project_id=project_id,
                title=title[:256],
                prompt=prompt,
                status="pending",
                source="planner",
                order_idx=idx,
                mode=mode,
            )
            s.add(t)
            s.flush()
            title_to_id[title] = t.id
            title_to_kind[title] = "task"
            created_ids.append(t.id)

        # Resolve dependencies. Titles that don't match anything are skipped
        # (no hard failure — the user can fix in the edit step).
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if title not in title_to_id:
                continue
            deps = item.get("depends_on_titles") or []
            if not isinstance(deps, list):
                continue
            tid = title_to_id[title]
            for dep_title in deps:
                if not isinstance(dep_title, str):
                    continue
                dep_id = title_to_id.get(dep_title)
                if dep_id is None or dep_id == tid:
                    continue
                s.add(models.TaskDependency(task_id=tid, depends_on_id=dep_id))
        s.flush()

        # Build the integrate prompts now that we know dep ids → branch names.
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if title not in title_to_id or title_to_kind.get(title) != "integrate":
                continue
            tid = title_to_id[title]
            target_branch = item.get("target_branch")
            if not isinstance(target_branch, str):
                continue
            dep_rows = s.execute(
                select(models.TaskDependency.depends_on_id).where(
                    models.TaskDependency.task_id == tid
                )
            ).all()
            dep_ids = [r[0] for r in dep_rows]
            if not dep_ids:
                log.warning(
                    "planner integrate task %s has no resolvable deps; "
                    "leaving prompt placeholder",
                    title,
                )
                continue
            dep_branches = [worktrees.branch_name_for(did) for did in dep_ids]
            t = s.get(models.Task, tid)
            if t is None:
                continue
            t.prompt = integration.build_planner_integrate_prompt(
                dep_branches, target_branch, create_target=True
            )

        # Promote drafts whose deps are already done (or absent) to 'ready' so
        # autorun picks them up without a manual click.
        for tid in created_ids:
            t = s.get(models.Task, tid)
            if t is None:
                continue
            dep_rows = s.execute(
                select(models.TaskDependency.depends_on_id).where(
                    models.TaskDependency.task_id == tid
                )
            ).all()
            if not dep_rows:
                t.status = "ready"
                continue
            dep_ids = [r[0] for r in dep_rows]
            statuses = s.execute(
                select(models.Task.status).where(models.Task.id.in_(dep_ids))
            ).all()
            if all(r[0] == "done" for r in statuses):
                t.status = "ready"
    return created_ids


def _integrate_prompt_placeholder(target_branch: str) -> str:
    return (
        f"(planner integrate — will merge dep task branches into "
        f"'{target_branch}' once deps resolve)"
    )
