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
import subprocess
from datetime import datetime, timezone

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

- `plan_then_execute` (default) — the agent does a read-only planning turn
  first, writes a mini-plan, and waits for the user to ack before editing.
  The execute turn then runs in a fresh worktree on a `task/<id>` branch and
  commits the work. Use this for non-trivial code changes where a quick
  review of the agent's intent before files change is worth the extra step.
- `execute_only` — same worktree + branch model, but skip the plan/ack
  handshake. Good when the task is narrow and well-specified enough that a
  planning turn would just be ceremony — "Add a `hubbleTime(H0)` helper in
  src/physics/cosmology.ts", "Rename FooBar → FizzBuzz across src/components".
- `research` — answer-only task. Runs at the project root with no worktree
  and makes no commits — the final assistant message is the deliverable shown
  to the user as the answer. Use when the ask is a question, an explanation,
  or any non-code investigation. Anything the agent writes to disk in a
  research task is lost; the answer must live in the message.

## Question-shaped asks

If the user's ask is a question or research request (e.g. "what does this
repo do?", "explain how X works"), the right shape is exactly one `research`
task whose prompt is the question itself. One task owns the answer; the user
clicks into it to read the reply.

## Always emit at least one task

The project page expects a task to track every ask, so the user has a
visible row to follow and a place where the result will land. If you cannot
decompose further, emit one task whose prompt is the user's ask verbatim
rather than returning an empty list.

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
prompt automatically — leave `prompt` off integrate entries; it's ignored.
Downstream tasks that depend on this integrate task will start their
worktrees from `target_branch`, so they see all the merged work.

Use waves only when the foundation is actually shared. If the ask is small
or the tasks are truly independent (disjoint files), one or a few siblings
with no deps is best — the harness will run them in parallel, which is
faster than serializing through a wave you didn't need.

## Task-shaping rules

- **1 to 16 tasks.** A handful of concrete, parallel tasks lands faster and
  is easier to review than a long serial chain. If you find yourself drafting
  more than ~16, the scopes are probably too small — collapse related ones.
- **Maximize parallelism.** Tasks that touch disjoint files MUST have empty
  `depends_on_titles` so the harness can run them concurrently. Only add a
  dep when the later task genuinely cannot succeed before the earlier one
  lands — e.g. it imports a function the earlier task introduces. A spurious
  dep serializes work that could have run in parallel.
- **If many files share one foundation change, plan a wave.** Foundation
  task(s) → integrate → dependent tasks. The integrate merges the foundation
  into a shared branch so the dependents actually see it; without it,
  sibling branches are invisible to each other.
- **Each `prompt` names specific files/paths and the concrete change.**
  "Update PlotSection.tsx to render plot and controls in a responsive Stack
  (column on base, row on md+)" gives the agent enough to execute against;
  "implement the layout" is a goal, not a task — the agent will guess at
  the scope and likely drift.
- **No separate "audit" task.** Each task's own session reads the files it
  needs before editing; an upfront audit task just duplicates that work and
  doesn't share its findings with the siblings anyway (different branches).
- **No final "verify" or "run tests" task.** The harness runs verification
  on integration, and most tasks should commit a passing change anyway.
  Exception: if the ask itself is to add tests or verify something, that's
  the task — not an extra step bolted onto another task.
- **Each task should be small enough to checkpoint with one git commit.**
  That's the unit the harness records as an outcome and what makes a failed
  task safe to retry. A task that needs three commits is two tasks too few.

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


# ============================ Loop (iterate) strategy ======================= #
#
# The planner's second task-generation strategy. Where the decompose path turns
# one job into a parallel DAG, the iterate path turns each finished iteration
# into (at most) one successor, carrying state and honoring stop conditions —
# an autoresearch loop. ``start_loop`` seeds iteration #1; ``advance_loop`` is
# called by ``task_runner.on_job_finalized`` when a ``source='loop'`` iteration
# finishes, and decides whether to spawn the next one or end the loop.


