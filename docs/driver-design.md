# Driver: autopilot + co-pilot

The driver completes the agent-harness loop: it acks plans, kicks ready tasks,
integrates completed waves, and retries transient failures so a multi-hour
project can run unattended. It also surfaces "what should happen next" as
suggestions when the human is in the chair.

## Mental model

- **Two modes per project** — `autopilot_mode = 'off' | 'on'`.
- **Co-pilot (mode=off)** lives **in the harness**: a pure decision function
  computes the next actions; the UI renders them as one-tap buttons. No
  external process involved.
- **Autopilot (mode=on)** lives in **a separate process** (`agent-harness-driver`)
  that listens to an SSE event stream and dispatches the same actions over
  REST.
- The harness **gates event emission at the source** by `autopilot_mode`. If a
  project is `off`, no signals leave the harness for it. The driver process
  sits idle, holding its SSE connection open but receiving nothing.

This satisfies "signals only when activated; driver is independent" — the
harness doesn't tick, the driver doesn't poll, and the two are loosely
coupled by one SSE stream + one REST surface.

## Schema additions

```
Project.autopilot_mode  VARCHAR(8)  DEFAULT 'off'   -- 'off' | 'on'
Task.retries            INTEGER     DEFAULT 0       -- retry counter

DriverNote
  id              PK
  project_id      FK
  task_id         nullable FK
  job_id          nullable FK
  severity        VARCHAR(8)    -- 'info' | 'warn' | 'escalate'
  kind            VARCHAR(16)   -- 'acked' | 'ran' | 'integrated' | 'retried' |
                                --  'escalated' | 'suggest' | 'stuck'
  message         TEXT
  action_url      TEXT NULL     -- for 'suggest' notes; REST verb + path
  created_at, acknowledged_at
```

Auto-prune notes older than 7 days (cron'd into the existing schedule service
or done lazily on read).

## Event vocabulary

A new internal `DriverEventBus` exposes `emit(event_type, project_id, **kw)`.
Each emit checks `project.autopilot_mode`; **suppressed at source** if `off`.

```
event             payload                                    fires from
─────             ───────                                    ──────────
task_ready        {project_id, task_id}                      task_runner._reevaluate_downstream,
                                                             routes/tasks.create_task (when initial=ready),
                                                             services/integration.create_integration_task
plan_ready        {project_id, job_id, task_id}              jobs._finalize_turn (when phase→awaiting_ack)
task_done         {project_id, task_id, job_id}              task_runner.on_job_finalized (execute branch)
task_failed       {project_id, task_id, job_id}              task_runner.on_job_finalized (failed)
integration_done  {project_id, task_id}                      task_runner.on_job_finalized (synthetic, success)
integration_conflict {project_id, task_id}                   task_runner.on_job_finalized (synthetic, failed)
reconcile_now     {project_id}                               emitted on subscribe + on mode→on
mode_off          {project_id}                               emitted on mode→off, before suppression
```

Driver doesn't pattern-match on event types except `reconcile_now` /
`mode_off`. Every other event triggers the same handler: **fetch current
project state, decide, act**. Events are wake-up pings; state is in the DB.

## Decision policy (pure, reused by copilot)

`services/driver_policy.py`:

```python
@dataclass
class Action:
    kind: Literal['ack', 'retry', 'integrate', 'run']
    project_id: str
    task_id: str | None = None
    job_id: str | None = None
    payload: dict | None = None    # request body
    reason: str = ""               # human-readable for notes / UI
    rest_verb: str = ""            # 'POST', 'PATCH'
    rest_path: str = ""            # '/api/jobs/X/followup'

def next_actions(session, project_id, max_actions=8) -> list[Action]:
    # priority-ordered:
    # 1. ack jobs at phase='awaiting_ack'
    # 2. retry tasks at status='failed' with retries < max_retries
    #    and elapsed_since_failed > backoff(retries)
    # 3. integrate the largest mergeable wave (definition below)
    # 4. run tasks at status='ready', bounded by parallel cap
    ...
```

**Wave definition.** A task `t` is in the integratable wave iff:
- `t.status == 'done'`
- `t.integration_status == 'pending'`
- every dep-chain ancestor of `t` has `integration_status == 'integrated'`
  (or is a root with no integration_status)

Integrate the entire wave in one synthetic task. The driver only triggers
integration when no integration job is currently running for the project.

**Retry policy.** `max_retries = 2`, exponential backoff 60s → 180s. After
max, the task stays `failed`; an `escalate` note is posted exactly once
per task.

## API surface

```
PATCH /api/projects/{id}/driver            { mode: 'off' | 'on' }
GET   /api/projects/{id}/driver            { mode, ... }
GET   /api/projects/{id}/driver/suggestions → Action[]  (when mode=off, the
                                                          UI renders these)
GET   /api/projects/{id}/driver/notes      → DriverNote[]  (filter by severity)
POST  /api/driver/notes                     { project_id, severity, kind,
                                              message, task_id?, job_id?,
                                              action_url? }    -- driver-only
POST  /api/driver/notes/{id}/acknowledge
GET   /api/driver/events                    -- SSE; auth-required
GET   /api/driver/status                    { connected, last_seen,
                                              mode_on_projects: [id] }
```

The SSE endpoint allows at most **one active subscriber**. A second connect
gets 409. This avoids racing acks/runs.

## Auto-spawn fallback

`PATCH /driver {mode:'on'}` flow:

