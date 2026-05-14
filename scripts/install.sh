#!/usr/bin/env bash
# One-command install for agent-harness on macOS.
#
# Idempotent: re-running keeps existing token, VAPID keys, and certs.
# All runtime state lives in $AH_HOME (default: ~/.agent-harness).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
AH_HOME="${AH_HOME:-$HOME/.agent-harness}"
VENV="$AH_HOME/venv"
LABEL="com.$(whoami).agent-harness"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
PORT="${AH_PORT:-8765}"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
err()  { printf "\033[31mERROR:\033[0m %s\n" "$*" >&2; }

if [[ "$(uname)" != "Darwin" ]]; then
  err "This installer is macOS-only. (Detected $(uname).)"
  exit 1
fi

bold "==> Preparing $AH_HOME"
mkdir -p "$AH_HOME/logs/jobs"

bold "==> Creating Python venv at $VENV"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1090
. "$VENV/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -e "$REPO"

export AH_HOME

bold "==> Building frontend (web/)"
if [[ ! -d "$REPO/web/node_modules" ]]; then
  (cd "$REPO/web" && npm ci)
fi
(cd "$REPO/web" && npm run build)

bold "==> TLS certs (mkcert)"
if ! command -v mkcert >/dev/null 2>&1; then
  err "mkcert not found. Install it with: brew install mkcert nss"
  err "Then re-run this script."
  exit 1
fi

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi
if [[ -z "$LAN_IP" ]]; then
  err "Could not detect LAN IP via en0/en1. Connect to WiFi and re-run."
  exit 1
fi

if [[ ! -f "$AH_HOME/server.pem" || ! -f "$AH_HOME/server-key.pem" ]]; then
  mkcert -install
  (cd "$AH_HOME" && mkcert \
     -cert-file server.pem \
     -key-file server-key.pem \
     "$LAN_IP" localhost 127.0.0.1)
  echo "  cert: $AH_HOME/server.pem"
else
  echo "  cert: existing $AH_HOME/server.pem (delete to regenerate)"
fi

bold "==> Auth token + VAPID keys"
agent-harness init >/dev/null
agent-harness gen-token >/dev/null
agent-harness gen-vapid >/dev/null
TOKEN="$(python -c "from agent_harness import config; print(config.load_toml()['auth_token'])")"

bold "==> launchd ($LABEL)"
mkdir -p "$PLIST_DIR"
PLIST_TMP="$(mktemp)"
SAFE_PATH="$VENV/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
sed \
  -e "s|__LABEL__|$LABEL|g" \
  -e "s|__VENV_BIN__|$VENV/bin|g" \
  -e "s|__REPO__|$REPO|g" \
  -e "s|__AH_HOME__|$AH_HOME|g" \
  -e "s|__PATH__|$SAFE_PATH|g" \
  "$REPO/scripts/launchd.plist.tmpl" > "$PLIST_TMP"

# Reload cleanly
launchctl unload "$PLIST" 2>/dev/null || true
mv "$PLIST_TMP" "$PLIST"
launchctl load "$PLIST"
echo "  loaded $PLIST"

# Give it a moment to bind.
sleep 1

ROOTCA="$(mkcert -CAROOT)/rootCA.pem"
URL="https://$LAN_IP:$PORT/auth?token=$TOKEN"

cat <<EOF

$(bold "Done.")

Open this URL on your iPhone (on the same WiFi):
  $URL

iPhone setup (one-time):
  1. Email yourself $ROOTCA, open it on the phone, install the profile.
     Settings → General → VPN & Device Management → install.
  2. Settings → General → About → Certificate Trust Settings → enable
     "mkcert development CA".
  3. Open the URL above in Safari.
  4. Share → Add to Home Screen.
  5. Open from the home-screen icon (NOT Safari).
  6. Settings → Notifications → Enable.

Server logs:
  tail -f $AH_HOME/logs/server.err.log

To uninstall:
  launchctl unload $PLIST && rm $PLIST
EOF