def _default_loop_state() -> dict:
    return {
        "iteration": 0,  # count of COMPLETED iterations
        "best_metric": None,
        "best_commit": None,
        "consecutive_failures": 0,
        # Consecutive successful-but-not-improving iterations. Drives
        # stuck-detection: when it reaches spec.stuck_after the next iteration
        # is a "rethink" (meta) iteration.
        "non_improving_streak": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "spent_usd": 0.0,
    }


def _extract_json_object(text: str) -> dict | None:
    """Find the iteration's ``LOOP_RESULT: { ... }`` line and parse the object.

    Tolerant of the object appearing without the prefix, in a fence, or with
    surrounding prose. Returns None if nothing parseable is found.
    """
    if not text:
        return None
    # Prefer an explicit LOOP_RESULT: marker, last occurrence wins (the agent
    # may print intermediate ones).
    marks = list(re.finditer(r"LOOP_RESULT:\s*(\{.*?\})", text, re.DOTALL))
    for m in reversed(marks):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            continue
    # Fenced object fallback.
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fence:
        try:
            obj = json.loads(fence.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            pass
    return None


def _git_head(cwd: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _results_tsv_last_metric(cwd: str, metric_name: str) -> float | None:
    """Backstop metric source: read the last data row of ``results.tsv`` in the
    loop's working dir and pull the ``metric_name`` column. Returns None if the
    file/column/row is missing or unparseable."""
    p = Path(cwd) / "results.tsv"
    if not p.is_file():
        return None
    try:
        lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return None
    if len(lines) < 2:
        return None
    header = lines[0].split("\t")
    if metric_name not in header:
        return None
    col = header.index(metric_name)
    last = lines[-1].split("\t")
    if col >= len(last):
        return None
    try:
        return float(last[col])
    except ValueError:
        return None


def _parse_iteration_result(
    log_dir: Path | None, cwd: str, spec: dict, iteration: int
) -> dict:
    """Build the iteration result dict from the agent's structured output, with
    a ``results.tsv`` + ``git HEAD`` backstop. Always returns a dict with at
    least ``iteration``; ``metric`` may be None if nothing was parseable."""
    metric_name = spec.get("metric_name", "metric")
    obj = _extract_json_object(_all_assistant_text(log_dir)) if log_dir else None
    metric: float | None = None
    kept: bool | None = None
    description: str | None = None
    citation: str | None = None
    if obj is not None:
        m = obj.get("metric")
        if isinstance(m, (int, float)):
            metric = float(m)
        if isinstance(obj.get("kept"), bool):
            kept = obj["kept"]
        if isinstance(obj.get("description"), str):
            description = obj["description"][:500]
        if isinstance(obj.get("citation"), str):
            citation = obj["citation"][:300]
    if metric is None:
        metric = _results_tsv_last_metric(cwd, metric_name)
    commit = _git_head(cwd)
    return {
        "iteration": iteration,
        "metric": metric,
        "kept": kept,
        "description": description,
        "citation": citation,
        "commit": commit,
    }


def _is_improvement(direction: str, new: float, best: float | None) -> bool:
    if best is None:
        return True
    return new > best if direction == "maximize" else new < best


def _loop_stop_reason(spec: dict, state: dict, job_status: str) -> str | None:
    """Return the first triggered stop condition, or None to keep looping.

    Priority: consecutive failures → target reached → max iterations →
    cost budget → wall-clock budget.
    """
    if state["consecutive_failures"] >= spec.get("max_consecutive_failures", 3):
        return "max-consecutive-failures"
    tgt = spec.get("target_metric")
    best = state.get("best_metric")
    if tgt is not None and best is not None:
        direction = spec.get("direction", "maximize")
        hit = best >= tgt if direction == "maximize" else best <= tgt
        if hit:
            return "target-reached"
    if state["iteration"] >= spec.get("max_iterations", 50):
        return "max-iterations"
    max_cost = spec.get("max_cost_usd")
    if max_cost is not None and state.get("spent_usd", 0.0) >= max_cost:
        return "max-cost"
    max_wall = spec.get("max_wall_clock_s")
    if max_wall is not None:
        try:
            started = datetime.fromisoformat(state["started_at"])
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        except Exception:  # noqa: BLE001
            elapsed = 0.0
        if elapsed >= max_wall:
            return "max-wall-clock"
    return None


def _build_iteration_prompt(
    parent: models.Task, iteration: int, state: dict, spec: dict, meta: bool = False
) -> str:
    """Synthesize iteration N's prompt from the standing instruction
    (``parent.prompt`` — the program.md body) plus the carried state header.
    This is the "task generated on the go": the prompt evolves with the run.

    When ``meta`` is set (stuck-detection fired), the header tells the agent to
    step back, review the whole run, and try a fundamentally different
    direction instead of another local variation."""
    metric_name = spec.get("metric_name", "metric")
    direction = spec.get("direction", "maximize")
    best = state.get("best_metric")
    best_commit = state.get("best_commit")
    if meta:
        streak = spec.get("stuck_after", "several")
        standing = (
            f"⟲ RETHINK ITERATION {iteration}. The loop has plateaued — about "
            f"{streak} iterations in a row failed to improve {metric_name} "
            f"(best is {best}). STOP iterating on recent variations. Read ALL of "
            f"results.tsv and the git log, and explicitly reason about WHY "
            f"progress stalled: which families of ideas have been exhausted, "
            f"what assumption might be wrong. Then try ONE fundamentally "
            f"different approach this iteration — a different mechanism, not a "
            f"tweak of the last few. Be bold; this is the move that breaks the "
            f"plateau."
        )
    elif best is None:
        standing = (
            f"This is iteration {iteration} — the FIRST one. Establish the "
            f"baseline: run the experiment as-is and record {metric_name}."
        )
    else:
        standing = (
            f"You are iteration {iteration}. Best {metric_name} so far: "
            f"{best} (at commit {best_commit}). The goal is to {direction} "
            f"{metric_name}. Read results.tsv for what's already been tried and "
            f"pick a NEW idea — do not repeat a prior experiment."
        )
    contract = (
        "Run exactly ONE experiment this iteration, keep/discard via git as the "
        "standing instructions describe, regenerate the progress graph, and "
        "register artifacts. When finished, print EXACTLY one line:\n"
        'LOOP_RESULT: {"metric": <number>, "kept": <true|false>, '
        '"description": "<≤10 words>", "citation": "<paper or empty>"}'
    )
    # Inject the harness coordinates so the iteration's artifact curls target the
    # LOOP PARENT, not this iteration child. Registering progress.png/results.tsv
    # against the parent (F3 re-registers same-name in place) means the parent
    # task always shows one current graph across the whole run.
    coords = _harness_coords_block(parent.id)
    return f"{coords}\n\n{standing}\n\n{contract}\n\n---\n\n{parent.prompt}"


def _harness_coords_block(parent_task_id: str) -> str:
    """Env exports the iteration agent should `export` before registering
    artifacts. AH_TASK_ID points at the loop parent so the graph lands there."""
    from ..config import get_settings

    s = get_settings()
    token = s.auth_token or ""
    base = "http://127.0.0.1:8765"
    return (
        "Before registering any artifact, export these (artifacts must attach to "
        "the loop parent so its page shows one current graph):\n"
        f"  export AH_URL={base}\n"
        f"  export AH_TOKEN={token}\n"
        f"  export AH_TASK_ID={parent_task_id}"
    )


def _make_iteration_task(
    s, parent: models.Task, iteration: int, state: dict, spec: dict,
    meta: bool = False,
) -> models.Task:
    """Insert a single loop iteration child task (ready to run).

    ``meta`` marks a stuck-detection "rethink" iteration: a different prompt
    and, if ``spec.escalate_model`` is set, a stronger model for that run.
    """
    model_override = spec.get("escalate_model") if meta else None
    child = models.Task(
        project_id=parent.project_id,
        # Short, self-describing title. Retitled with the experiment's
        # description once it reports (see task_runner loop branch), so the
        # list reads like "Iteration 11 · Adam lr 0.01" rather than echoing
        # the parent's full name on every row.
        title=f"Iteration {iteration}{' · rethink' if meta else ''}",
        prompt=_build_iteration_prompt(parent, iteration, state, spec, meta=meta),
        status="ready",
        source="loop",
        order_idx=iteration,
        mode="one_shot",  # project root, no worktree, agent self-commits
        parent_task_id=parent.id,
        idle_timeout_seconds=parent.idle_timeout_seconds,
        model_override=model_override,
    )
    s.add(child)
    s.flush()
    return child


def start_loop(parent_task_id: str) -> str | None:
    """Seed a loop: init the parent's ``loop_state``, flip it to ``running``,
    and insert iteration #1. Returns the child task id to kick, or None if the
    parent is missing or not a runnable loop.

    Called from ``task_runner.kickoff_first_phase`` for ``mode='loop'`` tasks.
    """
    with session_scope() as s:
        parent = s.get(models.Task, parent_task_id)
        if parent is None or parent.mode != "loop":
            return None
        spec = parent.loop_spec or {}
        state = _default_loop_state()
        next_iter = state["iteration"] + 1  # 1
        child = _make_iteration_task(s, parent, next_iter, state, spec)
        parent.loop_state = state
        parent.status = "running"
        parent.phase = "executing"
        return child.id


def advance_loop(
    parent_task_id: str,
    job_status: str,
    cwd: str,
    cost_usd: float | None,
    log_dir: Path | None,
) -> tuple[dict, str | None, str | None]:
    """Process a finished loop iteration and decide what's next.

    Returns ``(result, next_child_id, stop_reason)``:
      - ``result``: the parsed iteration result, to be stored on the iteration's
        ``Outcome.meta`` by the caller.
      - ``next_child_id``: the id of the next iteration task (caller appends it
        to ``autorun_ids`` for inline kickoff), or None if the loop is ending.
      - ``stop_reason``: non-None iff the loop is ending (caller marks the
        parent done with a summary).

    Owns the ``loop_state`` update and the next-task insertion in its own
    session — mirroring how the decompose path owns ``_insert_drafts``. The
    caller keeps the iteration ``Outcome`` write.
    """
    with session_scope() as s:
        parent = s.get(models.Task, parent_task_id)
        if parent is None:
            return {}, None, "parent-missing"
        # A cancel flips the parent off 'running'; respect it and stop.
        if parent.status != "running":
            return {}, None, "canceled"
        spec = parent.loop_spec or {}
        state = dict(parent.loop_state or _default_loop_state())
        iteration = state["iteration"] + 1
        result = _parse_iteration_result(log_dir, cwd, spec, iteration)

        # Update carried state.
        state["iteration"] = iteration
        state["spent_usd"] = round(
            float(state.get("spent_usd", 0.0)) + float(cost_usd or 0.0), 6
        )
        metric = result.get("metric")
        succeeded = job_status == "done" and metric is not None
        if succeeded:
            state["consecutive_failures"] = 0
            if _is_improvement(
                spec.get("direction", "maximize"), metric, state.get("best_metric")
            ):
                state["best_metric"] = metric
                state["best_commit"] = result.get("commit")
                state["non_improving_streak"] = 0
            else:
                state["non_improving_streak"] = (
                    state.get("non_improving_streak", 0) + 1
                )
        else:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1

        stop_reason = _loop_stop_reason(spec, state, job_status)
        parent.loop_state = state  # reassign so SQLAlchemy flags the JSON dirty

        if stop_reason is not None:
            parent.status = "done"
            parent.phase = "done"
            return result, None, stop_reason

        # Stuck-detection: if we've gone stuck_after iterations without
        # improving, make the NEXT iteration a "rethink" and reset the streak
        # so the new direction gets a fresh window before we escalate again.
        stuck_after = spec.get("stuck_after")
        meta_next = bool(stuck_after) and state["non_improving_streak"] >= stuck_after
        if meta_next:
            state["non_improving_streak"] = 0
            parent.loop_state = state

        child = _make_iteration_task(
            s, parent, iteration + 1, state, spec, meta=meta_next
        )
        return result, child.id, None
