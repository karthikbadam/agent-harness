# Tier 2 — Native Loop Task (F4), with F5/F8 folded in

Branch: `feat/agent-loop`. Follows Tier 1 (F1 heartbeat watchdog, F2 per-task
idle timeout, F3 artifact store — all landed). This plan makes the autoresearch
loop a **first-class harness primitive** instead of one long `claude -p` turn
that says "NEVER STOP".

## The problem with the current approach

The dry run works, but the whole loop runs inside **one execute job / one Claude
turn**. Consequences:

1. **Invisible iterations.** The project's task list shows a single
   "autoresearch" task. Every experiment, keep/discard, and metric bump is
   buried inside one turn's transcript. You can't scan the series in the tasks
   list or click into experiment #7.
2. **Context ceiling (F8).** A single turn accumulates tool_use/tool_result
   chatter every experiment. Around a few dozen iterations it hits the context
   limit and the turn dies. The one-turn loop cannot run "for 12 hours".
3. **Caps are external (F5).** Stopping after N experiments / at a target metric
   / under a token budget has no home — today it'd be a bolted-on watcher.

## The design: the loop is the planner's iterative sibling — same module, not a new service

The planner is **already** "turn a finished job's output into follow-on tasks."
It owns the assistant-text reader (`_all_assistant_text`), the JSON extractor
(`_extract_json_array`), the `Task()` construction (`_insert_drafts`), and it
hands new tasks to the `autorun_ids` inline-kickoff path
(`task_runner.on_job_finalized` → `jobs.py:481`). A loop needs all four. So we
**extend the planner**, we do not add a `loop_runner` service.

The two are the same capability in two shapes:

- **planner = decompose** — one job → *many* independent child tasks (a DAG),
  auto-run in parallel. (Today.)
- **loop = iterate** — each job → *at most one* successor task, with carried
  state and a stop condition. (New.)

Shared core: read the job's output → make task(s) → return ids for inline
kickoff. Two thin policies on top: DAG-fanout (existing) vs.
iterate-with-state-and-caps (new). The single orchestration seam stays
`task_runner.on_job_finalized`, which already dispatches by `job.kind` /
`task.mode`; we add one branch for "the finished task is a loop iteration."

```
Task "tool-picker autoresearch"   mode='loop'   ← the controller row in the UI
   │   (runs NO Claude turn itself; holds spec + state, groups the children)
   ├─ iter 1   (child task, source='loop', one experiment)  → Outcome{metric,kept} + progress.png
   ├─ iter 2   (spawned after iter 1 finalizes, prompt built from carried state)
   ├─ iter 3
   └─ …        (until a stop condition: max_iters / target / budget / cancel)
```

