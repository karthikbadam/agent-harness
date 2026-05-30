# agent-loop: long-running autoresearch on agent-harness

Branch: `feat/agent-loop`. Inspired by Karpathy's `autoresearch` repo
(`~/Code/autoresearch`), adapted for the agent-harness execution model.

## Goal

Stress-test agent-harness with a multi-hour autonomous research task and
build the minimum harness features needed to make it work. The target
task: **train a small classifier with policy-gradient RL to pick the best
tool from a candidate set, given a user query**. The agent runs as an
autoresearch loop — propose an experiment, train for a fixed time budget,
evaluate, decide keep/discard, repeat — for as long as the human leaves
the harness running.

Every loop iteration must produce:

1. A row in `results.tsv` (commit, metric, status, description).
2. A regenerated `progress.png` showing metric-vs-experiment-number with
   running best, kept points labeled (mirroring `autoresearch/analysis.ipynb`).
3. Updates to `citations.md` — every paper/technique the agent leaned on
   for this iteration's idea gets a bibtex-style entry.

Both `progress.png` and `citations.md` are surfaced as task artifacts so
the project page shows the curve and bibliography without diving into git.

## What we borrow from Karpathy's autoresearch

- **Single edit surface.** Agent only modifies `train.py`. `prepare.py`
  (data + eval) is read-only. Keeps diffs reviewable, prevents the agent
  from gaming the metric by changing how it's computed.
- **Fixed time budget per experiment.** ~5 minutes wall clock so we can
  expect ~12 experiments/hour. Variants we'd want to compare are then
  comparable regardless of arch/batch size changes.
- **Keep/discard via git.** Improvement → advance branch; regression →
  `git reset` to last kept commit. Branch state IS the experiment log.
- **`program.md` as the "skill".** A markdown file is the agent's standing
  prompt for the loop. The human edits this between runs to shape the
  research org.
- **NEVER STOP.** Once the loop starts, the agent does not ask "should I
  continue?". The human's only signal is interrupting the harness.

## What's different on agent-harness (and why)

| Karpathy autoresearch (raw Claude Code) | agent-harness adaptation |
|---|---|
| Single foreground Claude session running `LOOP FOREVER` | Each iteration = its own Task spawned by an outer `mode='plan'`-style supervisor, so the harness owns lifecycle, retries, and persistence |
| `results.tsv` lives in the repo (untracked) | Same file, but artifacts API surfaces it + `progress.png` on the task page |
| User runs `claude` directly with permissions disabled | Per-project `dangerously_skip=true` inside an isolated worktree, plus `Bash(*)` rules already granted to execute kind |
| H100, 5-min budget gives ~500M tokens | M-series Mac MPS, 5-min budget gives a much smaller distilbert SFT/RL step — that's fine, we're testing the loop, not topping the leaderboard |
| Single GPU, single file, single metric | Same: single MPS device, agent edits `train.py`, metric = held-out tool-pick accuracy |

## Target task: RL tool-picker (concrete)

**Setup** (`prepare.py`, fixed and read-only):

- Dataset: `~/.cache/agent-loop/dataset.jsonl` = ~5k tuples of
  `(query: str, tools: list[ToolSpec], gold_tool_name: str)`. Synthesized
  one-shot by Claude during setup (NOT in the loop). Held-out split
  `eval.jsonl` = 500.
- Eval: `evaluate_accuracy(model_path) -> {accuracy, latency_ms, peak_mem_mb}`.
  Greedy pick from logits over tool names. Fixed forever; agent cannot
  touch.

**Edit surface** (`train.py`, modified each iteration):

- Backbone: `distilbert-base-uncased` (small enough to fine-tune on MPS in
  ~5 min).
- Loss/optimizer/reward shape/batch size/architecture head: all fair game.
- Default baseline: cross-entropy SFT on `(query, gold_tool)` for 1
  epoch. RL variants (REINFORCE with reward = `1.0 if pick == gold else
  -0.1`, PPO with KL-to-baseline, etc.) are improvements the agent can
  propose.

