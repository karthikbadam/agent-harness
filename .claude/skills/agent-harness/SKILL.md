---
name: agent-harness
description: Drive a locally running agent-harness server (the self-hosted Claude Code mobile harness on macOS) from Claude Code. Use when the user wants to submit a job, follow up, stop, check status, tail a stream, list/create projects or schedules, manage the tool allowlist, decompose an ask into tasks via the planner, run a task, list outcomes (git checkpoints), or run service-lifecycle commands (init, gen-token, gen-openapi, serve, launchctl). The harness exposes /api/* on http://127.0.0.1:8765 with a bearer token stored in ~/.agent-harness/config.toml.
---

# agent-harness skill

This skill talks to a **locally running** agent-harness server via its HTTP API,
and uses the `agent-harness` CLI for setup / service lifecycle. It does **not**
modify the user's repo state on its own — every action it takes is via the
documented API or CLI.

## Decide which surface to use

| Task                                       | Use         |
| ------------------------------------------ | ----------- |
| Submit/list/stop jobs, follow up, stream   | HTTP API    |
| Manage projects, schedules, allowlist      | HTTP API    |
| Define tasks, run tasks, list outcomes     | HTTP API    |
| Decompose an ask into draft tasks (planner) | HTTP API   |
| Inspect a turn's transcript jsonl          | Filesystem  |
| First-time install, rotate token, regen OpenAPI | CLI    |
| Start/stop the launchd service             | `launchctl` |

If the user describes a job/project/schedule operation, **default to HTTP**.
The CLI cannot reach into a running server.

## Bootstrap (do this first, every session)

Resolve the base URL and token before any HTTP call. Cache them in shell
variables for the rest of the turn — don't re-read on every request.

```bash
export AH_BASE="${AH_BASE:-http://127.0.0.1:8765}"
export AH_TOKEN="$(python3 -c 'import tomllib,os; p=os.path.expanduser("~/.agent-harness/config.toml"); print(tomllib.loads(open(p).read())["auth_token"])')"
curl -sS -f -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/me" >/dev/null || {
  echo "agent-harness not reachable at $AH_BASE — is the service running?" >&2
  exit 1
}
```

If the bootstrap fails, check `launchctl list | grep agent-harness` and
`tail ~/.agent-harness/logs/server.err.log` before calling any other endpoint.

## HTTP API surface (auth = `Authorization: Bearer $AH_TOKEN`)

All endpoints below are JSON in / JSON out unless noted. Base path `/api`.

### Projects

| Verb | Path | Body / Notes |
| --- | --- | --- |
| GET    | `/projects`              | list |
| POST   | `/projects`              | `{name, path, permission_mode?, dangerously_skip?, extra_claude_args?, idle_timeout_seconds?, instructions?, skills?, context_paths?}` |
| GET    | `/projects/{id}`         | |
| PATCH  | `/projects/{id}`         | any subset of the create fields |
| DELETE | `/projects/{id}`         | cascades to jobs/rules |

`permission_mode` ∈ `acceptEdits` (default) | `plan` | `default`.

**Shared context fields** (additive; jobs in the project inherit them):
- `instructions`: free-text rules synced into a managed block of
  `<path>/CLAUDE.md` — Claude Code reads it natively.
- `skills`: list of skill names auto-allowed for this project (each becomes
  `Skill(<name>)` in the allowlist, no permission prompt).
- `context_paths`: extra reference paths listed in the managed CLAUDE.md
  block.

### Jobs

| Verb | Path | Body / Notes |
| --- | --- | --- |
| GET    | `/jobs`                  | list, newest first |
| POST   | `/jobs`                  | `{project_id, prompt, title?}` — turn 0 spawns immediately |
| GET    | `/jobs/{id}`             | includes `turns[]` |
| POST   | `/jobs/{id}/followup`    | `{prompt}` — re-attaches to the same Claude session |
| POST   | `/jobs/{id}/stop`        | SIGTERM → SIGKILL after 5s |
| GET    | `/jobs/{id}/stream`      | SSE; use `?token=$AH_TOKEN` (EventSource can't set headers) |

Job `status` ∈ `queued | running | done | failed | stopped`. Turn `status`
mirrors plus exit_code/cost_usd when terminal.

### Schedules

| Verb | Path | Body / Notes |
| --- | --- | --- |
| GET    | `/schedules`             | |
| POST   | `/schedules`             | `{project_id, name, cron, prompt, enabled?}` (5- or 6-field cron) |
| PATCH  | `/schedules/{id}`        | subset of fields |
| DELETE | `/schedules/{id}`        | |

### Allowlist

| Verb | Path | Body / Notes |
| --- | --- | --- |
| GET    | `/allowlist`             | `?project_id=` to filter (returns global ∪ project) |
| POST   | `/allowlist`             | `{rule, project_id?}` (omit `project_id` for global) |
| DELETE | `/allowlist/{id}`        | |

Rule syntax matches Claude Code's allowlist: `Bash(pytest:*)`,
`Edit(**/*.py)`, `WebFetch(*)`, etc. A project's `skills` are merged in as
`Skill(<name>)` automatically — no need to add them here.

### Tasks

A task is a unit of work in a project with optional deps on other tasks.
Status flow:

```
pending  ──(all deps done)──▶  ready  ──(POST /run)──▶  running
running  ──(job finalize)──▶  done | failed
any      ──(POST /cancel)──▶  canceled
```

Tasks **do not auto-run** when deps satisfy — you kick them with `POST /run`.

| Verb | Path | Body / Notes |
| --- | --- | --- |
| POST   | `/projects/{pid}/tasks`     | `{title, prompt, depends_on?: [task_id], order_idx?}` |
| GET    | `/projects/{pid}/tasks`     | list with `status`, `depends_on`, `latest_outcome_id` |
| GET    | `/tasks/{tid}`              | one task |
| PATCH  | `/tasks/{tid}`              | edit `title`/`prompt`/`depends_on`/`order_idx`; confirms a planner draft (`pending` → `ready` if deps allow) |
| DELETE | `/tasks/{tid}`              | only when no jobs reference it (409 → use cancel) |
| POST   | `/tasks/{tid}/run`          | requires status `ready`; creates a job with `task_id` set |
| POST   | `/tasks/{tid}/cancel`       | stops the running job (if any) and marks the task canceled |

Task `source` is `manual` (created via POST) or `planner` (drafted by `/plan`).

### Planner

| Verb | Path | Body / Notes |
| --- | --- | --- |
| POST   | `/projects/{pid}/plan`     | `{ask}` — runs a one-off claude job that emits a JSON task array; inserts drafts with `status=pending`, `source=planner`. Returns `{task_ids[], raw?, error?}`. |

The planning conversation is itself a regular job (visible under `/api/jobs`,
streamed via SSE) — the endpoint returns once the job finishes.

### Outcomes

Checkpoints recorded after a task-bound job finalizes: commit sha, branch
(`git -C <project.path> rev-parse HEAD`), the assistant's last
`assistant_text` as `summary`, and `status` ∈ `success | failed`.

| Verb | Path | Body / Notes |
| --- | --- | --- |
| GET | `/tasks/{tid}/outcomes`        | per-task history, newest first |
| GET | `/projects/{pid}/outcomes`     | project-wide checkpoint log |

### Misc

- `GET /healthz` — unauthenticated; returns `{status:"ok",version:"..."}`.
- `GET /api/me` — auth ping.
- `GET /api/openapi.json` — full spec (use this if anything below is stale).

## Example flows

### Submit a job, wait for done, summarize cost

```bash
PID="$1"   # project id
PROMPT="$2"

