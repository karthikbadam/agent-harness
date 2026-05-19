# Frontend v2: plan-then-execute, worktrees, integration, driver

What changed in the API surface and what the UI needs to show or let the
user do. Pair with the OpenAPI dump (`agent-harness gen-openapi`) for exact
field types — this doc is concepts + UX guidance.

The two big shifts:
1. **Multi-phase tasks.** A task is now `plan_then_execute` by default: it
   produces a *plan* turn (parked waiting for human ack), then an *execute*
   turn in an isolated git worktree, then later an *integration* turn that
   merges its branch back. The lifecycle in §1 covers all of this.
2. **Per-project driver.** Each project has an `autopilot_mode` toggle.
   - Off → **copilot**: the harness computes next actions and the UI
     renders them as one-tap suggestions.
   - On → **autopilot**: an external `agent-harness-driver` process
     dispatches the same actions automatically. The UI shifts to an
     audit / escalation surface.

Contents:
1. [Task lifecycle](#1-task-lifecycle)
2. [DTO additions](#2-dto-additions)
3. [New endpoints](#3-new-endpoints)
4. [Per-view UI changes](#4-per-view-ui-changes)
5. [End-to-end flows](#5-end-to-end-flows)
6. [State / refresh strategy](#6-state--refresh-strategy)
7. [Gotchas](#7-gotchas)
8. [OpenAPI reference](#8-openapi-reference)

---

## 1. Task lifecycle

v1 was: `pending → ready → running → done|failed|canceled`. v2 keeps that
for the **task**, but adds a `phase` on the **job** that backs it, and
most tasks now go through two turns instead of one:

```
            Task.status                  Job.phase
            ───────────                  ─────────
created  →  pending                      (no job yet)
deps met →  ready                        (no job yet)
POST /run → running   ─ creates job ─→   planning
                                          │   turn 0 runs in project.path
                                          ▼
                       ←─── (parked) ─── awaiting_ack       ⬅ human (or driver) acks
followup →                                │   creates worktree,
                                          │   sets job.cwd_override
                                          ▼
                                         executing
                                          │   turn 1 runs in the worktree
                                          ▼
                                         done
            done           ←──────────────
            integration_status = pending                    ⬅ becomes integratable

POST /projects/{id}/integrate
            ──→ synthetic task created (status=ready, synthetic=true,
                mode=one_shot, deps = the input tasks)

POST /tasks/{synth}/run
            running                       integrating
                                          │
                                          ▼
            done                          done
            inputs.integration_status = integrated
```

Per-task opt-out: `mode='one_shot'` skips planning — a single turn runs in
`project.path` directly, with no worktree and no ack gate. Don't fan out
parallel one-shots against the same project; they'll fight over the shared
git index. Synthetic (integration) tasks are also `mode='one_shot'` and run
in `project.path` to perform the merge.

**Where the driver fits.** Each `⬅` step above is either the human clicking
a button (copilot) or `agent-harness-driver` reacting to an event
(autopilot). The lifecycle is the same; only who advances it differs.

---

## 2. DTO additions

### `ProjectOut`
```ts
{
  // … v1 fields …
  autopilot_mode: 'off' | 'on'
}
```

### `TaskOut`
```ts
{
  // … v1 fields …
  mode: 'plan_then_execute' | 'one_shot'
  worktree_path: string | null     // populated while executing/done
  worktree_branch: string | null   // `task/<task_id>` once a worktree exists
  integration_status: null | 'pending' | 'integrated' | 'conflict'
  synthetic: boolean                // true = system-generated integration task
  retries: number                   // bumped by POST /tasks/{id}/retry
  last_failed_at: string | null     // ISO timestamp; null if never failed
}
```

UI surfacing:
- **"plan"** badge when `mode === 'plan_then_execute'`,
  **"one-shot"** when `mode === 'one_shot' && !synthetic`,
  **"integration"** when `synthetic`.
- **Worktree link/copy-path** button when `worktree_path` is set.
- **Integration status pill** when `integration_status` is non-null:
  `pending` (grey), `integrated` (green), `conflict` (red, actionable).
- **Retry counter** small badge `↻ N` when `retries > 0`, with tooltip
  showing `last_failed_at`. Reach max (2) and the task should look
  visually distinct (escalated) — see `DriverNote(severity='escalate')`.

### `JobOut`
```ts
{
  // … v1 fields …
  phase: null | 'planning' | 'awaiting_ack' | 'executing' | 'integrating' | 'done'
}
```

UI surfacing:
- The job header should show **phase prominently** when non-null, not just
  the v1 `status`. A `status=done, phase=awaiting_ack` job is **not**
  finished from the user's POV — it's parked at the plan gate. Treat
  `phase === 'awaiting_ack'` as a first-class "needs you" state.
- Phase pill colors: `planning` (blue), `awaiting_ack` (amber, action),
  `executing` (blue), `integrating` (purple), `done` (green).
- `phase === null` means an ad-hoc job (v1 behavior) — fall back to `status`.

### `OutcomeOut`
```ts
{
  // … v1 fields …
  kind: 'plan' | 'execute' | 'integrate'
}
```

UI surfacing:
- Group outcomes by `kind` in the task detail view. The latest `kind='plan'`
  outcome's `summary` is the plan text — render it as the centerpiece
  when the job is in `awaiting_ack`.
- `kind='plan'` outcomes have `commit_sha=null` (planning doesn't commit).
  Don't link to a commit for those.
- `kind='integrate'` outcomes are produced by synthetic tasks.

### `DriverStateOut`, `SuggestedAction`, `DriverNoteOut`
```ts
DriverStateOut {
  mode: 'off' | 'on'
  has_connected_driver: boolean
  open_notes: number   // unacknowledged warn+escalate
}

SuggestedAction {
  kind: 'ack' | 'retry' | 'integrate' | 'run'
  project_id: string
  task_id?: string
  job_id?: string
  reason: string             // human-readable for the button label
  rest_verb: 'POST' | 'PATCH'
  rest_path: string
  payload?: object           // JSON to send
}

DriverNoteOut {
  id: string
  project_id: string
  task_id: string | null
  job_id: string | null
  severity: 'info' | 'warn' | 'escalate'
  kind: 'acked' | 'ran' | 'integrated' | 'retried' | 'escalated' | 'suggest' | 'stuck'
  message: string
  action_url: string | null
  created_at: string
  acknowledged_at: string | null
}

DriverGlobalStatus {
  connected: boolean
  last_seen: string | null
  mode_on_projects: string[]
}
```

---

## 3. New endpoints

All under the existing bearer-token auth.

### Reshape the DAG before running

`POST /api/tasks/{task_id}/split`
```ts
body: {
  new_tasks: { title: string; prompt: string }[]
  inherit_deps_in?: boolean   // default true
  link_in_series?: boolean    // default true
}
→ TaskOut[]   // the new tasks; original is deleted
```

`POST /api/tasks/merge`
```ts
body: {
  task_ids: string[]   // all must be in same project, status='pending'
  title: string
  prompt: string
}
→ TaskOut             // the merged task; originals are deleted
```

Both reject (409) on `running` tasks. Merge also rejects (400) if any input
depends transitively on another input.

### Ack a plan

There is **no new endpoint** — acking is a followup on a job at
`phase=awaiting_ack`:

```
POST /api/jobs/{job_id}/followup   { prompt: '' }
```

The backend sees `phase === 'awaiting_ack'`, creates the worktree, flips
phase to `executing`, and spawns turn 1. Any non-empty `prompt` is
appended as guidance. If the job's phase is **not** `awaiting_ack`, this
behaves exactly like v1 (extra conversational turn).

### Integrate a wave

`POST /api/projects/{project_id}/integrate`
```ts
body: {
  task_ids: string[]              // all must be status='done'
  target_branch?: string | null   // defaults to project's current HEAD branch
}
→ TaskOut   // a synthetic task in status='ready'; you still need to run it
```

The synthetic task is `synthetic=true, mode='one_shot'`. To start the
merge, `POST /api/tasks/{id}/run` on it. While it runs the job's `phase`
is `integrating`. On success, input tasks' `integration_status` flips to
`integrated` and their `worktree_path`/`worktree_branch` are cleared. On
failure, inputs are marked `conflict` and the integration job stays open
— a followup on it can drive resolution.

### Retry a failed task

`POST /api/tasks/{task_id}/retry`
- Allowed only when `task.status === 'failed'`.
- Clears any existing worktree, increments `retries`, resets status to
  `ready`, and immediately spawns a fresh job.
- Returns `JobOut` for the new run.

Used by the driver (autopilot) and by copilot one-tap "Retry" buttons.

### List outstanding worktrees

`GET /api/projects/{project_id}/worktrees`
```ts
→ {
    path: string
    branch: string | null
    head: string | null
    detached: boolean
    task_id: string | null   // null = orphan (unknown to harness)
  }[]
```

### Driver

```
PATCH /api/projects/{id}/driver         { mode: 'off' | 'on' }   → DriverStateOut
GET   /api/projects/{id}/driver                                  → DriverStateOut
GET   /api/projects/{id}/driver/suggestions                      → SuggestedAction[]
GET   /api/projects/{id}/driver/notes
        ?severity=info|warn|escalate
        &acknowledged=true|false                                  → DriverNoteOut[]
POST  /api/driver/notes/{note_id}/acknowledge                    → DriverNoteOut
GET   /api/driver/status                                         → DriverGlobalStatus
```

Notes:
- `PATCH /driver { mode: 'on' }` **can return 409** if no driver process
  is connected and the harness can't auto-spawn one. Show the error.
- `GET /driver/suggestions` returns valid actions regardless of mode — when
  mode is `on`, the driver is already executing them; when `off`, the UI
  should render them as buttons.
- `/api/driver/events` (SSE) exists, but it's for the **driver process**
  only; the UI doesn't subscribe.

---

## 4. Per-view UI changes

### Project list

- Add an **autopilot indicator** next to each project name when
  `autopilot_mode === 'on'` (e.g., a small "AUTO" pill).
- Add a **notes badge** when `DriverStateOut.open_notes > 0` — colored by
  the worst severity (warn = amber, escalate = red). Tapping deeplinks to
  the project's driver tab.
- Source either by adding the two fields to the project list response, or
  by fetching `/driver` per-project on the list page (cheap, just two
  ints). For 12-hour autopilot users the badge is the most important
  affordance — they glance at the list to spot escalations.

### Project detail header

- **Autopilot toggle** in settings or the header. Backed by
  `PATCH /api/projects/{id}/driver`.
  - If `GET /api/driver/status.connected === false`, the toggle should
    still be enabled — flipping to `on` triggers an auto-spawn. If the
    response is 409, show an inline error with a link to
    `~/.agent-harness/logs/driver.log` instructions.
- Below the header, a one-line **status strip** when autopilot is on:
  *"Autopilot active · 2 tasks running · 1 awaiting ack · 0 escalations"*.
  Counts come from a single fetch of tasks + jobs + notes.

### Driver tab (per project)

A new tab dedicated to the driver, useful in both modes.

- **Mode = off:**
  - Top section: live **suggestions** from
    `GET /api/projects/{id}/driver/suggestions`. Render each as a
    button row with `reason` as the label and the action's `kind` as a
    chip ("ack" / "run" / "integrate" / "retry"). Clicking dispatches
    the suggestion's `rest_verb` + `rest_path` + `payload` via the
    existing REST surface.
  - Below: a **notes timeline** for context (any past autopilot runs,
    historical escalations).

- **Mode = on:**
  - Top section: a **notes feed** (newest first), grouped or filtered
    by severity. Escalate notes are sticky at the top with a primary
    "Acknowledge" button.
  - Suggestions list can stay rendered (read-only, for visibility),
    but greyed out — the driver is executing them, not the user.

### Task list

- New badges: `mode`, `synthetic`, `integration_status`, `retries`. See §2.
- **Bulk-select** affordance for tasks with `integration_status='pending'`
  with an "Integrate selected" action that POSTs to
  `/api/projects/{id}/integrate`. Confirmation modal with target branch
  (default = project's current HEAD branch).
- Don't show a "confirm draft" affordance for planner-created tasks;
  v1's `pending → ready` is automatic.

### Task detail

- **Plan section**: when there's a `kind='plan'` outcome for this task,
  render its `summary` prominently. If the bound job is at
  `phase='awaiting_ack'`, show the **"Ack plan"** primary button (with an
  optional guidance textarea); empty input is valid. Hide the generic
  "Followup" form in that state.
- **Outcomes**: group by `kind`. For `kind='execute'` and `kind='integrate'`,
  link to the commit (`commit_sha`). For `kind='plan'`, no commit link.
- **Retry surface**: when `status='failed' && retries < 2`, show a
  one-tap "Retry" button → `POST /api/tasks/{id}/retry`. When `retries >= 2`,
  show the escalation note prominently (autopilot won't auto-retry further).

### Job detail

- Add a **phase pill** at the top, alongside the existing status. See §2.
- When `phase='integrating'`, label the job clearly as an integration job
  (it's the synthetic task's job).
- When `phase='awaiting_ack'`, the page should funnel toward the
  "Ack plan" action defined on the task detail (or render it inline here).

---

## 5. End-to-end flows

### Manual (mode = off, no copilot help)

1. **Plan an ask.** `POST /api/projects/{id}/plan { ask }` → draft tasks
   with `status='pending'`, `mode='plan_then_execute'`.
2. **Reshape if needed.** Split/merge from the task list.
3. **Run a ready task.** `POST /api/tasks/{id}/run`. Task → `running`;
   job appears with `phase='planning'`. Stream the transcript.
4. **Job parks at `awaiting_ack`.** Render the plan; user clicks
   "Ack plan" → `POST /api/jobs/{id}/followup { prompt: '' }`.
5. **Execute turn.** Phase → `executing`, `worktree_path` populated.
   Stream turn 1.
6. **Task done, integration pending.** Repeat 3–5 for sibling tasks.
7. **Integrate the wave.** Select pending-integration tasks, click
   "Integrate", run the returned synthetic task. Phase → `integrating`.
8. **Conflict?** Inputs marked `integration_status='conflict'`. Followup
   on the integration job's textbox until resolved.

### Copilot (mode = off, suggestions visible)

Same as manual, but the **driver tab** shows the next button(s) to click.
Steps 3, 4, 7 surface as one-tap actions ("Run T1", "Ack plan for T1",
"Integrate wave of 2") — clicking dispatches the same REST call you'd
make manually. Each click invalidates suggestions and the next one
appears. The UI is the executor; the driver process is uninvolved.

### Autopilot (mode = on)

User clicks the autopilot toggle. The harness ensures a driver is
connected (auto-spawning if needed), then proceeds without further user
input. The UI's job becomes:

1. Show the **status strip** (running tasks, awaiting acks, escalations)
   in the project header.
2. Show the **notes feed** in the Driver tab as it grows.
3. **Surface escalations.** When a `DriverNote(severity='escalate')`
   appears (e.g., a task hit max retries, an integration conflict
   couldn't be resolved), the UI should pop a notification and offer
   the user manual control: usually "Retry one more time", "Edit
   prompt and re-run", "Followup the integration job", or "Cancel
   task".
4. **Hand back.** User can flip the toggle off at any time; in-flight
   driver actions complete normally, but no new ones start.

---

## 6. State / refresh strategy

The existing SSE stream covers per-job event flow. For state outside
the job stream (driver state, suggestions, notes), the UI fetches
on-demand or on a slow timer.

A pragmatic minimum:
- After `POST /tasks/{id}/run`, `/retry`, `/cancel` or `POST /jobs/{id}/followup`:
  refetch the affected task + job + its outcomes.
- After `POST /projects/{id}/integrate`: refetch tasks in the project.
- After `PATCH /driver`: refetch `/driver` (state) and `/driver/status`.
- On every `job_status` SSE event: refetch the affected job + task. If
  the new state suggests a likely driver action (e.g., a job just hit
  `phase='awaiting_ack'`), also refetch `/driver/suggestions` and
  `/driver/notes` so the UI updates immediately.
- **Driver tab, mode=off**: refetch `/suggestions` on a 10s slow timer
  while focused (or after each suggestion is dispatched).
- **Driver tab, mode=on**: refetch `/notes` on a 10s slow timer while
  focused. Notes are append-only between fetches; you can paginate.

The driver SSE stream (`/api/driver/events`) is **not** for the UI —
it's the driver process's communication channel.

---

## 7. Gotchas

- **`job.status='done'` does not mean the workflow is done.** Inspect
  `job.phase` first; `done` + `awaiting_ack` is the parked plan state.
- **Empty followup `prompt` is valid** — it's a bare ack. Don't validate
  for non-empty content when the job is `awaiting_ack`.
- **Worktree paths are absolute paths inside `~/.agent-harness/worktrees/`**
  — fine to display, but they live outside the project repo.
- **Synthetic tasks should be visually distinct** but **still selectable** —
  they participate in the DAG and can themselves fail and need followups.
- **Don't surface a "confirm draft" affordance.** v1's `pending → ready`
  transition is automatic when deps are satisfied.
- **Cancelling a running task also removes its worktree** server-side;
  refetch the task to see `worktree_path` go back to `null`.
- **Mode = on can 409** when no driver is connected and auto-spawn fails.
  Surface the error with a hint to run `agent-harness-driver` manually.
- **`/driver/notes` can reference deleted tasks/jobs.** The FK is nullable
  but there's no `ON DELETE SET NULL` — old notes may have stale ids.
  Render defensively (`task_id` lookups can return 404).
- **Don't compete with autopilot.** When `autopilot_mode === 'on'`,
  showing "Ack plan" / "Run" / "Integrate" buttons is fine for parity but
  the user clicking them races the driver. Prefer to grey them out (or
  show a "driver will handle this" hint) when mode is on.
- **Don't subscribe to `/api/driver/events` from the UI.** It accepts
  only one active subscriber; the driver process owns it.

---

## 8. OpenAPI reference

| Concept | Path / Schema |
|---|---|
| Task fields | `components.schemas.TaskOut` |
| Job phase | `components.schemas.JobOut.phase` |
| Outcome kind | `components.schemas.OutcomeOut.kind` |
| Project autopilot | `components.schemas.ProjectOut.autopilot_mode` |
| Split | `paths./api/tasks/{task_id}/split.post` (`SplitIn`) |
| Merge | `paths./api/tasks/merge.post` (`MergeIn`) |
| Integrate | `paths./api/projects/{project_id}/integrate.post` (`IntegrateIn`) |
| Worktrees | `paths./api/projects/{project_id}/worktrees.get` (`WorktreeOut`) |
| Ack | `paths./api/jobs/{job_id}/followup.post` — branches on `phase` |
| Retry | `paths./api/tasks/{task_id}/retry.post` |
| Driver toggle | `paths./api/projects/{project_id}/driver.patch` (`DriverModeUpdate`) |
| Driver state | `paths./api/projects/{project_id}/driver.get` (`DriverStateOut`) |
| Suggestions | `paths./api/projects/{project_id}/driver/suggestions.get` (`SuggestedAction`) |
| Notes (list) | `paths./api/projects/{project_id}/driver/notes.get` (`DriverNoteOut`) |
| Notes (ack) | `paths./api/driver/notes/{note_id}/acknowledge.post` |
| Driver status (global) | `paths./api/driver/status.get` (`DriverGlobalStatus`) |
