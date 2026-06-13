# agent-harness

A self-hosted mobile harness for [Claude Code](https://docs.claude.com/) on
macOS. Submit / monitor / manage jobs from your iPhone — from anywhere, not just
home WiFi — with live event streaming.

Single user, runs as a launchd service. Wraps
`claude -p --output-format stream-json` and exposes a mobile-friendly web UI.

Because the harness runs `claude` with shell access on your Mac under your
Claude account, it is treated as a high-value target and **never listens on the
LAN or the public internet**. It binds to `127.0.0.1` only; remote access goes
through [Tailscale](https://tailscale.com) (a private WireGuard network), with
HTTPS terminated by Tailscale Serve. Two independent factors guard it: an
enrolled device on your tailnet, **and** the bearer token.

```
iPhone (Tailscale, anywhere)
       │  HTTPS over WireGuard (tailnet only — no public exposure)
       ▼
Mac:  tailscale serve  ──►  127.0.0.1:8765
       │                      (uvicorn + FastAPI + APScheduler)
       └── ~/Library/LaunchAgents/com.you.agent-harness.plist
              │
              └── claude -p --output-format stream-json ...
                     │
                     └── per-turn jsonl in ~/.agent-harness/logs/jobs/<id>/
```

## Prerequisites

The harness shells out to a `claude` CLI that speaks
`claude -p --output-format stream-json`. Install it once before running
`./scripts/install.sh`:

```bash
npm install -g @anthropic-ai/claude-code
claude --version    # confirm it's on PATH
```

Then pick a backend:

- **Anthropic API** (default): run `claude` once and follow the interactive
  sign-in, or set `ANTHROPIC_API_KEY` in the environment.
- **Ollama** (open-source, local models): follow Ollama's Claude Code
  integration guide at
  <https://docs.ollama.com/integrations/claude-code> — same `claude` binary,
  pointed at a local Ollama server instead of the Anthropic API.

If `claude` isn't on PATH after install, set `claude_path` in
`~/.agent-harness/config.toml` or `AH_CLAUDE_PATH` (see Configuration below).

## Quick start

```bash
# One-time prerequisites:
brew install uv                       # install script will check
brew install tailscale                # or the Tailscale.app
tailscale up                          # log in; do the same on your iPhone
# In the Tailscale admin console, enable MagicDNS and HTTPS Certificates.

git clone <repo> agent-harness
cd agent-harness
./scripts/install.sh
```

The installer is idempotent — re-running it preserves your token. It binds the
service to `127.0.0.1`, runs `tailscale serve` to front it with tailnet-only
HTTPS, and prints a URL of the form
`https://<mac>.<tailnet>.ts.net/auth#token=<x>` — open that on any device on
your tailnet (works beyond your WiFi) and you're in. The token rides in the URL
*fragment* (`#`), so it never reaches the server or its logs; the page swaps it
for an `HttpOnly` cookie and clears the URL.

## Development

```bash
./scripts/dev.sh
# Backend on :8765 (uvicorn --reload, also serves the built SPA at /)
# Vite runs `build --watch` into web/dist/ — no separate dev port.
# Open http://localhost:8765
```

To restart a detached server (picks up code changes without holding a
terminal open):

```bash
./scripts/restart.sh
# Logs at ~/.agent-harness/logs/server.{out,err}.log
#                       and ~/.agent-harness/logs/vite.{out,err}.log
```

Hard-refresh the browser to pick up frontend changes (the bundle hash changes
on each rebuild, so it busts the cache).

Run tests:

```bash
uv run pytest -q                              # 88 tests
cd web && npm run typecheck && npm run build
```

Regenerate TypeScript types after editing Pydantic schemas:

```bash
# Option A: hit a live server
cd web && npm run gen:types

# Option B: dump spec offline
uv run agent-harness gen-openapi web/openapi.json
cd web && npm run gen:types:offline
```


## Walkthrough

1. Run `./scripts/install.sh` once.
2. Open the printed `https://<mac>.<tailnet>.ts.net/auth#token=…` URL on your
   iPhone (with Tailscale connected — works on cellular or any network).
3. Create a project pointing at your repo (Jobs page → "+" → New project).
4. Submit a job: "build chapter 0 of the QFT book".
5. Watch live `tool_use` / `tool_result` / `assistant_text` events stream into
   the transcript.
6. Submit a followup turn ("now use Susskind notation"); it continues the
   same session via `claude --resume <session_id>`.
7. Hit Stop on a long-running job → SIGTERM → SIGKILL after 5s.
8. Schedules page → create a cron schedule that fires a job daily at 9am.
9. Hit a permissions block → tap "Allow Bash(pytest:*) and retry"
   → claude reruns the same prompt with the rule in the allowlist.
10. Reboot the Mac → launchd brings the service back → any
    `running` job in the DB is reconciled to `stopped`.

### Projects, Tasks, Outcomes
Beyond one-shot jobs, projects can carry shared context and be driven as
multi-step plans:

- **Project context** (`instructions`, `skills`, `context_paths`) is synced
  into a managed block in `<path>/CLAUDE.md` **and `<path>/AGENTS.md`** (Codex
  reads the latter), so every job inherits it natively whichever provider runs.
  Skills are also merged into the allowlist as `Skill(<name>)`.
- **Tasks** decompose a complex ask into smaller units with DAG dependencies.
  Status flow: `pending → ready → running → done | failed | canceled`.
  Tasks do **not** auto-run when their dependencies satisfy — you kick each
  ready task with `POST /api/tasks/{id}/run`.
- **Planner**: `POST /api/projects/{id}/plan {ask}` runs a one-off claude
  job that drafts a task list (status `pending`, source `planner`) for you
  to edit and confirm.
- **Outcomes** are checkpoints recorded when a task-bound job finalizes:
  `commit_sha`, `branch`, the assistant's closing summary, and
  `success | failed`. List with `GET /api/projects/{id}/outcomes`.

See the [agent-harness skill](.claude/skills/agent-harness/SKILL.md) for curl
recipes covering each of these.

### Plan-then-execute, parallel worktrees, orchestrator MCP

The default task lifecycle now gates on a planning turn:

```
pending → ready → running (planning) → awaiting_ack → running (executing) → done
```

- **Plan turn 0** runs in `project.path` (read-only by convention) and ends
  parked at `phase=awaiting_ack` with an `Outcome(kind=plan)` containing the
  plan text. A followup on that job acks the plan.
- **Execute turn 1** runs in a per-task git worktree at
  `~/.agent-harness/worktrees/<task_id>` on branch `task/<task_id>` so
  concurrent executes don't fight over the project's git index.
- **Integration** is itself a synthetic agent task: `POST /api/projects/{id}/integrate
  {task_ids,target_branch?}` creates a one-shot task whose prompt merges
  the listed worktree branches into `target_branch`. On success the input
  worktrees + branches are cleaned up.
- **Structural ops**: `POST /api/tasks/{id}/split` and
  `POST /api/tasks/merge` reshape the DAG before any run.
- **Orchestrator MCP**: the same surface is exposed as a typed MCP tool set
  at `/mcp` (HTTP) and as a stdio binary `agent-harness-mcp`. Connect a
  separate Claude Code session to it from outside the harness — jobs spawned
  by the harness do **not** have the orchestrator tools auto-injected.

Opt out per task with `mode=one_shot` — skip planning, run a single turn
directly in `project.path` (no worktree, no ack gate). Useful for trivial
edits that don't need isolation; concurrent one-shot tasks share the
project's git index, so don't fan them out in parallel.

### Autopilot driver

For unattended multi-hour runs, set `Project.autopilot_mode='on'` and the
external `agent-harness-driver` process (auto-spawned by default) reacts to
harness events and dispatches ack / run / integrate / retry actions without
human input. Same decision logic powers `GET /api/projects/{id}/driver/
suggestions` in copilot mode — surfacing one-tap next actions in the UI.
Every action lands as a `DriverNote` for audit + escalation. Details in
`docs/driver-design.md`.

## How it works

### Agent providers (Claude + Codex)
Each project, ad-hoc job, and task chooses which agent runs it:

- `claude` — Claude Code for every phase (the default; existing installs are
  unchanged).
- `codex` — the Codex CLI (`codex exec`) for every phase.
- `auto` — Claude for the read-only **plan** phase, Codex for the write phases
  (execute / integrate) and ad-hoc jobs.

The provider is resolved **once per job** from the project default (or a
job/task override) and the job's `kind`, then frozen on the job as
`agent_provider` (`claude` | `codex`). Because each phase is its own job, the
resolved value is well-defined per job. Precedence: job override → task
override → project default → `claude`. Set it in the UI's Auto/Claude/Codex
picker (new-project composer, project prompt composer, ad-hoc job composer, and
the project-settings popover), or via the API field `agent_provider` on
projects, jobs, tasks, and `POST /plan`.

Codex differs from Claude in a few load-bearing ways the runner handles:
- **Sandbox, not allowlist.** Codex gates via `--sandbox` mode
  (`read-only` for plan, `workspace-write` otherwise; `dangerously_skip` maps to
  `--dangerously-bypass-approvals-and-sandbox`). The per-tool allowlist and the
  `tool_blocked` "allow and retry" flow are **Claude-only** and inert for Codex.
- **Resume is a subcommand:** followups spawn `codex exec resume <thread_id>`,
  not a `--resume` flag. The `thread.started.thread_id` is captured as the
  session id.
- Codex reports **no per-turn cost** (`cost_usd` is null), and an `error` /
  `turn.failed` event (e.g. a usage-limit) is mapped to a non-zero `turn_done`
  so the task fails cleanly even though the CLI itself exits 0.

### Stream-json parser
Each `claude -p --output-format stream-json` line is parsed into a typed
event (`tool_use`, `tool_result`, `assistant_text`, `turn_done`, `job_status`,
`tool_blocked`). Unknown event types are dropped. Each parsed event is
persisted to `~/.agent-harness/logs/jobs/<job-id>/turn-N.jsonl` with a
monotonic `seq`, then fanned out to SSE subscribers.

### Followup turns
A job is a conversation; a turn is one `claude -p` invocation. Turn 0
captures `session_id` from the first `system/init` event. Followup turns
spawn `claude -p "<prompt>" --resume <session_id>`. Per-job lock serializes
turns on the same job; global semaphore (default 2) caps concurrent jobs.

### Permission strategy
Default: `--permission-mode acceptEdits` + `--allowed-tools` list from your
allowlist rules (`Bash(npm test:*)`, `Edit(**/*.py)`, etc.). When a tool is
refused, we synthesize a `tool_blocked` event with a suggested rule; the UI
shows a one-tap "Allow rule and retry" button. Per-project escape hatch:
`dangerously_skip=true` skips the gate entirely. **Use only on repos you
fully trust.**

### Idle watchdog
If a turn produces no events for `idle_timeout_seconds` (default **600s /
10 min**, configurable per project), the watchdog SIGTERMs the claude
process and marks the turn `stopped`.

### Reconciliation
On startup we walk every job in `running` status. If its turn's PID is alive
we kill it (we can't reattach to its stdout from a fresh parent). Then we
mark the job `stopped` and emit a synthetic `job_status` event so the UI's
replay shows the terminal state.

## Configuration

`~/.agent-harness/config.toml`:

```toml
auth_token = "..."                # set by `agent-harness gen-token`

host = "127.0.0.1"                # default; loopback only. Tailscale Serve
                                  # proxies the tailnet to this. Do NOT widen to
                                  # 0.0.0.0 — that exposes an RCE-capable service
                                  # to the LAN.
claude_path = "/usr/local/bin/claude"   # optional; default is `which claude`
default_claude_args = ["--model", "claude-opus-4-7"]  # appended to every claude job
codex_path = "/usr/local/bin/codex"     # optional; default is `which codex`
default_codex_args = []                  # appended to every codex job (codex CLI syntax)
max_concurrent_jobs = 2
idle_timeout_seconds = 600        # 10 min default
log_retention_days = 30
```

`codex_path` / `default_codex_args` are only needed if any project uses the
`codex` or `auto` provider. Note that launchd's PATH is minimal, so set
`codex_path` to an absolute path (`which codex`) rather than relying on PATH.

### Auth & transport

- **Bearer token** — scripts/curl/MCP send `Authorization: Bearer <token>`.
- **Session cookie** — the browser exchanges the token once (`POST /api/session`)
  for an `HttpOnly`, `Secure`, `SameSite=Strict` cookie. SSE/EventSource (which
  can't set headers) authenticates with this cookie, so the token never appears
  in a URL.
- **TLS** is terminated by Tailscale Serve at the tailnet edge; the loopback hop
  to uvicorn is plain HTTP on `127.0.0.1` (safe — it never leaves the machine).
  For end-to-end TLS instead, drop a cert at `~/.agent-harness/server.pem` +
  `server-key.pem` and `agent-harness serve` will pick them up automatically.

Per-project overrides (set via the API or Settings UI):
- `agent_provider`: `claude` (default), `codex`, `auto` — see
  [Agent providers](#agent-providers-claude--codex).
- `extra_claude_args`: list of args appended **after** the global default
  (last-wins for claude CLI). Claude-only — never forwarded to Codex.
- `permission_mode`: `acceptEdits` (default), `plan`, `default` (Claude-only).
- `dangerously_skip`: bool.
- `idle_timeout_seconds`: int | null (null = use global).
- `instructions`: free-text rules synced into `<path>/CLAUDE.md` and
  `<path>/AGENTS.md` (so they reach Claude and Codex respectively).
- `skills`: list of skill names auto-allowed (`Skill(<name>)`).
- `context_paths`: extra reference paths surfaced in the CLAUDE.md block.

Environment overrides (`AH_*` prefix): `AH_HOME`, `AH_AUTH_TOKEN`,
`AH_CLAUDE_PATH`, `AH_CODEX_PATH`, `AH_IDLE_TIMEOUT_SECONDS`, `AH_PORT`,
`AH_HOST`, `AH_MAX_CONCURRENT_JOBS`, `AH_LOG_RETENTION_DAYS`.

## Service commands

```bash
launchctl list | grep agent-harness        # status
launchctl unload ~/Library/LaunchAgents/com.<you>.agent-harness.plist  # stop
launchctl load   ~/Library/LaunchAgents/com.<you>.agent-harness.plist  # start
tail -f ~/.agent-harness/logs/server.err.log                            # logs
```

To uninstall completely:

```bash
launchctl unload ~/Library/LaunchAgents/com.<you>.agent-harness.plist
rm ~/Library/LaunchAgents/com.<you>.agent-harness.plist
rm -rf ~/.agent-harness
```

## License

Personal-use tool; no license attached. Fork freely.
