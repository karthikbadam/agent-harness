#!/usr/bin/env bash
# One-command install for agent-harness on macOS.
#
# Plain HTTP on the LAN; no TLS, no profile install on the iPhone.
# Idempotent: re-running keeps the existing token.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
AH_HOME="${AH_HOME:-$HOME/.agent-harness}"
LABEL="com.$(whoami).agent-harness"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
PORT="${AH_PORT:-8765}"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
err()  { printf "\033[31mERROR:\033[0m %s\n" "$*" >&2; }

[[ "$(uname)" == "Darwin" ]] || { err "macOS-only."; exit 1; }
command -v uv >/dev/null 2>&1 || { err "Install uv first: brew install uv"; exit 1; }

bold "==> Preparing $AH_HOME"
mkdir -p "$AH_HOME/logs/jobs"
export AH_HOME

bold "==> Installing python deps (uv sync)"
(cd "$REPO" && uv sync --quiet)
VENV_BIN="$REPO/.venv/bin"

bold "==> Building frontend"
[[ -d "$REPO/web/node_modules" ]] || (cd "$REPO/web" && npm ci)
(cd "$REPO/web" && npm run build)

bold "==> Auth token"
"$VENV_BIN/agent-harness" init >/dev/null
"$VENV_BIN/agent-harness" gen-token >/dev/null
TOKEN="$("$VENV_BIN/python" -c "from agent_harness import config; print(config.load_toml()['auth_token'])")"

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
[[ -n "$LAN_IP" ]] || { err "No LAN IP via en0/en1. Connect to WiFi and re-run."; exit 1; }

bold "==> launchd ($LABEL)"
mkdir -p "$PLIST_DIR"
SAFE_PATH="$VENV_BIN:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
sed \
  -e "s|__LABEL__|$LABEL|g" \
  -e "s|__VENV_BIN__|$VENV_BIN|g" \
  -e "s|__REPO__|$REPO|g" \
  -e "s|__AH_HOME__|$AH_HOME|g" \
  -e "s|__PATH__|$SAFE_PATH|g" \
  "$REPO/scripts/launchd.plist.tmpl" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 1

cat <<EOF

$(bold "Done.")

Open this on your iPhone (same WiFi):
  http://$LAN_IP:$PORT/auth?token=$TOKEN

Logs:    tail -f $AH_HOME/logs/server.err.log
Stop:    launchctl unload $PLIST
EOF