Each iteration is its **own task** with its own job, transcript, git checkpoint
(Outcome), and artifacts — exactly the "full series of changes and progress
updates in the tasks list" you asked for. And because each iteration is a
**fresh Claude turn**, context never accumulates across iterations — this is what
neutralizes F8 (the controller's carried state IS the compaction).

### Division of labour

- **The agent (each iteration task)** does the science, unchanged from
  `program.md`: form ONE hypothesis → edit `train.py` → commit → `uv run
  train.py` → eval → keep (advance branch) or discard (`git reset --hard`) →
  append `results.tsv` → regen `progress.png` → register artifacts → print a
  structured result line.
- **The controller (`loop_runner`)** is thin: read the finished iteration's
  result, update loop state, check stop conditions, and either spawn iteration
  N+1 (with a freshly-built prompt) or finish the loop.

Keep/discard persists across iterations through **git branch state**: every
iteration runs at the **project root** on one shared branch
(`autoresearch/<tag>`), so the branch HEAD is always "best so far". (This is why
loop iterations run at project root with no per-iteration worktree — the shared
branch is the experiment log, same as Karpathy's loop.)

## What's new (backend)

### 1. Models (`models.py` + `db.py` migration)

- `Task.parent_task_id: str | None` (FK `tasks.id`) — groups loop iteration
  children under the loop parent. Generally useful beyond loops.
- `Task.loop_spec: JSON | None` — on the parent only. The loop definition:
  ```jsonc
  {
    "metric_name": "val_acc",
    "direction": "maximize",          // or "minimize"
    "iter_prompt_template": "...",     // built from program.md + state slots
    "max_iterations": 30,              // F5 cap
    "target_metric": 0.97,             // F5 stop-when-reached (optional)
    "max_cost_usd": null,              // F5 token/$ budget (optional)
    "max_wall_clock_s": null,          // F5 time budget (optional)
    "max_consecutive_failures": 3      // safety stop
  }
  ```
- `Task.loop_state: JSON | None` — on the parent, updated each iteration:
  `{ "iteration": 7, "best_metric": 0.945, "best_commit": "285b404",
     "consecutive_failures": 0, "started_at": "...", "spent_usd": 0.12 }`.
- `Outcome.meta: JSON` — per-iteration result carried on the git-checkpoint row:
  `{ "iteration": 7, "metric": 0.945, "kept": true, "description": "...",
     "citation": "Oh et al. 2018" }`. Makes outcomes rich enough for the UI
  series without a new table.

Migration via the existing `db.py` additions list (the F2 pattern):
`("tasks","parent_task_id","TEXT")`, `("tasks","loop_spec","JSON")`,
`("tasks","loop_state","JSON")`, `("outcomes","meta","JSON")`.

### 2. New task mode `loop` + kickoff special-case (`task_runner.py`)

- `Task.mode` gains `"loop"`. `kickoff_first_phase` special-cases it: the loop
  parent runs **no Claude job**. Instead it calls `planner.start_loop(parent_id)`,
  which creates iteration child #1 and returns its id to kick. The parent flips
  to `status='running'` and stays there until the loop ends.
- Iteration children are created as `mode='one_shot'` (project root, no
  worktree, agent self-commits on the shared branch), `source='loop'`,
  `parent_task_id=<loop parent>`, with `idle_timeout_seconds=0` inherited from
  the parent (long training turns).

### 3. Extend the planner (`planner.py`) — the iterate strategy

No new service. The planner gains two functions alongside its existing
decompose helpers, and `task_runner.on_job_finalized` gets one new branch:
when the finished execute job's task has `source='loop'`, after recording the
normal execute `Outcome`, call `planner.advance_loop(...)`.

- `start_loop(parent_task_id) -> child_id` — read `loop_spec`, init
  `loop_state`, insert iteration child #1 (reusing the same `Task()` insertion
  path as `_insert_drafts`), return its id. Called from `kickoff_first_phase`.
- `advance_loop(parent_task, finished_iter_task, log_dir, job_cost) ->
  (result: dict, next_child_id: str | None, stop_reason: str | None)`:
  1. Parse the structured result (§4) from the iteration's `assistant_text`;
     backstop from `results.tsv` last row + `git HEAD` if the line is missing.
  2. Update the parent `loop_state` (iteration++, best_metric/commit,
     spent_usd += job cost, consecutive_failures).
  3. Evaluate stop conditions (§5). If continue → build iteration N+1's prompt
     from `iter_prompt_template` + state, insert the child, return its id (the
     caller appends it to `autorun_ids` for the existing inline-kickoff path).
     If stop → return `next_child_id=None` + a `stop_reason`; the caller marks
     the parent `done` and writes a final summary Outcome + `report` artifact.

  The caller (`task_runner`) keeps the DB writes it already owns — the iteration
  `Outcome` (and now its `meta`, set from the returned `result`) — so all Outcome
  writes stay in one place; the planner owns task-generation + `loop_state`, the
  same division it already has for decompose.

- The "tasks generated on the go": each iteration's prompt is **synthesized at
  spawn time** from carried state, e.g. *"You are iteration N. Best so far:
  val_acc=0.945 at commit 285b404. results.tsv lists prior experiments — pick a
  NEW idea. Run one experiment per program.md, keep/discard via git, register
  artifacts, then print `LOOP_RESULT: {json}`."*

### 4. Structured iteration result (generalize the planner's parser)

The planner already parses JSON from assistant_text (`_extract_json_array`). Add
a sibling `_extract_json_object` and a convention: each iteration ends with
`LOOP_RESULT: {"metric": 0.945, "kept": true, "description": "...",
"citation": "..."}`. Backstop: if absent, read `results.tsv` last data row and
`git rev-parse HEAD`. (v2 could promote this to a real MCP structured-output
tool; the sentinel matches the existing planner pattern and ships now.)

### 5. F5 stop conditions (native, a small pure helper in `planner.py`)

Checked after every iteration, in priority order:
`cancel` (manual) → `max_consecutive_failures` → `target_metric` reached →
`max_iterations` → `max_cost_usd` → `max_wall_clock_s`. On stop, parent → `done`
with a final summary. This is exactly the "stop after N experiments" ask, now a
first-class field instead of an external watcher.

### 6. API (`routes/`)

- `POST /api/projects/{pid}/loops` — create a loop. Body: `{title, program_ref
  or prompt, metric_name, direction, max_iterations, target_metric?,
  max_cost_usd?, max_wall_clock_s?}`. Creates the `mode='loop'` parent
  (`status='ready'`), returns it.
- `POST /api/tasks/{id}/run` — already kicks ready tasks; for a loop parent it
  routes through the `kickoff_first_phase` special-case → `loop_runner.start`.
- `GET /api/tasks/{parent}/iterations` — iteration children + their
  `Outcome.meta` (metric, kept) for the UI series, newest first.
- `POST /api/tasks/{parent}/cancel` — extend the existing cancel to stop the
  running child and mark the parent + loop `canceled`.
- Reuse the Tier-1 artifacts endpoints unchanged.

## What's new (frontend) — makes it visible on your phone

Two pieces, both currently missing (`grep artifact web/src` → nothing):

1. **Artifact rendering under task detail.** An "Artifacts" section: `kind=graph`
   PNG rendered inline (this is the `progress.png` you watch), `table`/`report`/
   others as labelled download links. Polls the artifacts endpoint so the graph
   refreshes each iteration. Required for F3 to actually be usable on the phone.
2. **Loop view.** The loop parent renders as a card with the iteration series —
   a sparkline of `metric` per iteration (kept=green, discarded=grey, running
   best line), and a list of child task rows (iteration #, metric, keep/discard
   badge, status) each linking to its job/outcome/artifacts. This is the
   "inspect the full series" surface.

## The other Tier 2/3 features — handled or consciously skipped

| Feature | Disposition |
|---|---|
| **F4 loop-mode** | **This plan.** |
| **F5 budget caps** | **Folded into `loop_spec`** (max_iterations / target_metric / max_cost_usd / max_wall_clock). No separate feature. |
| **F8 auto-compaction** | **Largely obviated.** Fresh Claude turn per iteration means cross-iteration context never accumulates; the controller's `loop_state` is the hand-off. Residual (one very long single iteration) is bounded by one experiment and covered by F1. |
| **F6 workspace files** | **Not needed for loops.** Iterations run at project root, sharing the venv, `~/.cache` dataset, `results.tsv`, and git branch. (Worktree-per-iteration would actually break keep/discard, so project-root is correct, not a compromise.) |
| **F7 per-kind concurrency** | **Orthogonal / skipped.** Loop iterations are inherently sequential (N+1 depends on N's keep/discard decision). Cross-project concurrency still uses the existing `max_concurrent_jobs`. |

## Tests

- `planner.advance_loop` unit: a finished iteration outcome → spawns the next;
  respects each stop condition (max_iters, target, budget, consec. failures,
  cancel); `start_loop` inserts iteration #1.
- Structured-result parse: `LOOP_RESULT:` happy path + missing-line backstop
  (reads `results.tsv` + HEAD).
- Integration with `fake_claude.sh`: a fixture that emits a `LOOP_RESULT` line
  and appends `results.tsv`; run a 3-iteration loop, assert 3 child tasks under
  the parent, parent `done`, 3 outcomes with `meta.metric`.
- API: create loop → run → `GET /iterations` shape; cancel stops the loop.
- Migration: fresh DB + upgrade-in-place both yield the new columns.

## Phasing (each phase = its own commits per CLAUDE.md)

- **Phase A — backend core.** Models + migration; `mode='loop'`; planner
  `start_loop`/`advance_loop` + `on_job_finalized` loop branch; kickoff
  special-case; F5 stop conditions; structured parse; the three API routes.
  Behind tests. (`add: loop task model`, `add: loop generation in planner`,
  `add: loop api`, …)
- **Phase B — frontend.** Artifact rendering + loop series view.
- **Phase C — migrate the live run.** Port `tool-picker-rl` from the
  one-big-turn task to a native loop; verify the series + graph on the phone.

## Open decisions (for sign-off)

1. **Controller shape** — parent runs no turn and every iteration is a uniform
   child (recommended, matches planner & UI grouping) vs. parent *is* iteration
   1 with children 2..N (less symmetric). → **recommend uniform children.**
2. **Result channel** — `LOOP_RESULT:` sentinel + `results.tsv` backstop
   (recommended, ships now) vs. a real MCP structured-output tool (cleaner, more
   infra). → **recommend sentinel for v1.**
3. **Scope now** — build Phase A+B+C this branch (recommended, given "tackle the
   other updates too") vs. backend-only (Phase A) first, frontend later.
