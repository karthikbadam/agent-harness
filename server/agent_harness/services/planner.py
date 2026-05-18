"""Planner: turn a high-level ask into a list of draft tasks.

Implementation strategy: spawn a one-off job in the project using `JobManager`,
prompt claude to emit a strict JSON task list, then parse the assistant_text
events from the resulting log. Tasks are inserted with `source='planner'` and
`status='pending'` so the user can edit/confirm them before running.

We piggy-back on the regular job machinery so the planning conversation is
visible in the Jobs UI (and is captured under `~/.agent-harness/logs/jobs/<id>/`
just like any other job).
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from pathlib import Path

from .. import models
from ..db import session_scope
from ..jobs import JobManager

log = logging.getLogger(__name__)


PLANNER_INSTRUCTIONS = """\
You are a planning assistant for a software repo. The user will describe a
high-level ask; your job is to produce a concrete, parallel-friendly task
list — but FIRST you must understand the codebase enough to plan against
reality, not guesses.

## Phase 1 — Audit (do this before drafting tasks)

Use your read-only tools (Glob, Grep, Read) to:
1. Locate the files, components, or modules the ask touches.
2. Note shared wrappers / utilities / patterns that multiple files use.
3. Identify which files are truly independent (no shared edits) and which
   share a contract (e.g. a wrapper component used by many call sites).
4. Spot existing tests, type checks, or build scripts that should pass
   afterwards.

Do not modify anything in this phase. The planner job is read-only.

## How the harness runs your plan (read this before drafting tasks)

Each task you emit runs as its own git worktree under a `task/<id>` branch.
The starting tree for a task depends on its dependencies:

- A task with **no dependencies** branches from the project's main HEAD —
  it sees the repo as it is today.
- A task whose dependency is an **integrate** task (see below) branches
  from the integrate's target branch — it sees all the merged work.
- A task with **a single regular dependency** branches from that dep's
  `task/<id>` tip — it sees that one task's files.
- A task with **multiple regular dependencies** branches from main —
  the harness can't safely auto-merge multiple `task/<id>` branches for
  you, so the dependent task won't see its predecessors' files. Don't
  rely on this shape.

Because each task lives on its own branch, sibling tasks (run in
parallel) don't see each other's files. If multiple downstream tasks
need to build on the same foundation, plan the *wave shape*:

```
[T1, T2, T3]   ← siblings, foundation work (no deps)
     ↓
 [Integrate]   ← kind: "integrate", merges T1+T2+T3 into a shared branch
     ↓
[T4, T5, T6]   ← siblings, build on integrated foundation
     ↓
 [Integrate]   ← optional final integrate, if you want one branch out
```

Use this only when it's actually needed. If the ask is small and
localized, one task is enough. If tasks are truly independent, sibling
shape with no deps is best — the harness will run them in parallel.

## Phase 2 — Output: a short audit summary, then the JSON task list

First, write a brief (3–10 line) findings paragraph in prose. This is the
plan the user reads in the UI to understand WHY you decomposed the way you
did — name the files you found, the shared pieces, and the parallel
structure you chose.

Then emit the task list as a strict JSON array (Markdown fence allowed but
not required), with these fields:

  [
    {
      "title": "<short imperative title, <= 80 chars>",
      "prompt": "<the exact prompt that should be sent to claude when this task runs — name specific files and the concrete change>",
      "depends_on_titles": ["<title of an earlier task>", ...],
      "kind": "task" | "integrate",       // optional, defaults to "task"
      "mode": "plan_then_execute" | "execute_only"  // optional, defaults to "plan_then_execute"
    },
    ...
  ]

### Mode selection

Default `plan_then_execute` runs a read-only planning turn before the
agent edits files. The planning turn writes a mini-plan; the execute
turn then runs in a fresh worktree on a `task/<id>` branch and commits
the work. Use it for non-trivial tasks where the agent benefits from
articulating its approach before touching files.

Use `"mode": "execute_only"` for narrow, well-specified tasks where a
planning turn would just be ceremony. The task still gets its own
worktree + branch (so it can be integrated), the agent just skips the
plan/ack handshake. Good candidates:

