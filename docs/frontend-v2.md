# Frontend v2: plan-then-execute, worktrees, integration

What changed in the API surface for v2, and what the UI needs to show or
let the user do. Pair this with the OpenAPI dump (`agent-harness gen-openapi`)
for exact field types — this doc covers the concepts and the suggested UX.

---

## 1. The new task lifecycle

v1 was: `pending → ready → running → done|failed|canceled`.

v2 keeps that for the **task**, but adds a `phase` on the **job** that
backs it, and most tasks now go through two turns instead of one:

```
            Task.status                  Job.phase
            ───────────                  ─────────
created  →  pending                      (no job yet)
deps met →  ready                        (no job yet)
POST /run → running   ─ creates job ─→   planning
                                          │   turn 0 runs in project.path
                                          ▼
                       ←─── (parked) ─── awaiting_ack
followup →                                │   ack: worktree created,
                                          │   job.cwd_override set
                                          ▼
                                         executing
                                          │   turn 1 runs in the worktree
                                          ▼
                                         done
            done           ←──────────────
            integration_status = pending

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
a worktree (unless `synthetic=true`, in which case it runs in `project.path`).

---

## 2. What's new on the DTOs

### `TaskOut`
```ts
{
  // … v1 fields …
  mode: 'plan_then_execute' | 'one_shot'
  worktree_path: string | null     // populated while executing/done
  worktree_branch: string | null   // `task/<task_id>` once a worktree exists
  integration_status: null | 'pending' | 'integrated' | 'conflict'
  synthetic: boolean                // true = system-generated integration task
}
```

UI surfacing:
- A **"plan" badge** on tasks where `mode === 'plan_then_execute'`.
- A **"one-shot"** or **"integration"** badge for `mode === 'one_shot'`
  (use `synthetic` to distinguish integration from user one-shots).
- A **worktree link/copy-path button** when `worktree_path` is set.
- An **integration status pill** when `integration_status` is non-null:
  `pending` (grey), `integrated` (green), `conflict` (red, actionable).

### `JobOut`
```ts
{
  // … v1 fields …
  phase: null | 'planning' | 'awaiting_ack' | 'executing' | 'integrating' | 'done'
}
```

UI surfacing:
- The job header should show **phase prominently** when non-null, not just
  the v1 `status`. A `status=done, phase=awaiting_ack` job is **not** finished
  from the user's POV — it's parked at the plan gate. Treat
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
  outcome's `summary` is the plan text the user is about to ack — render it
  as the centerpiece when the job is in `awaiting_ack`.
- `kind='plan'` outcomes have `commit_sha=null` (planning doesn't commit).
  Don't link to a commit for those.
- `kind='integrate'` outcomes are produced by synthetic tasks.

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

UX:
- A "Split…" action on a pending/ready task opens a modal with N rows of
  `{title, prompt}`, plus two checkboxes (`inherit_deps_in`, `link_in_series`).
- A "Merge…" action on the task list takes a multi-select of pending tasks
  and a title/prompt for the combined replacement.

### Ack a plan

There is **no new endpoint** for this — acking is just a followup on a job
that's currently `phase=awaiting_ack`:

`POST /api/jobs/{job_id}/followup` with body `{ prompt: '' }`
- The backend sees `phase === 'awaiting_ack'`, creates the worktree,
  flips phase to `executing`, and spawns turn 1 with the task prompt
  (plus any optional `prompt` you send, which is appended as guidance).
- If the followup is on a job whose phase is **not** `awaiting_ack`, it
  behaves exactly like v1 (extra conversational turn).

UX:
- An **"Ack plan"** primary button when `job.phase === 'awaiting_ack'`,
  with an optional "additional guidance" textarea (empty is fine — sends `""`).
- Hide the generic "Followup" form in that state; replace it with the
  Ack flow so users don't accidentally send a conversational turn instead
  of advancing the phase.

### Integrate a wave

`POST /api/projects/{project_id}/integrate`
```ts
body: {
  task_ids: string[]              // all must be status='done'
  target_branch?: string | null   // defaults to project's current HEAD branch
}
→ TaskOut   // a synthetic task in status='ready'; you still need to run it
```

The returned task is `synthetic=true, mode='one_shot'`. To start the merge,
`POST /api/tasks/{id}/run` on it. While it runs the job's `phase` will be
`integrating`. On success, the input tasks' `integration_status` flips to
`integrated` and their `worktree_path`/`worktree_branch` are cleared. On
failure, the inputs are marked `conflict` and the integration job is left
open — followups on it can drive resolution.

UX:
- A bulk "Integrate selected" action on tasks whose
  `integration_status === 'pending'`. Confirmation modal previews the
  target branch (use the project's current branch as a default, let user
  override).
- The created synthetic task should be visible in the task list with a
  distinct icon / "integration" label.

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

UX:
- A project-level "Worktrees" panel listing rows; highlight orphans
  (`task_id === null`) — these need manual cleanup (see
  `docs/operations.md`). Don't surface a delete button; we deliberately
  don't auto-delete orphans.

---

## 4. End-to-end UI flow (recommendation)

A user creates a project, drops in an ask, and walks through a wave:

1. **Plan an ask.** `POST /api/projects/{id}/plan { ask }` → draft tasks
   appear with `status='pending'`, `source='planner'`,
   `mode='plan_then_execute'`. (Already works in v1; nothing UI-new beyond
   showing the new mode badge.)
2. **Reshape if needed.** Split/merge from the task list.
3. **Run a ready task.** `POST /api/tasks/{id}/run`. The task moves to
   `running`; the new job appears with `phase='planning'`. Stream the
   transcript as in v1.
4. **Job parks at `awaiting_ack`.** Render the plan: pull the latest
   `kind='plan'` outcome for the task and show its `summary`. Primary
   button "Ack plan" (`POST /api/jobs/{id}/followup { prompt: '' }`).
5. **Execute turn.** Phase flips to `executing`, `worktree_path` appears
   on the task. Stream turn 1.
6. **Task done, integration pending.** Task badge changes to
   `integration_status='pending'`. Repeat 3–5 for sibling tasks.
7. **Integrate the wave.** User selects tasks with
   `integration_status='pending'` and clicks "Integrate". Confirmation
   modal asks for the target branch. After `POST /integrate`, run the
   returned synthetic task. While it runs, the job is in `phase='integrating'`.
8. **Conflict handling.** If the integration finishes failed,
   `integration_status` on inputs becomes `conflict`. The synthetic task's
   job is still open — surface a "Resolve" textbox that POSTs a followup
   on it (regular followup, not an ack — its phase will not be
   `awaiting_ack`). Loop until it succeeds.

---

## 5. State / refresh strategy

The existing SSE stream already covers per-job event flow. For the v2
state transitions that happen on the server (phase flips, worktree
creation, integration_status changes), no new event types are added —
the UI should invalidate task/job/outcome list queries on the existing
`job_status` event and on followup acks.

A pragmatic minimum:
- After `POST /tasks/{id}/run` or `POST /jobs/{id}/followup`: refetch
  the affected task + job + its outcomes.
- After `POST /projects/{id}/integrate`: refetch tasks in the project
  (the new synthetic task appears, inputs unchanged until the integration
  runs).
- On every `job_status` SSE event: refetch the affected job and its task.

---

## 6. Gotchas

- **`job.status='done'` does not mean the workflow is done.** Inspect
  `job.phase` first; `done` + `awaiting_ack` is the parked plan state.
- **Empty followup `prompt` is valid** — it's a bare ack. Don't validate
  for non-empty content in the UI when the job is `awaiting_ack`.
- **Worktree paths are absolute paths inside `~/.agent-harness/worktrees/`**
  — fine to display, not fine to assume they live under the project repo.
- **Synthetic tasks should be visually distinct** but **still selectable**
  in lists — they participate in the same DAG and can themselves fail and
  need followups.
- **Don't surface a "confirm draft" affordance.** v1's `pending → ready`
  transition is automatic when deps are satisfied; v2 didn't add a manual
  confirmation step.
- **Cancelling a running task also removes its worktree** server-side; the
  UI should refetch the task to see `worktree_path` go back to `null`.

---

## 7. Driver (autopilot + copilot)

A per-project mode controls whether the harness drives itself:

- `Project.autopilot_mode: 'off' | 'on'` — defaults to `off`.
- **Off (copilot):** call `GET /api/projects/{id}/driver/suggestions` to
  fetch the same actions an autopilot would take, render them as one-tap
  buttons. Each suggestion has `rest_verb`, `rest_path`, `payload`, `reason`.
- **On (autopilot):** an external process (`agent-harness-driver`)
  dispatches the same actions automatically. The harness records each
  action as a `DriverNote` for audit + escalation.

### New endpoints

```ts
PATCH /api/projects/{id}/driver        { mode: 'off' | 'on' }    → DriverStateOut
GET   /api/projects/{id}/driver        → DriverStateOut
GET   /api/projects/{id}/driver/suggestions  → SuggestedAction[]
GET   /api/projects/{id}/driver/notes        → DriverNoteOut[]
       ?severity=info|warn|escalate &acknowledged=true|false
