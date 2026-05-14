#!/usr/bin/env bash
# Dev mode: runs uvicorn --reload (HTTP, no TLS) on :8765 and vite dev on :5173
# in parallel. Ctrl+C kills both.
#
# Open http://localhost:5173 for the UI; vite proxies /api to :8765.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
AH_HOME="${AH_HOME:-$HOME/.agent-harness}"
export AH_HOME

if [[ ! -d "$AH_HOME" ]]; then
  mkdir -p "$AH_HOME"
fi

# Ensure token exists for dev.
if [[ -z "${AH_AUTH_TOKEN:-}" ]] && ! python3 -c "import sys; sys.path.insert(0, '$REPO/server'); from agent_harness import config; sys.exit(0 if config.load_toml().get('auth_token') else 1)" 2>/dev/null; then
  echo "==> No auth token; generating one for dev"
  python3 -c "import sys; sys.path.insert(0, '$REPO/server'); from agent_harness.cli import main; main(['init'])"
  python3 -c "import sys; sys.path.insert(0, '$REPO/server'); from agent_harness.cli import main; main(['gen-token'])"
fi

pids=()
cleanup() {
  for p in "${pids[@]:-}"; do
    kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting backend on :8765"
(
  cd "$REPO"
  python3 -m uvicorn agent_harness.main:app --reload --port 8765 --app-dir server
) &
pids+=("$!")

echo "==> Starting vite on :5173"
(
  cd "$REPO/web"
  npm run dev -- --host
) &
pids+=("$!")

wait