```
if event_bus.has_subscriber():
    set mode='on'; emit reconcile_now
    return 200
else:
    proc = spawn_subprocess('agent-harness-driver',
                            log_to=AH_HOME/logs/driver.log)
    wait up to spawn_timeout=5s for the bus to gain a subscriber
    if connected:
        track proc in app.state.owned_drivers   # so we can kill on shutdown
        set mode='on'; emit reconcile_now
        return 200
    else:
        proc.kill()
        raise HTTPException(409, "could not start agent-harness-driver; "
                                  "see logs/driver.log")
```

**Ownership.** Only auto-spawned drivers are killed when the *last* project
flips back to `off`. A driver the user manually started keeps running
across mode toggles. On harness shutdown, owned drivers are terminated;
unowned ones are left alone.

**Mode→off** emits `mode_off` *before* flipping the flag so the driver
gets one last wake-up to clean up retry timers for that project. Subsequent
events are suppressed at source.

## Driver process

`agent-harness-driver` — new console-script in `pyproject.toml`, entry
point `agent_harness.services.driver_runtime:main`.

```
read base_url + token from ~/.agent-harness/config.toml
loop forever (with reconnect + exponential backoff):
    open SSE: GET /api/driver/events
    on connect: harness sends reconcile_now per mode=on project
    for each event:
        if 'mode_off': drop retry timers for project_id; continue
        # all other events:
        state = GET /tasks /jobs /outcomes for project_id
        actions = decide locally (import driver_policy)
        for action in actions:
            dispatch via REST
            POST /api/driver/notes(info, kind, ...) afterwards
```

The driver is **stateless across restarts** — every retry timer is
in-memory. Restarting mid-12-hour run is safe: reconnect → reconcile →
resume. In-flight HTTP calls complete normally.

## Co-pilot rendering (mode=off)

UI hits `GET /projects/{id}/driver/suggestions` periodically (or on focus,
or after any mutating action). Each returned `Action` has `rest_verb`,
`rest_path`, `payload`, `reason`. UI renders a list of one-click suggestions
("Ack plan for 'extract auth module'", "Run task 'add JWT lib'",
"Integrate wave [t1, t2]"). Clicking dispatches the action via the existing
REST surface — no driver involvement.

## MCP additions

New tools (alongside the existing 15):

```
set_autopilot(project_id, mode)       — PATCH /driver
get_driver_state(project_id)
list_driver_notes(project_id, severity?, acknowledged?)
acknowledge_note(note_id)
get_suggestions(project_id)            — copilot-style next actions
```

These let an external Claude session inspect / drive autopilot the same
way the UI can.

## Files to add / modify

```
add  server/agent_harness/services/driver_bus.py        — singleton event bus
add  server/agent_harness/services/driver_policy.py     — pure decision fn
add  server/agent_harness/services/driver_runtime.py    — external process main
add  server/agent_harness/routes/driver.py              — all /api/driver* routes
add  server/tests/test_driver_policy.py
add  server/tests/test_driver_bus.py
add  server/tests/test_driver_routes.py
add  server/tests/test_driver_runtime.py
mod  server/agent_harness/models.py                     — Project.autopilot_mode,
                                                          Task.retries, DriverNote
mod  server/agent_harness/db.py                         — ALTERs + table create
mod  server/agent_harness/schemas.py                    — DriverNoteOut,
                                                          DriverStateOut,
                                                          DriverModeUpdate,
                                                          SuggestedAction
mod  server/agent_harness/services/task_runner.py       — bus.emit at finalize
mod  server/agent_harness/jobs.py                       — bus.emit on awaiting_ack
mod  server/agent_harness/routes/tasks.py               — bus.emit on task_ready,
                                                          on create with deps satisfied
mod  server/agent_harness/services/integration.py       — bus.emit when synth task
                                                          is created and ready
mod  server/agent_harness/services/orchestrator_mcp.py  — new MCP tools
mod  server/agent_harness/main.py                       — register bus on app.state,
                                                          kill owned drivers on
                                                          shutdown
mod  pyproject.toml                                     — agent-harness-driver script
mod  docs/operations.md                                 — driver lifecycle, logs
mod  docs/frontend-v2.md                                — copilot suggestions UX
mod  .claude/skills/agent-harness/SKILL.md              — autopilot recipes
```

## Verification

1. `uv run pytest server/tests` — new tests for: bus emit gating
   by mode, policy decisions per scenario, mode toggle flow, auto-spawn
   path, SSE single-subscriber 409, suggestions endpoint vs autopilot
   dispatch parity.
2. End-to-end manual: create a project, plan an ask with 3 tasks
   (T1, T2, T3 deps T1+T2), toggle `mode=on`, wait. Verify each phase
   transition (plan → ack → execute → wave-integrate → T3 plan → ack →
   execute → integrate) happens without manual intervention. Check
   `DriverNote` timeline.
3. Fault injection: kill the driver mid-run. Verify the user sees
   `connected=false` on `/driver/status` after a few seconds. Restart;
   it reconciles and picks up where it left off.

## Out of scope

- Cost cap / spend tracking integration (use existing `turn.cost_usd`
  data, future feature).
- Replanning / split decisions on failure (a Claude judgment, not a driver
  one).
- Cross-project orchestration (driver acts per-project).
- Pause/resume semantics — `mode='off'` is the only pause.
- Webhook-based remote driver (we ship SSE; remote drivers can use it too
  as long as they can reach the harness).