POST  /api/driver/notes/{id}/acknowledge     → DriverNoteOut
GET   /api/driver/status               → { connected, last_seen, mode_on_projects }
POST  /api/tasks/{id}/retry            → JobOut   (used by retry suggestions)
```

`DriverStateOut`:
```ts
{
  mode: 'off' | 'on'
  has_connected_driver: boolean
  open_notes: number   // unacknowledged warn+escalate
}
```

`SuggestedAction`:
```ts
{
  kind: 'ack' | 'retry' | 'integrate' | 'run'
  project_id: string
  task_id?: string
  job_id?: string
  reason: string             // human-readable for the button label
  rest_verb: 'POST' | 'PATCH'
  rest_path: string
  payload?: object           // JSON to send
}
```

`DriverNoteOut`:
```ts
{
  id: string
  project_id: string
  task_id: string | null
  job_id: string | null
  severity: 'info' | 'warn' | 'escalate'
  kind: string               // 'acked' | 'ran' | 'integrated' | 'retried' |
                             //  'escalated' | 'suggest' | 'stuck'
  message: string
  action_url: string | null
  created_at: string
  acknowledged_at: string | null
}
```

### Suggested UX

- Project settings: a single **Autopilot** toggle backed by `PATCH /driver`.
  Disabled if `/api/driver/status.connected === false` (with a hint to
  start `agent-harness-driver`).
- A **Driver** tab per project:
  - Top: if `mode === 'off'`, render the live `suggestions` as a list of
    one-tap buttons ("Ack plan for X", "Run T1", "Integrate wave of 2").
    Each button POSTs to the suggested `rest_path` with `payload`.
  - Below: the **notes timeline** (newest first), grouped by severity.
    Escalate notes get a prominent treatment and an "Acknowledge" button.
- Global header: a small dot/counter for un-acked `warn`+`escalate` notes
  across all projects. Tap → driver tab of the most recent.

### Polling vs SSE

The driver event SSE stream (`/api/driver/events`) is for the driver
**process** — not for the UI. The UI should fetch `/suggestions` and
`/notes` on demand: after any mutating action, or on a slow timer
(e.g., 10s) when the user is viewing the driver tab. Cheap and sufficient.

### Gotchas

- **Mode=on can 409** if no driver is connected and auto-spawn fails.
  Show the error to the user with a hint to run `agent-harness-driver` or
  check `~/.agent-harness/logs/driver.log`.
- **Notes can reference deleted tasks/jobs** — the FK is nullable but
  doesn't ON DELETE SET NULL; just be defensive when rendering.
- **The "Ack plan" suggestion** appears for plan-then-execute tasks the
  same way as in §6 — the driver surfaces it; the UI should treat it as
  the canonical action even in copilot mode (don't show a competing
  generic "Followup" form when phase=awaiting_ack).

## 8. Where to find each thing in the OpenAPI dump

| Concept | Path / Schema |
|---|---|
| Task phase fields | `components.schemas.TaskOut` |
| Job phase | `components.schemas.JobOut.phase` |
| Outcome kind | `components.schemas.OutcomeOut.kind` |
| Split | `paths./api/tasks/{task_id}/split.post` (`SplitIn`) |
| Merge | `paths./api/tasks/merge.post` (`MergeIn`) |
| Integrate | `paths./api/projects/{project_id}/integrate.post` (`IntegrateIn`) |
| Worktrees | `paths./api/projects/{project_id}/worktrees.get` (`WorktreeOut`) |
| Ack | `paths./api/jobs/{job_id}/followup.post` — same endpoint, branches on `phase` |
| Driver toggle | `paths./api/projects/{project_id}/driver.patch` (`DriverModeUpdate`) |
| Driver state | `paths./api/projects/{project_id}/driver.get` (`DriverStateOut`) |
| Suggestions | `paths./api/projects/{project_id}/driver/suggestions.get` (`SuggestedAction`) |
| Notes | `paths./api/projects/{project_id}/driver/notes.get` (`DriverNoteOut`) |
| Driver status | `paths./api/driver/status.get` (`DriverGlobalStatus`) |
| Retry | `paths./api/tasks/{task_id}/retry.post` |
