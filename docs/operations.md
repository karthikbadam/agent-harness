# Operations & diagnostics

Day-to-day use and deep diagnosis for a running agent-harness install.
For first-time setup see the [README](../README.md).

All paths assume `AH_HOME=~/.agent-harness` (the default).

---

## 1. The control surface

Three ways to drive the harness:

| Surface | Use when |
| --- | --- |
| Web UI (`http://<lan-ip>:8765/`) | Day-to-day; submitting/monitoring from iPhone. |
| HTTP API (`/api/*`) | Scripts, cron'd shell pokes, Claude Code skill, tests. |
| `agent-harness` CLI | Setup and service lifecycle only (`init`, `gen-token`, `gen-openapi`, `serve`). |

The CLI never reaches into a running server — it operates on `AH_HOME`
files. To talk to a live server, use the HTTP API.

### Auth

Single bearer token, set at install time and stored at `~/.agent-harness/config.toml`.

```bash
TOKEN=$(python3 -c 'import tomllib,os; print(tomllib.loads(open(os.path.expanduser("~/.agent-harness/config.toml")).read())["auth_token"])')
BASE="http://127.0.0.1:8765"

curl -sS -H "Authorization: Bearer $TOKEN" $BASE/api/me
# {"ok":true}
```

EventSource can't set headers, so the SSE route also accepts `?token=...`.

### Rotate the token

```bash
agent-harness gen-token --force        # prints the new one
launchctl kickstart -k gui/$(id -u)/com.$(whoami).agent-harness
```

Old tokens are invalidated immediately; reopen the UI with the new token.

---

## 2. Common HTTP recipes

### Create a project

```bash
curl -sS -X POST $BASE/api/projects \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"qft-book","path":"/Users/me/code/qft-book"}'
```

`permission_mode` defaults to `acceptEdits`. To run hands-off on a trusted
repo, pass `"dangerously_skip": true`.

### Submit a job

```bash
curl -sS -X POST $BASE/api/jobs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"project_id":"<pid>","prompt":"build chapter 0","title":"ch0"}'
```

Response includes `id`. A turn 0 row is created immediately and the runner
spawns in the background.

### Follow up on a running/completed job

```bash
curl -sS -X POST $BASE/api/jobs/<jid>/followup \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"now switch to Susskind notation"}'
```