**Time budget**: 300 seconds, enforced inside `train.py` via a wall-clock
check that breaks the training loop. The agent CANNOT change this — it's
in `prepare.py`'s constants. (Same trick autoresearch uses.)

**Metric**: `eval_acc` (higher is better). `results.tsv` columns:

```
commit  eval_acc  peak_mem_mb  status  description
```

## Where the harness currently breaks (and the fixes — Tier 1)

The full gap analysis is in the conversation log. Tier 1, picked by the
human, addresses the three issues that would block *any* long-running
task on this harness:

### F1 — heartbeat-aware idle watchdog

**Problem.** `jobs.py:339-356` SIGTERMs the Claude subprocess after
`idle_timeout_seconds` of no stream-json events (default 600s). When the
agent runs `python train.py` for 5 minutes, the only events we see are
`tool_use(Bash)` at the start and `tool_result` at the end. Nothing in
between. We kill the process.

**Fix.** Track an "in-flight tool" counter in `_run_turn_inner`. Increment
on every `ToolUseEvent`, decrement on every `ToolResultEvent`. In the
watchdog loop, when the counter > 0, refresh `last_event_at` (effectively
suspending the timer). The Bash call can take however long it needs.

When the tool result eventually arrives, the timer resumes naturally
because the result event itself updates `last_event_at`.

This is correct because:
- Multiple parallel tool_use blocks in one assistant message → multiple
  increments, multiple matching decrements as results stream in.
- A truly stuck agent (Bash never returns because of e.g. a hung
  subprocess) is not detected by this — but that's a higher-level concern
  for F8 (per-tool timeout), out of scope here.

### F2 — per-task `idle_timeout_seconds` override

**Problem.** `idle_timeout_seconds` only exists on `Project`. The
autoresearch loop has bootstrap turns that should fail fast (10 min) and
training turns that should not time out (or take 30 min). One project,
two regimes.

**Fix.** Add `Task.idle_timeout_seconds: int | None`. Resolution order in
`jobs.py:307`: task → project → settings default.

### F3 — Artifact store

**Problem.** `Outcome` only records `commit_sha`, `branch`, `summary`,
`status`. There's nowhere to register `progress.png`, `results.tsv`, the
final report, or model checkpoints — and the UI has no surface to render
them.

**Fix.** New `Artifact` table:

```python
class Artifact(Base):
    id: str          # primary key
    task_id: str     # FK
    kind: str        # "graph" | "table" | "report" | "checkpoint" | "log"
    name: str        # display name
    path: str        # filesystem path under AH_HOME/artifacts/<task_id>/
    metadata: JSON   # free-form, e.g. {"iteration": 7, "metric": 0.81}
    created_at: datetime
```

Routes:

- `POST /api/tasks/{id}/artifacts` — register a file (path inside worktree
  or absolute). The handler copies into
  `AH_HOME/artifacts/<task_id>/<name>` so the artifact survives worktree
  cleanup after integration.
- `GET /api/tasks/{id}/artifacts` — list, newest first.
- `GET /api/artifacts/{id}/download` — serve the file.

UI: under task detail, an "Artifacts" section listing each artifact with
inline rendering for `kind=graph` (PNG) and a download link otherwise.

