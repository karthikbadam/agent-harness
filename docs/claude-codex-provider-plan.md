# Claude + Codex Provider Plan

## Goal

Extend agent-harness so each project, ad-hoc job, and task can choose which
agent runs it:

- `claude`: use Claude Code for every phase.
- `codex`: use Codex CLI for every phase.
- `auto`: use Claude for planning phases and Codex for execution phases.

The existing Claude behavior remains the backward-compatible default for
existing installs.

## Current Architecture

The harness currently has a single agent integration:

- `server/agent_harness/claude.py` owns the Claude subprocess runner and
  stream-json parser.
- `server/agent_harness/jobs.py` always instantiates `ClaudeRunner`.
- `Job.kind` identifies the lifecycle phase: `ad_hoc`, `plan`, `execute`, or
  `integrate`.
- `Project.extra_claude_args`, `permission_mode`, and `dangerously_skip`
  configure Claude-specific execution.
- The UI creates projects from `NewProjectComposer`, ad-hoc jobs from
  `JobsPage`, and project plans from `ProjectDetailPage`.

Codex CLI supports the needed non-interactive mode through:

```bash
codex --ask-for-approval never exec --json --sandbox read-only --cd <repo> "<prompt>"
codex --ask-for-approval never exec --json --sandbox workspace-write --cd <repo> "<prompt>"
```

`codex exec --json` emits JSONL events such as `thread.started`,
`turn.started`, `item.completed`, and `turn.completed`. The runner must ignore
non-JSON warning/progress lines defensively.

## Data Model And API

Add agent-provider fields:

- `Project.agent_provider`: configured default, one of `claude`, `codex`,
  `auto`.
- `Job.agent_provider`: resolved provider actually used for that job, one of
  `claude`, `codex`.
- Optionally `Task.agent_provider`: task-level override. If omitted, inherit
  the project setting.

Add the same fields to Pydantic schemas:

- `ProjectCreate`, `ProjectUpdate`, `ProjectOut`
- `JobCreate`, `JobOut`
- `TaskCreate`, `TaskUpdate`, `TaskOut` if task overrides are included

Migration:

- Add idempotent SQLite column migrations in `db.py`.
- Backfill existing projects to `claude`.
- Backfill existing jobs to `claude`.

## Runner Abstraction

Introduce a small protocol shared by both runners:

- `run() -> AsyncIterator[StreamEvent]`
- `stop()`
- `pid`
- `returncode`
- `session_id`
- `stop_requested`

Keep `ClaudeRunner` mostly unchanged and add:

- `server/agent_harness/codex.py`
- `resolve_codex_path()`
- `CodexJsonParser`
- `CodexRunner`

Codex event mapping:

- `thread.started.thread_id` -> runner `session_id`
- completed agent message item -> `AssistantTextEvent`
- command/tool start -> `ToolUseEvent`
- command/tool completion -> `ToolResultEvent`
- `turn.completed` -> `TurnDoneEvent(exit_code=0)`
- `turn.failed` or `error` -> terminal failure

## Provider Resolution

Provider selection lives in `JobManager` so all routes use one rule.

Resolved provider:

- Project/job setting `claude` -> Claude.
- Project/job setting `codex` -> Codex.
- Project/job setting `auto`:
  - `kind == "plan"` -> Claude.
  - `kind in {"execute", "integrate", "ad_hoc"}` -> Codex.

Task lifecycle implication:

- Top-level project planning uses Claude in `auto`.
- Per-task planning uses Claude in `auto`.
- Acked execution jobs use Codex in `auto`.
- Execute-only, research, one-shot, loop iterations, and integration jobs use
  Codex in `auto`.

## Permissions And Arguments

Claude keeps existing behavior:

- `permission_mode`
- `allowed_tools`
- `dangerously_skip`
- `default_claude_args`
- `extra_claude_args`

Codex uses its own configuration:

- `codex_path`
- `default_codex_args`
- optional project `extra_codex_args`

Initial Codex permission mapping:

- Plan jobs: `--sandbox read-only`
- Execute and integrate jobs: `--sandbox workspace-write`
- Non-interactive approval policy: global flag
  `--ask-for-approval never` before `exec`
- Dangerous skip maps to
  `--dangerously-bypass-approvals-and-sandbox`

Codex runner should pass:

```bash
codex --ask-for-approval never exec --json --cd <cwd> --sandbox <mode> <prompt>
```

with command ordering matching the installed CLI.

## Frontend

Add an agent picker using a segmented control with:

- Auto
- Claude
- Codex

Surfaces:

- New project setup: choose project default provider.
- Ad-hoc job composer: choose provider for the job.
- Settings drawer: view/edit the default project provider.
- Job card/detail: display the resolved provider that ran the job.
- Task cards/detail can show provider only if task-level overrides are added.

Generated types:

- Regenerate `web/openapi.json`.
- Regenerate `web/src/types/api.ts`.

## Tests

Backend tests:

- `CodexRunner.build_argv()` flag ordering and sandbox mapping.
- Codex JSONL parser handles:
  - JSON event lines
  - non-JSON warning lines
  - agent final message
  - command/tool events
  - success and failure terminal events
- Project/job/task schema defaults.
- Route create/update round trips for `agent_provider`.
- `JobManager` resolves:
  - `claude` -> Claude
  - `codex` -> Codex
  - `auto + plan` -> Claude
  - `auto + execute` -> Codex

Frontend verification:

- `npm run typecheck`
- `npm run build`

Full backend verification:

```bash
uv run pytest -q
```

## Estimated Scope

Likely handwritten changes:

- Backend: 450-700 added lines.
- Frontend: 120-220 added lines.
- Tests: 200-350 added lines.
- Docs: 30-60 added lines.

Expected total handwritten net new lines: 800-1,300.

Generated OpenAPI and TypeScript updates may add another 50-150 changed lines.

## Rollout Order

1. Create branch `add/codex-agent-provider`.
2. Add schemas, models, and DB migrations.
3. Add runner abstraction and Codex runner/parser.
4. Wire provider resolution in `JobManager`.
5. Update routes.
6. Update UI picker/display surfaces.
7. Regenerate OpenAPI and TypeScript types.
8. Add tests.
9. Run backend and frontend verification.
10. Commit in small chunks following `CLAUDE.md` commit preferences.