Follow-ups re-attach to the same Claude session (`--resume <session_id>`,
captured from turn 0's `system/init`). The per-job lock makes followups
wait for the current turn to finish.

### Stop a job

```bash
curl -sS -X POST $BASE/api/jobs/<jid>/stop \
  -H "Authorization: Bearer $TOKEN"
```

Sends SIGTERM, then SIGKILL after 5s. Idempotent; safe to call when nothing
is running.

### Watch the live stream

```bash
curl -N "$BASE/api/jobs/<jid>/stream?token=$TOKEN"
```

`-N` disables curl's buffering. To resume after a disconnect:

```bash
curl -N "$BASE/api/jobs/<jid>/stream?token=$TOKEN&last_event_id=42"
```

Heartbeats arrive as `: hb\n\n` every 15s — useful as a liveness check.

### Allowlist a tool pattern

```bash
# global rule
curl -sS -X POST $BASE/api/allowlist \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"rule":"Bash(pytest:*)"}'

# project-scoped rule
curl -sS -X POST $BASE/api/allowlist \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"rule":"Edit(**/*.py)","project_id":"<pid>"}'
```

The runner gathers `(global) ∪ (this project's)` rules and passes them
as `--allowed-tools rule1,rule2,...`. There is no subtract semantics.

### Schedule a cron job

```bash
curl -sS -X POST $BASE/api/schedules \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"project_id":"<pid>","name":"morning ch","cron":"0 9 * * *","prompt":"daily build"}'
```

Validated against APScheduler's cron parser; 5-field or 6-field accepted.

### Configure a project's shared context

```bash
curl -sS -X PATCH $BASE/api/projects/<pid> \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"instructions":"Use snake_case.","skills":["init","review"],"context_paths":["~/notes"]}'
```

The server writes (or refreshes) a managed block in `<project.path>/CLAUDE.md`:

```
<!-- BEGIN agent-harness managed: do not edit -->
... rendered from instructions / skills / context_paths ...
<!-- END agent-harness managed -->
```

User content outside the fence is preserved on re-sync.

### Define a task and run it

```bash
# Task with no deps becomes 'ready' immediately.
T1=$(curl -sS -X POST $BASE/api/projects/<pid>/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"scaffold","prompt":"create module skeleton"}' | jq -r .id)

# Dependent task is 'pending' until t1 is 'done'.
T2=$(curl -sS -X POST $BASE/api/projects/<pid>/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg d "$T1" '{title:"tests",prompt:"add tests",depends_on:[$d]}')" | jq -r .id)

# Kick t1 (tasks never auto-run; you decide when each ready task starts).
curl -sS -X POST -H "Authorization: Bearer $TOKEN" $BASE/api/tasks/$T1/run
```

Job created from a task has `task_id` set; when it finalizes the task
runner records an outcome and flips `t1` to `done`, which makes `t2` flip
from `pending` to `ready` (not run — you still kick it).

### Decompose an ask into draft tasks (planner)

```bash
curl -sS -X POST $BASE/api/projects/<pid>/plan \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"ask":"Add a /jobs/retry endpoint with tests and docs."}'
# → {"task_ids":["...","..."],"raw":"...","error":null}
```

The planner is a normal claude job (visible under `/api/jobs`); it returns
once the job finishes. Drafts land as `status=pending`, `source=planner`,
ready for edit/confirm via `PATCH /api/tasks/{tid}`. Parse failures return
`error` + `raw` so you can copy/paste and create tasks manually.

### List outcomes (git checkpoints)

```bash
# Per task
curl -sS -H "Authorization: Bearer $TOKEN" $BASE/api/tasks/<tid>/outcomes | jq .

# Project-wide log
curl -sS -H "Authorization: Bearer $TOKEN" $BASE/api/projects/<pid>/outcomes | jq .
```

Each outcome row has `commit_sha`, `branch`, `summary` (assistant's closing
message), and `status` ∈ `success | failed`. `commit_sha`/`branch` are
captured via `git -C <project.path> rev-parse HEAD` at finalize time; if
the project path is not a git repo they are null.

---

## 3. Where state lives

```
~/.agent-harness/
├── config.toml              # auth_token, claude_path, defaults
├── harness.db               # SQLite (WAL): projects/jobs/turns/schedules/allowlist
│                            #               + tasks/task_dependencies/outcomes
└── logs/
    ├── server.out.log       # uvicorn stdout
    ├── server.err.log       # uvicorn stderr (most useful)
    └── jobs/<job-id>/
        ├── turn-0.jsonl     # one StreamEvent per line, in order
        ├── turn-1.jsonl
        └── ...
```

A turn's `turn-N.jsonl` is the source of truth for the transcript — the
broadcaster appends each event line-by-line and the SSE endpoint replays
from disk on connect. The DB stores high-level Job/Turn rows; the jsonl
files store every event.

### Cheap inspection

```bash
# How many events in turn 3?
wc -l ~/.agent-harness/logs/jobs/<jid>/turn-3.jsonl

# Last assistant text the runner produced
grep '"type":"assistant_text"' ~/.agent-harness/logs/jobs/<jid>/turn-*.jsonl | tail -1

# Cost so far across all turns
grep -h '"type":"turn_done"' ~/.agent-harness/logs/jobs/<jid>/turn-*.jsonl \
  | jq -s 'map(.cost_usd // 0) | add'
```

### Poking the DB

```bash
sqlite3 ~/.agent-harness/harness.db <<'SQL'
.headers on
.mode column
SELECT id, status, title, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;
SELECT id, status, pid, exit_code FROM turns WHERE job_id='<jid>' ORDER BY idx;
SQL
```

Safe to read concurrently with the running server (WAL mode). Avoid
writes — use the API.

---

## 4. Diagnostic playbook

### "I submitted a job and nothing happens"

1. Did it actually enqueue? `GET /api/jobs/<jid>` — status should be `queued`
   or `running` within ~1s.
2. Is the semaphore full? Default is `max_concurrent_jobs = 2`. Check
   `GET /api/jobs?` for other `running` jobs.
3. Is the runner spawning? `tail -f ~/.agent-harness/logs/server.err.log` —
   you'll see a `claude -p ...` invocation logged when a turn starts.
4. Is `claude` resolvable? `agent-harness serve` resolves `claude_path` from
   config, env, or `PATH`. If `which claude` returns nothing inside the
   launchd environment, set `claude_path` in `config.toml`.

### "Stop button doesn't kill the job"

The stop path is: API → `JobManager.stop()` → `ClaudeRunner.stop()` →
SIGTERM → 5s grace → SIGKILL. If the process refuses both:

```bash
# Find the pid (we persist it for visibility)
sqlite3 ~/.agent-harness/harness.db "SELECT job_id, idx, pid FROM turns WHERE pid IS NOT NULL AND ended_at IS NULL;"
ps -fp <pid>
kill -9 <pid>
```

After a manual kill the watchdog will *not* immediately finalize the turn
in the DB — restart the server (`launchctl kickstart -k …`) and the
reconciler will mark the job `stopped`.

### "Job's stuck at running but produces no output"

Idle watchdog should kill it after `idle_timeout_seconds` (default 600s,
per-project overridable). If you want a tighter loop for diagnosis:

```bash
# Lower the global default
sed -i '' 's/^idle_timeout_seconds.*/idle_timeout_seconds = 60/' ~/.agent-harness/config.toml
launchctl kickstart -k gui/$(id -u)/com.$(whoami).agent-harness
```

Or set it per-project via `PATCH /api/projects/<pid>` with
`{"idle_timeout_seconds": 60}`.

### "Tool keeps getting blocked even after I added a rule"

1. Confirm the rule is stored:
   `curl -sS -H "Authorization: Bearer $TOKEN" $BASE/api/allowlist`
2. Confirm the rule applies to this project: a project-scoped rule on
   project A won't help a job on project B.
3. Confirm the rule string syntax matches claude's expected form
   (`Bash(npm test:*)`, `Edit(**/*.py)`, `WebFetch(*)`). Quotes/escaping
   in JSON can sneak a backslash in — round-trip through the GET.
4. The runner computes `allowed_tools` once per turn — if you added the
   rule mid-turn, it won't apply until the *next* turn.

### "SSE just hangs on the iPhone"

- Open `http://<lan-ip>:8765/healthz` — if that fails, it's a network /
  service problem, not SSE.
- Heartbeats: with `curl -N $BASE/api/jobs/<jid>/stream?token=$TOKEN` you
  should see a `: hb` line within 15s. If not, the server isn't serving
  the stream — check `server.err.log`.
- Replay-then-live: subscribing replays from disk first. For a very long
  transcript this can take a moment; the first byte is `retry: 3000`,
  followed by replayed events with `id:` lines.
- Behind a reverse proxy? Set `X-Accel-Buffering: no` and disable
  response buffering (nginx `proxy_buffering off`).

### "Service crashed / I rebooted"

On startup the reconciler walks every `running` job, kills any still-alive
PID, and writes a synthetic `job_status: stopped` event into the jsonl
log. The transcript on disk is preserved. Expected after:
- Reboot
- `launchctl unload` then `load`
- Uvicorn `--reload` triggered by a code change in dev

### "TypeScript types out of sync with Pydantic"

The frontend reads `web/src/types/api.ts`, generated from `/api/openapi.json`.
After editing `server/agent_harness/schemas.py`:

```bash
# Live server present
cd web && npm run gen:types

# Offline (no running server)
uv run agent-harness gen-openapi web/openapi.json
cd web && npm run gen:types:offline
```

If the codegen drops a discriminated union variant (e.g. a new
`StreamEvent` subclass), check that it's wired into the
`StreamEvent = Annotated[Union[...]]` in `schemas.py` — only union members
land in the OpenAPI components.

---

## 4a. v2: worktrees, planning gates, integration

v2 tasks run in two phases by default (`mode=plan_then_execute`). The plan
turn runs in `project.path`; the execute turn runs in a per-task worktree
under `~/.agent-harness/worktrees/<task_id>` on branch `task/<task_id>`.

### List outstanding worktrees

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/projects/$PID/worktrees" | jq .
# [{"path":"/Users/me/.agent-harness/worktrees/abc","branch":"refs/heads/task/abc",
#   "head":"…","detached":false,"task_id":"abc"}, …]
```

`task_id=null` rows are **orphans** — worktrees the harness has no record
of (server killed mid-execute, or pre-existing manual worktrees).

### Clean up an orphan manually

The harness does **not** auto-delete unknown worktrees. From the project's
working dir:

```bash
PROJECT_PATH=$(curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/api/projects/$PID" | jq -r .path)

# Remove the worktree directory + git's internal record.
git -C "$PROJECT_PATH" worktree remove --force /path/to/worktree

# Delete the branch left behind, if any.
git -C "$PROJECT_PATH" branch -D task/<task_id>
```

`git worktree prune` is also safe if a worktree directory has been deleted
out from under git but the bookkeeping entry remains.

### Stuck integration job

An integration job whose merge conflicts the agent can't resolve stays at
`phase=integrating`. Followup turns on the same job can fix it:

```bash
curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/api/jobs/$JID/followup" \
  -d '{"prompt":"the conflict in src/auth.py was a comment-only divergence; keep main"}'
```

If you give up, `POST /api/jobs/$JID/stop` then `POST /api/tasks/$INT_TASK/cancel`.
The input tasks stay at `integration_status=conflict`; you can recreate
the integration task with a fresh `POST /api/projects/$PID/integrate`.

---

## 4b. Driver: autopilot + copilot

The driver is a separate process (`agent-harness-driver`) that listens to
the harness's event stream and dispatches actions (ack plans, run tasks,
integrate waves, retry failures) when a project's `autopilot_mode` is on.
When mode is off, the driver is uninvolved and the harness UI shows
copilot suggestions inline. See [docs/driver-design.md](./driver-design.md)
for the full design.

### Toggle autopilot

```bash
PID="$1"

# Turn on (auto-spawns the driver if not already connected; 409 if it can't).
curl -sS -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/api/projects/$PID/driver" -d '{"mode":"on"}'

# Turn off
curl -sS -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/api/projects/$PID/driver" -d '{"mode":"off"}'

# Status (per-project mode + driver connectivity + open warn/escalate notes)
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/api/projects/$PID/driver" | jq .

# Global driver status (which projects are on; is a driver connected)
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/api/driver/status" | jq .
```

### Run the driver manually

If you prefer to manage the driver yourself (or run it on a different
machine / under launchd), just start the binary — it'll connect to the
harness and idle until any project flips to `mode=on`.

```bash
# Same config.toml + AH_HOME the harness uses.
agent-harness-driver
```

Env overrides: `AGENT_HARNESS_URL` (default `http://127.0.0.1:8765`),
`AGENT_HARNESS_TOKEN` (defaults to the token in config.toml),
`AGENT_HARNESS_DRIVER_LOG` (default `INFO`).

Only one driver can be connected at a time — the second `agent-harness-driver`
to attach gets 409 and backs off in a reconnect loop.

### Driver logs

When the harness auto-spawns the driver, stdout/stderr land at
`~/.agent-harness/logs/driver.log`. When you start the driver manually,
logs go to your terminal (or wherever you redirect them).

### Driver notes

Every action the driver takes (or every escalation it surfaces) is
recorded in the `driver_notes` table.

```bash
# All notes for a project (newest first; limit 100)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/projects/$PID/driver/notes" | jq .

# Only un-acked escalations
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/projects/$PID/driver/notes?severity=escalate&acknowledged=false" | jq .

# Dismiss
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/driver/notes/$NID/acknowledge"
```

Notes older than 7 days that are already acknowledged are pruned lazily
on each `POST /api/driver/notes`.

### Stopping a stuck autopilot

If you're not sure what the driver is doing, flip mode off — the
harness stops emitting signals for that project at the source.
In-flight HTTP calls the driver already started complete normally, but
no new actions are dispatched.

```bash
curl -sS -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/api/projects/$PID/driver" -d '{"mode":"off"}'
```

If you want to fully kill the driver process (e.g., the harness-spawned
one shouldn't be sticking around because something is wrong), find it:

```bash
ps aux | grep agent-harness-driver
kill <pid>
```

The harness will not respawn it until you flip `mode=on` again.

---

## 5. Service ops

```bash
# Status
launchctl list | grep agent-harness

# Restart (no unload)
launchctl kickstart -k gui/$(id -u)/com.$(whoami).agent-harness

# Stop / start
launchctl unload ~/Library/LaunchAgents/com.$(whoami).agent-harness.plist
launchctl load   ~/Library/LaunchAgents/com.$(whoami).agent-harness.plist

# Tail logs
tail -F ~/.agent-harness/logs/server.err.log

# Full uninstall (preserves logs/db if you want them)
launchctl unload ~/Library/LaunchAgents/com.$(whoami).agent-harness.plist
rm ~/Library/LaunchAgents/com.$(whoami).agent-harness.plist
# rm -rf ~/.agent-harness   # uncomment to also drop state
```

### Re-running `scripts/install.sh`

Idempotent. Preserves `auth_token`, rebuilds the frontend, refreshes the
plist, reloads launchd. Run it after pulling new code.

---

## 6. From Claude Code itself

A project skill is shipped at `.claude/skills/agent-harness/SKILL.md`.
With Claude Code in this repo, ask things like:

- "Submit a job to agent-harness on the qft-book project: …"
- "What's the status of job `abc123` and tail its last turn."
- "Allow `Bash(pytest:*)` globally."

The skill drives the HTTP API via curl, using the token at
`~/.agent-harness/config.toml`. See the skill file for the full surface.