JOB=$(curl -sS -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  -X POST "$AH_BASE/api/jobs" \
  -d "$(jq -nc --arg p "$PID" --arg q "$PROMPT" '{project_id:$p, prompt:$q}')")
JID=$(echo "$JOB" | jq -r .id)
echo "started $JID"

# Poll status (or use the SSE stream below for live)
while :; do
  S=$(curl -sS -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/jobs/$JID" | jq -r .status)
  [[ "$S" == "running" || "$S" == "queued" ]] || break
  sleep 5
done

curl -sS -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/jobs/$JID" \
  | jq '{status, turns:[.turns[] | {idx,status,cost_usd,exit_code}]}'
```

### Stream live events (one-shot)

```bash
curl -N "$AH_BASE/api/jobs/$JID/stream?token=$AH_TOKEN"
```

Lines come as SSE: `id: <seq>`, `event: <type>`, `data: <json>`, blank line.
Heartbeat comments `: hb` arrive every 15s — drop them when parsing.

To resume a disconnected stream, pass `&last_event_id=<seq>`.

### Tail a turn from disk (no server needed)

```bash
JID="$1"; N="$2"
tail -F "$HOME/.agent-harness/logs/jobs/$JID/turn-$N.jsonl"
```

Each line is one `StreamEvent` (see `server/agent_harness/schemas.py`).

### Configure a project's shared context

```bash
PID="$1"

curl -sS -X PATCH "$AH_BASE/api/projects/$PID" \
  -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  -d '{
        "instructions": "Use snake_case. Run pytest before committing.",
        "skills": ["init", "review"],
        "context_paths": ["~/notes/style-guide.md"]
      }'
```

The server writes a managed block into `<project.path>/CLAUDE.md` so Claude
Code reads it natively; existing user content in CLAUDE.md outside the
fence is preserved.

### Decompose an ask into draft tasks

```bash
PID="$1"
ASK="Add a /jobs/retry endpoint with tests and docs."