The agent registers artifacts via Bash + curl in its own session (we
don't need an MCP tool for v1 — `curl -X POST ...` is plenty). The
project's `program.md` (Skill) documents the convention.

## The loop, mapped to harness primitives

```
Task: "supervisor"      mode='loop'   ← new mode? OR just a Task whose prompt is the
                                       autoresearch program.md verbatim, with autopilot
                                       on and high idle_timeout. Single long-running
                                       conversation, like Karpathy intended.
   │
   ├─ turn 0: setup, baseline experiment, register progress.png
   ├─ turn 1: propose experiment #2, edit train.py, run, eval, commit/reset
   ├─ turn 2: propose experiment #3, ...
   └─ ...    (until manually stopped or budget hit)
```

We don't strictly need a new `loop` task mode for v1. The simplest path
is one Task with `mode='execute_only'` (or `one_shot`) whose prompt is
the autoresearch program.md verbatim, running with:

- `idle_timeout_seconds = null` (disabled), since training turns can be
  long
- `dangerously_skip = true` on the project (we trust the worktree)
- F1's heartbeat fix is what actually keeps it alive during training

The supervisor stays in one Claude session. It uses the harness's
followup mechanism only if the human needs to redirect it.

The downside vs Karpathy's pure-CLI setup: a single Claude session has
finite context. Over hours of iteration, the agent's context fills with
tool_use/tool_result chatter. We accept this as a known limitation for
v1 — Tier 3 (F8 auto-compaction) addresses it later. In practice
Karpathy's loop hits the same wall; he just restarts Claude.

## Per-iteration artifacts (the deliverable)

After each experiment, the agent runs:

```bash
uv run analysis.py        # reads results.tsv, regenerates progress.png
curl -X POST .../api/tasks/$TASK_ID/artifacts \
  -d '{"kind":"graph","name":"progress.png","path":"./progress.png",
       "metadata":{"iteration":N,"best_acc":X}}'
```

`analysis.py` mirrors `autoresearch/analysis.ipynb`:

- Scatter all experiments at their eval_acc
- Highlight kept ones (green dots), discarded ones (grey)
- Step-line for running best
- Annotate each kept point with its description
- Save to `progress.png`

Citation discipline (a small addition to Karpathy's loop):

- `citations.md` is a curated bibliography for this run
- Whenever the agent draws on a specific technique — REINFORCE, PPO,
  knowledge distillation, label smoothing, etc. — it appends a bibtex
  entry. No phantom citations: only cite work that actually shaped the
  iteration just committed.
- After updating, register `citations.md` as an artifact too (`kind=report`).

## Test-and-iterate sequence

1. **Land F1+F2+F3** behind tests. Three commits, each `add: <thing>` per
   CLAUDE.md.
2. **Set up the tool-picker project** at `~/Code/tool-picker-rl/` with
   `prepare.py`, `train.py`, `program.md`, `analysis.py`, `pyproject.toml`,
   `README.md` citing the source papers.
3. **Register the project** in agent-harness pointed at that dir, with
   `dangerously_skip=true`, `idle_timeout_seconds=null`, `autopilot_mode='on'`.
4. **Submit one task** carrying the program.md content as its prompt.
   Mode: `execute_only`. Watch the first 2–3 iterations.
5. **Observe failures.** Expected categories:
   - Watchdog still firing → F1 has a bug
   - Agent doesn't call the artifacts API → tighten `program.md`
   - Artifact UI doesn't render PNG → fix the React renderer
   - Context fills up after iteration ~10 → defer to Tier 3
6. **Iterate the harness** based on what actually breaks, not what we
   guessed would break.

## Out of scope for this branch

Logged here so we don't forget — these are the Tier 2/3 features the gap
analysis identified, deferred until a real failure motivates them:

- **F4 loop-mode task** with structured replan output. Single-session
  supervisor works for v1; revisit if context blowup forces it.
- **F5 budget caps** (`Project.budget_usd`). Manual `launchctl unload` is
  the kill switch for now.
- **F6 workspace files**. Model checkpoints live in the worktree; if we
  need them across iterations we'll add this.
- **F7 per-kind concurrency**. Sequential is fine for the loop.
- **F8 auto-compaction handoff**. Restart-and-resume is the workaround.

## References (for this plan)

- Karpathy, autoresearch — repo at `github.com/karpathy/autoresearch`,
  README and `program.md` set the loop discipline and the progress-graph
  visual.
- nanochat — parent project autoresearch is carved from.
- `docs/driver-design.md` — existing autopilot/copilot model in this
  harness; F4 would extend it. Out of scope here.
