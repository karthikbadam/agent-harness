# Agent-Loop — Status & Handoff

_Last updated: 2026-05-30. Branch: `feat/agent-loop`._

Resume point for the autoresearch effort: make the harness run a long-running,
autonomous RL "autoresearch" loop (train a small model to pick the best tool
call), inspired by Karpathy's autoresearch repo (`~/Code/autoresearch`).

**Core constraint (do not violate):** the *agent-harness itself* must perform
the RL research work. We build harness features and scaffold the project, then
submit the task TO the harness. We do not run the RL training by hand.

## Plan reference

Full plan: [`docs/agent-loop-plan.md`](./agent-loop-plan.md). This file is the
live status layer on top of it.

## Done (Tier 1 — all committed on `feat/agent-loop`)

- **F1 — heartbeat-aware idle watchdog** (`23b5018`). The idle watchdog now
  skips idle ticks while tool calls are in flight, so long `Bash` calls (e.g. a
  5-min training run) no longer get SIGTERM'd. Counter `inflight_tools` in
  `server/agent_harness/jobs.py`; incremented on `ToolUseEvent`, decremented on
  `ToolResultEvent`.
- **F2 — per-task idle timeout override** (`7b615b2`). `Task.idle_timeout_seconds`
  (nullable). Resolution order: task override → project → global default. `0`
  disables the watchdog for that task. Wired through models, db ALTER, schemas
  (TaskCreate/Update/Out), routes/tasks.py.
- **F3 — artifact store** (`195fbe0`). An agent registers a file it produced
  (progress graph, results table, report) against a task; the file is copied
  into `AH_HOME/artifacts/<task_id>/<name>` so it survives worktree cleanup.
  - Model: `Artifact` in `models.py`. Schemas: `ArtifactCreate`/`ArtifactOut`.
  - Routes (`server/agent_harness/routes/artifacts.py`):
    - `POST /api/tasks/{task_id}/artifacts` — register+copy. Re-registering the
      same `name` updates in place (so a loop overwriting `progress.png` shows
      one current artifact, not N).
    - `GET /api/tasks/{task_id}/artifacts` — list, newest-first.
    - `GET /api/artifacts/{artifact_id}/download` — FileResponse.
    - `DELETE /api/artifacts/{artifact_id}`.
  - Security: `_resolve_source` confines the source path to the task's job
    cwd / worktree / project root; rejects traversal + absolute paths outside
    the tree (prevents prompt-injected exfiltration of e.g. `/etc/passwd`).
  - Tests: `server/tests/test_artifacts.py` (4 passing).

**Live server state:** running via `python -m uvicorn agent_harness.main:app
--reload --host 0.0.0.0 --port 8765 --app-dir server` (PID was 26561; `--reload`
picks up edits). Confirmed all three artifact routes present in live
`/api/openapi.json`.

- **Harness URL for phone monitoring:** `http://10.0.0.185:8765`
- **Bearer token:** `7sRtkf2ocDsPVlsXdlAJhkZzhMX8HjjH` (from `~/.agent-harness/config.toml`)
- **NOT** managed by launchd — it's a plain foreground uvicorn process.

## Not done — next steps

### Task #6 — Scaffold the RL tool-picker autoresearch project (`~/Code/tool-picker-rl`)

Mirror Karpathy's autoresearch layout (`~/Code/autoresearch`: `program.md`,
`prepare.py` [read-only], `train.py` [the single edit surface], `analysis.ipynb`,
`progress.png`, `results.tsv`, `pyproject.toml`, `README.md`). Adapt to our task:

- **Task:** contextual-bandit / RL tool-picker. Given a task context + K
  candidate tool calls (feature vectors, hidden quality scalar), a small policy
  (MLP or tiny transformer, torch, MPS-friendly) scores candidates; trained with
  REINFORCE / policy gradient. Reward = picked the argmax-quality candidate.
- **Metric:** validation **pick-accuracy** (higher better → chart shows
  running-MAX, unlike autoresearch's running-MIN bpb). Script must print a
  parseable line, e.g. `VAL_ACC=0.8123`.
- **Files to create:**
  - `prepare.py` — deterministic synthetic dataset into `~/.cache/tool-picker-rl/`
    (so iterations are comparable). Read-only per program.md. Holds the eval fn.
  - `train.py` — working baseline the loop improves. Single edit surface.
  - `program.md` — loop instructions (see Karpathy's: fixed time budget,
    edit-run-grep-record-keep/discard-via-git, NEVER STOP, results.tsv schema).
  - `analysis.py` — reads `results.tsv`, writes `progress.png` (val_acc per
    iteration + running-best line). Use a script, not a notebook, so the loop
    can run it headless.
  - `citations.md` — bibliography; append the paper/work when a technique is
    used (REINFORCE/Williams 1992, baseline subtraction, advantage, etc.).
  - `pyproject.toml` — deps: torch, numpy, matplotlib. `.python-version`,
    `.gitignore` (ignore `results.tsv`, `run.log`, `progress.png`, cache).
  - `README.md` — context + how the loop runs.

### Task #7 — Per-iteration `progress.png` + `citations.md`

Each loop iteration must: append a row to `results.tsv`, regenerate
`progress.png` via `analysis.py`, register `progress.png` as a **graph**
artifact against the task (`POST /api/tasks/{tid}/artifacts` with
`{"kind":"graph","path":"progress.png","meta":{"iteration":N}}`), and append any
newly-used technique to `citations.md`. The re-register-in-place behavior (F3)
means the phone view always shows the latest graph.

### Task #8 — Dry run end-to-end

1. `git init` the `tool-picker-rl` repo; run `uv run prepare.py` once.
2. Register it as a harness project: `dangerously_skip=true` (training needs
   Bash), high or `0` `idle_timeout_seconds` (long unattended turns; F2),
   autopilot/driver mode `on`.
3. Submit the autoresearch task (mode likely `execute_only` or `one_shot`,
   pointed at `program.md` as the "skill").
4. Watch 2–3 iterations from the phone at `http://10.0.0.185:8765` — confirm
   tool-call progress streams, `progress.png` artifact updates each iteration,
   citations grow.

The `agent-harness` skill drives the server (submit job, follow up, tail stream,
list outcomes/artifacts).

## Open questions / watch-outs

- torch on MPS inside the harness's `claude` subprocess: the project needs its
  own `uv` venv (`pyproject.toml`). Confirm torch+MPS imports in that env before
  the dry run.
- The 5-min fixed budget from autoresearch may be too long per iteration for a
  quick dry run — consider a shorter budget constant in `prepare.py` for the
  first end-to-end test, then raise it.
- `results.tsv` is intentionally git-untracked (matches autoresearch) so the
  keep/discard `git reset` dance doesn't clobber it.