curl -sS -X POST "$AH_BASE/api/projects/$PID/plan" \
  -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg a "$ASK" '{ask:$a}')" \
  | jq '.'
# → {"task_ids":["...","..."],"raw":"...","error":null}
```

Drafts land as `status=pending`, `source=planner`. Review and confirm them
by either editing (`PATCH /tasks/{tid}`) or running directly once their deps
are satisfied.

### Run a task DAG end to end

```bash
PID="$1"

# 1. Create a chain: t1 → t2 → t3
T1=$(curl -sS -X POST "$AH_BASE/api/projects/$PID/tasks" \
  -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"t1","prompt":"scaffold module"}' | jq -r .id)

T2=$(curl -sS -X POST "$AH_BASE/api/projects/$PID/tasks" \
  -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg d "$T1" '{title:"t2",prompt:"add tests",depends_on:[$d]}')" | jq -r .id)

T3=$(curl -sS -X POST "$AH_BASE/api/projects/$PID/tasks" \
  -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg d "$T2" '{title:"t3",prompt:"write docs",depends_on:[$d]}')" | jq -r .id)

# 2. Kick t1 — t2 only becomes 'ready' after t1 is 'done'.
curl -sS -X POST -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/tasks/$T1/run"

# 3. Wait, then kick t2; repeat for t3.
while [[ "$(curl -sS -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/tasks/$T2" | jq -r .status)" != "ready" ]]; do sleep 5; done
curl -sS -X POST -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/tasks/$T2/run"
```

### Inspect outcomes (checkpoints)

```bash
curl -sS -H "Authorization: Bearer $AH_TOKEN" "$AH_BASE/api/projects/$PID/outcomes" \
  | jq '[.[] | {task_id, commit_sha, branch, status, created_at}]'
```

Each row is a git checkpoint produced when a task-bound job finished.

### Allow a tool rule and retry a blocked job

```bash
# rule from the tool_blocked event's suggested_rule field
curl -sS -X POST -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  "$AH_BASE/api/allowlist" -d '{"rule":"Bash(pytest:*)"}'

# rerun the same prompt as a followup
curl -sS -X POST -H "Authorization: Bearer $AH_TOKEN" -H "Content-Type: application/json" \
  "$AH_BASE/api/jobs/$JID/followup" -d '{"prompt":"retry"}'
```

## CLI surface (setup / lifecycle only)

These touch `~/.agent-harness/` and the launchd plist; they do **not** talk
to a running server.

```bash
agent-harness init                 # create AH_HOME + DB
agent-harness gen-token            # print existing or generate one
agent-harness gen-token --force    # rotate
agent-harness gen-openapi <path>   # dump OpenAPI offline (no running server needed)
agent-harness serve                # start uvicorn in foreground (don't run if launchd has it)
```

`agent-harness serve` and the launchd service are mutually exclusive on the
default port. Don't run `serve` if `launchctl list | grep agent-harness`
shows a running entry — you'll get `Address already in use`.

### Service lifecycle (macOS launchd)

```bash
LBL="com.$(whoami).agent-harness"

launchctl list | grep agent-harness          # status
launchctl kickstart -k gui/$(id -u)/$LBL     # restart
launchctl unload ~/Library/LaunchAgents/$LBL.plist
launchctl load   ~/Library/LaunchAgents/$LBL.plist
```

## Diagnostic shortcuts

When the user asks "why is X broken":

1. `curl -sS $AH_BASE/healthz` — is the server up at all?
2. `tail -n 200 ~/.agent-harness/logs/server.err.log` — most failure modes
   surface here (claude not found, port collision, DB lock, runner crash).
3. `sqlite3 ~/.agent-harness/harness.db 'SELECT id,status,title,created_at FROM jobs ORDER BY created_at DESC LIMIT 10;'`
4. `ls ~/.agent-harness/logs/jobs/<jid>/` — turn jsonl files; one per `claude -p` invocation.
5. For a hung job, look up the PID from the DB:
   `sqlite3 ~/.agent-harness/harness.db "SELECT job_id, idx, pid FROM turns WHERE pid IS NOT NULL AND ended_at IS NULL;"`
   then `ps -fp <pid>`.

The full diagnostic playbook lives at `docs/operations.md`; consult it when
something doesn't match the surface listed here.

## Don'ts

- Don't write directly to `harness.db` — go through the API. Reads via
  `sqlite3` are fine (WAL).
- Don't `kill -9` runner processes unless the API's stop endpoint refused
  twice. The runner does graceful TERM → 5s → KILL on its own.
- Don't run `agent-harness serve` while launchd has the service loaded.
- Don't expose the API beyond LAN without TLS in front of it — auth is a
  single bearer token, no rate-limiting.
- Don't store the bearer token in shell history or commit it. Always
  source it from `~/.agent-harness/config.toml`.
