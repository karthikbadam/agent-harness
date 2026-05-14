#!/usr/bin/env bash
# Dev mode: uvicorn --reload on :8765 + vite on :5173, parallel. Ctrl+C kills both.
# Open http://localhost:5173 — vite proxies /api to :8765.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
export AH_HOME="${AH_HOME:-$HOME/.agent-harness}"
mkdir -p "$AH_HOME"

command -v uv >/dev/null 2>&1 || { echo "ERROR: install uv first (brew install uv)" >&2; exit 1; }
(cd "$REPO" && uv sync --quiet)
VENV_BIN="$REPO/.venv/bin"

# Bootstrap a token if missing.
if ! "$VENV_BIN/python" -c "from agent_harness import config; import sys; sys.exit(0 if config.load_toml().get('auth_token') else 1)" 2>/dev/null; then
  echo "==> Generating dev token"
  "$VENV_BIN/agent-harness" init
  "$VENV_BIN/agent-harness" gen-token
fi

pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> Backend on :8765"
(cd "$REPO" && "$VENV_BIN/python" -m uvicorn agent_harness.main:app --reload --port 8765 --app-dir server) &
pids+=("$!")

echo "==> Vite on :5173"
(cd "$REPO/web" && npm run dev -- --host) &
pids+=("$!")

wait
