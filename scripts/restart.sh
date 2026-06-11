#!/usr/bin/env bash
# Restart: kill any running harness uvicorn + vite, then start fresh detached.
# Logs go to ~/.agent-harness/logs/server.{out,err}.log. Use this when you
# want the server to pick up code changes without holding a terminal open.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
export AH_HOME="${AH_HOME:-$HOME/.agent-harness}"
mkdir -p "$AH_HOME/logs"

echo "==> Stopping any running harness processes"
pkill -f "uvicorn agent_harness.main:app" 2>/dev/null || true
pkill -f "vite build --watch" 2>/dev/null || true
sleep 1

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: install uv first (brew install uv)" >&2
  exit 1
fi
(cd "$REPO" && uv sync --quiet)
VENV_BIN="$REPO/.venv/bin"

TOKEN="$("$VENV_BIN/python" -c "from agent_harness import config; print(config.load_toml().get('auth_token', ''))")"
echo "==> Token: $TOKEN"

echo "==> Backend on :8765 (detached)"
(cd "$REPO" && nohup "$VENV_BIN/python" -m uvicorn agent_harness.main:app \
  --reload --host 0.0.0.0 --port 8765 --app-dir server \
  >> "$AH_HOME/logs/server.out.log" 2>> "$AH_HOME/logs/server.err.log") &
disown

# Find an npm on PATH or the most recent nvm install. dev.sh assumes the
# user's interactive shell already has npm; this script is invoked detached,
# so we resolve it explicitly.
NPM_BIN="$(command -v npm || true)"
if [ -z "$NPM_BIN" ] && [ -d "$HOME/.nvm/versions/node" ]; then
  NPM_BIN="$(ls -t "$HOME/.nvm/versions/node" | head -1 | xargs -I{} echo "$HOME/.nvm/versions/node/{}/bin/npm")"
fi
if [ -z "$NPM_BIN" ] || [ ! -x "$NPM_BIN" ]; then
  echo "WARN: npm not found — frontend won't rebuild. Backend still up."
else
  echo "==> Vite build --watch (detached) using $NPM_BIN"
  NODE_DIR="$(dirname "$NPM_BIN")"
  (cd "$REPO/web" && PATH="$NODE_DIR:$PATH" nohup "$NPM_BIN" run build -- --watch \
    >> "$AH_HOME/logs/vite.out.log" 2>> "$AH_HOME/logs/vite.err.log") &
  disown
fi

sleep 2
if curl -s -f http://127.0.0.1:8765/api/openapi.json >/dev/null 2>&1; then
  echo "==> http://localhost:8765 is up"
else
  echo "WARN: server not responding yet — check $AH_HOME/logs/server.err.log"
fi
