# agent-harness

A self-hosted mobile harness for [Claude Code](https://docs.claude.com/) on
macOS. Submit / monitor / manage jobs from your iPhone over your home WiFi,
with live event streaming and push notifications when a job finishes.

Single user, LAN-only, runs as a launchd service. Wraps
`claude -p --output-format stream-json` and exposes a mobile-PWA frontend
over HTTPS.

```
iPhone (Safari → home-screen PWA)
       │  HTTPS over LAN
       ▼
Mac:  ~/Library/LaunchAgents/com.you.agent-harness.plist
       │
       └── uvicorn + FastAPI + APScheduler
              │
              └── claude -p --output-format stream-json ...
                     │
                     └── per-turn jsonl in ~/.agent-harness/logs/jobs/<id>/
```

## Quick start

```bash
git clone <repo> agent-harness
cd agent-harness
brew install mkcert nss      # one-time; install script will check
./scripts/install.sh
```

The installer is idempotent — re-running it preserves your token, VAPID keys,
and certs. It prints a URL of the form
`https://<lan-ip>:8765/auth?token=<x>` — that's how the iPhone receives the
auth token.

## iPhone setup (one-time)

1. **Trust the mkcert CA.** Email yourself `mkcert -CAROOT`/`rootCA.pem`.
   Open the attachment on your iPhone, install the profile.
   Settings → General → VPN & Device Management → install.
   Then Settings → General → About → Certificate Trust Settings → enable
   "mkcert development CA".
2. **Open the URL** the installer printed in Safari. You should see the
   harness UI (a token chip flashes and you're in).
3. **Add to Home Screen.** Tap Share → Add to Home Screen.
4. **Open from the home-screen icon.** iOS requires PWA-launched-from-home
   for service workers + push to work. Don't tap the URL in Safari again —
   always use the icon.
5. **Enable notifications.** Settings → Notifications → Enable. (This pops a
   permission dialog the first time.)

## The 11-step walkthrough

1. Run `./scripts/install.sh` once.
2. Open the printed URL on your iPhone (same WiFi), trust the cert, add to
   home screen.
3. From the home-screen app: create a project pointing at your repo
   (Jobs page → "+" → New project).
4. Submit a job: "build chapter 0 of the QFT book".
5. Watch live `tool_use` / `tool_result` / `assistant_text` events stream into
   the transcript.
6. Get a push notification when it finishes.
7. Submit a followup turn ("now use Susskind notation"); it continues the
   same session via `claude --resume <session_id>`.
8. Hit Stop on a long-running job → SIGTERM → SIGKILL after 5s.
9. Schedules page → create a cron schedule that fires a job daily at 9am.
10. Hit a permissions block → tap "Allow Bash(pytest:*) and retry"
    → claude reruns the same prompt with the rule in the allowlist.
11. Reboot the Mac → launchd brings the service back → any
    `running` job in the DB is reconciled to `stopped`.

## How it works

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
vapid_public_key = "..."          # set by `agent-harness gen-vapid`
vapid_private_key = "..."
vapid_subject = "mailto:you@localhost"

claude_path = "/usr/local/bin/claude"   # optional; default is `which claude`
default_claude_args = ["--model", "claude-opus-4-7"]  # appended to every job
max_concurrent_jobs = 2
idle_timeout_seconds = 600        # 10 min default
log_retention_days = 30
```

Per-project overrides (set via the API or Settings UI):
- `extra_claude_args`: list of args appended **after** the global default
  (last-wins for claude CLI).
- `permission_mode`: `acceptEdits` (default), `plan`, `default`.
- `dangerously_skip`: bool.
- `idle_timeout_seconds`: int | null (null = use global).

Environment overrides (`AH_*` prefix): `AH_HOME`, `AH_AUTH_TOKEN`,
`AH_CLAUDE_PATH`, `AH_IDLE_TIMEOUT_SECONDS`, `AH_PORT`, `AH_HOST`,
`AH_MAX_CONCURRENT_JOBS`, `AH_LOG_RETENTION_DAYS`.

## Development

```bash
./scripts/dev.sh
# Backend on :8765 (uvicorn --reload)
# Frontend on :5173 (vite, proxies /api to :8765)
```

Run tests:

```bash
. .venv/bin/activate     # or whatever venv you set up
pytest -q                # 88 tests
cd web && npm run typecheck && npm run build
```

Regenerate TypeScript types after editing Pydantic schemas:

```bash
# Option A: hit a live server
cd web && npm run gen:types

# Option B: dump spec offline
agent-harness gen-openapi web/openapi.json
cd web && npm run gen:types:offline
```

## Troubleshooting

**"Your connection is not private" on iPhone.**
The mkcert root CA isn't trusted. Email yourself `mkcert -CAROOT`/`rootCA.pem`,
install the profile, then enable it under Settings → General → About →
Certificate Trust Settings.

**Push notifications never arrive.**
Three things to check:
1. You opened the app from the **home-screen icon**, not Safari. iOS
   requires standalone display mode.
2. Settings → Notifications → harness → Enable.
3. Server's VAPID keys are configured (`agent-harness gen-vapid`).
4. Settings page shows "Push notifications: Enable" — tap it once.

**Job stuck running, Stop button doesn't help.**
The harness sent SIGTERM and waited 5s before SIGKILL. If the claude
subprocess refused both, check `ps -fp <pid>` to confirm — then
`kill -9 <pid>` manually. Also check the idle watchdog (`idle_timeout_seconds`)
will eventually catch it.

**Reconciler ran on startup and marked things stopped.**
Expected after a Mac reboot or a manual `launchctl unload` then `load`. We
can't reattach to a subprocess's stdout from a fresh parent, so any in-flight
job is killed and marked stopped. The transcript jsonl is preserved.

**Permission keeps getting blocked even after I added a rule.**
Allowlist rules are passed via `--allowed-tools <rule1>,<rule2>,...`. Patterns
follow claude's syntax: `Bash(npm test:*)`, `Edit(**/*.py)`, `WebFetch(*)`.
If you add a global rule but the project also has a more restrictive one,
both lists merge — there's no "subtract" semantics.

**`agent-harness serve` won't start: "claude CLI not found".**
Set `claude_path` in `config.toml` or `AH_CLAUDE_PATH` env. The error
message includes the search order.

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

## Project layout

```
agent-harness/
├── pyproject.toml
├── server/
│   ├── agent_harness/
│   │   ├── claude.py          # subprocess + stream-json parser
│   │   ├── jobs.py            # JobManager (semaphore, locks, watchdog)
│   │   ├── broadcaster.py     # replay-then-live SSE pub/sub
│   │   ├── reconcile.py       # orphan-job recovery on startup
│   │   ├── notify.py          # pywebpush sender (auto-prunes dead subs)
│   │   ├── schedule_service.py# APScheduler integration
│   │   └── routes/            # FastAPI routers
│   └── tests/                 # pytest (88 tests)
├── web/
│   ├── src/
│   │   ├── hooks/useJobStream.ts   # EventSource + Last-Event-ID reconnect
│   │   ├── sw.ts                   # push + notificationclick
│   │   ├── components/TurnTranscript.tsx
│   │   └── pages/{Jobs,JobDetail,Schedules,Settings,AuthGate}.tsx
│   └── types/api.ts          # generated from /api/openapi.json
└── scripts/
    ├── install.sh
    ├── dev.sh
    └── launchd.plist.tmpl
```

## License

Personal-use tool; no license attached. Fork freely.
