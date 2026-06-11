#!/usr/bin/env bash
# Dev mode: uvicorn --reload on :8765 + vite build --watch into web/dist/.
# The harness serves the built SPA from web/dist at the same port, so the
# whole app is at http://localhost:8765. Ctrl+C kills both.
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

TOKEN="$("$VENV_BIN/python" -c "from agent_harness import config; print(config.load_toml().get('auth_token', ''))")"
echo "==> Token: $TOKEN"

echo "==> Backend on :8765 (also serves web/dist/)"
# Bind 0.0.0.0 so phones on the same LAN can hit http://<mac-ip>:8765.
(cd "$REPO" && "$VENV_BIN/python" -m uvicorn agent_harness.main:app --reload --host 0.0.0.0 --port 8765 --app-dir server) &
pids+=("$!")

echo "==> Vite build --watch → web/dist/"
(cd "$REPO/web" && npm run build -- --watch) &
pids+=("$!")

echo "==> Open http://localhost:8765"
wait