- "Add a `hubbleTime(H0)` helper in src/physics/cosmology.ts" — narrow,
  one or two files, prompt describes the change exactly.
- "Rename FooBar → FizzBuzz across src/components" — mechanical.
- A single-step change you can describe in one sentence.

When in doubt, default `plan_then_execute`.

### Integrate tasks

An entry with `"kind": "integrate"` is a merge step the harness runs for
you. The harness builds the prompt automatically from the dependencies;
you do not write a prompt for it. Required fields:

  {
    "title": "Integrate foundation",
    "kind": "integrate",
    "depends_on_titles": [<every task whose work should be merged>],
    "target_branch": "<branch name to create and merge into>"
  }

- `target_branch` MUST be a fresh branch name like
  `feat/<short-slug>-foundation` — the integrate task creates it from
  the current HEAD and merges every dep's `task/<id>` branch into it.
- Downstream tasks depending on this integrate task will start their
  worktrees from `target_branch`, so they see all the merged work.
- Do not include `prompt` on integrate entries; it's ignored.

## Task-shaping rules

- 1 to 16 tasks. Concrete and parallel beats long and serial.
- **Maximize parallelism**: tasks that touch disjoint files MUST have empty
  `depends_on_titles`. Only add a dep when the later task genuinely cannot
  succeed before the earlier one lands (e.g. it consumes a function the
  earlier task introduces).
- If many files share one foundation change, plan: foundation task(s) →
  integrate → dependent tasks. The integrate makes the foundation visible
  to the dependents.
- Each `prompt` must name specific files/paths and the concrete change.
  "Update PlotSection.tsx to render plot and controls in a responsive
  Stack (column on base, row on md+)" — not "implement the layout".
- Do not include a separate "audit" task. Your Phase 1 above replaces that;
  the agent that runs each task already has read access to the repo.
- Do not include a final "verify" or "run tests" task. The harness checks
  on integration. (Exception: if the ask itself is a test/verify task.)
- Tasks should each be small enough to checkpoint with one git commit.

The user's ask follows.
"""


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


async def plan(
    project_id: str,
    ask: str,
    job_manager: JobManager,
    log_root: Path,
) -> tuple[list[str], str | None, str | None]:
    """Run the planner, insert draft tasks, and auto-kick any that landed
    ``ready`` (i.e. have no unsatisfied deps).

    Returns (task_ids, raw_output_or_None, error_or_None). On JSON parse
    failure the raw output is returned so the caller can show it to the user;
    no tasks are inserted in that case.
    """
    prompt = PLANNER_INSTRUCTIONS + "\n\nAsk:\n" + ask
    title = f"[plan] {ask[:60]}"
    try:
        jid = job_manager.create_job(project_id, prompt, title=title)
    except ValueError as e:
        return [], None, f"could not create planner job: {e}"
    await job_manager.start(jid)
    await job_manager.wait(jid)

    log_dir = log_root / "jobs" / jid
    raw = _all_assistant_text(log_dir)
    parsed = _extract_json_array(raw)
    if parsed is None:
        return [], raw, "could not parse a JSON task array from planner output"

    task_ids = _insert_drafts(project_id, parsed)

    # Auto-kick: planner drafts that landed ``ready`` (no deps) run immediately.
    # The user already approved the ask by submitting it; gating each task on
    # a manual Run click adds friction without value. Tasks still in ``pending``
    # (waiting on a predecessor) auto-kick later via on_job_finalized.
    from . import task_runner

    for tid in task_ids:
        try:
            await task_runner.kickoff_first_phase(tid, job_manager)
        except Exception:  # noqa: BLE001
            log.exception("planner autorun failed for task %s", tid)

    return task_ids, raw, None


def _insert_drafts(project_id: str, parsed: list[dict]) -> list[str]:
    """Insert planner-drafted tasks, resolving depends_on_titles to ids.

    Drafts land as ``source='planner'``. Tasks with no deps are inserted as
    ``ready`` so the user can run them immediately; tasks with deps stay
    ``pending`` until their predecessors finish. Skips entries missing a
    title or prompt.

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
            if mode not in ("plan_then_execute", "execute_only", "one_shot"):
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
        # the user doesn't have to PATCH each one to confirm.
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
