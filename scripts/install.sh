#!/usr/bin/env bash
# One-command install for agent-harness on macOS.
#
# The service binds to 127.0.0.1 only (never the LAN). Reach it from other
# devices over your tailnet via Tailscale Serve, which terminates HTTPS and
# proxies to loopback. If `tailscale` is present we set the proxy up for you.
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

# Front the loopback service with Tailscale Serve (tailnet-only HTTPS). We use
# `serve`, never `funnel` — funnel would publish this RCE-capable service to the
# public internet.
bold "==> Tailscale"
TS_NAME=""
if command -v tailscale >/dev/null 2>&1; then
  tailscale serve --bg "$PORT" >/dev/null 2>&1 || \
    err "tailscale serve failed; run 'tailscale up' then re-run, or set it up manually."
  TS_NAME="$("$VENV_BIN/python" - <<'PY' 2>/dev/null || true
import json, subprocess
try:
    out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True).stdout
    print(json.loads(out).get("Self", {}).get("DNSName", "").rstrip("."))
except Exception:
    pass
PY
)"
else
  err "tailscale not found. Install it (https://tailscale.com/download), run 'tailscale up' on this Mac and the iPhone, enable MagicDNS + HTTPS in the admin console, then 'tailscale serve --bg $PORT'."
fi

cat <<EOF

$(bold "Done.")

EOF
if [[ -n "$TS_NAME" ]]; then
  cat <<EOF
Open this on any device on your tailnet (works beyond your WiFi):
  https://$TS_NAME/auth#token=$TOKEN

The token is in the URL fragment (#), so it never reaches the server or its
logs — the page swaps it for an HttpOnly cookie and clears the URL.
EOF
else
  cat <<EOF
Once Tailscale Serve is up, open (from any tailnet device):
  https://<mac>.<tailnet>.ts.net/auth#token=$TOKEN

For local testing on this Mac only:
  http://127.0.0.1:$PORT/auth#token=$TOKEN
EOF
fi
cat <<EOF

Logs:    tail -f $AH_HOME/logs/server.err.log
Stop:    launchctl unload $PLIST
EOF
