# Remote access

The harness binds to `127.0.0.1` by default. There are two ways to reach it
from your phone. Both end at the same `/auth#token=<token>` URL — the token
rides in the URL fragment (`#`), so it never hits the server or its logs; the
`/auth` page swaps it for a session cookie and clears the URL.

Your token is in `~/.agent-harness/config.toml` (`auth_token`), and is printed
by `./scripts/install.sh`.

## LAN (same Wi-Fi, works immediately)

Set the bind to all interfaces and restart:

```bash
# ~/.agent-harness/config.toml
host = "0.0.0.0"
```

```bash
launchctl kickstart -k gui/$(id -u)/com.$(whoami).agent-harness
```

Then on the phone (replace with your Mac's LAN IP — `ipconfig getifaddr en0`):

```
http://<mac-lan-ip>:8765/auth#token=<token>
```

Plain http, so only do this on a trusted network — the bearer token is the only
guard. The session cookie is set without the `Secure` flag over http, so the
`/auth#token` flow still works. Never port-forward this to the public internet.

## Tailscale (works anywhere, incl. cellular)

Reach it over your tailnet — no LAN exposure, encrypted end to end by WireGuard.

1. **CLI location.** The Mac App Store build ships its CLI inside the app
   bundle, not on `PATH`. Alias it:

   ```bash
   alias tailscale="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
   ```

   (The standalone build from tailscale.com / Homebrew puts `tailscale` on
   `PATH` directly.)

2. **Enrol both devices** on the same tailnet (`tailscale up` on the Mac via the
   menu-bar app; install Tailscale on the phone and log in). Enable **MagicDNS**
   in the admin console so the Mac gets a `*.ts.net` name
   (`tailscale status --json | … .Self.DNSName`).

3. **Enable Serve on the tailnet.** Serve is gated per-tailnet. The first
   `tailscale serve` prints a one-click enable link:

   ```
   Serve is not enabled on your tailnet.
   To enable, visit: https://login.tailscale.com/f/serve?node=<id>
   ```

   Open it once. (This is a tailnet feature toggle, not the App Store sandbox
   and not the HTTPS-certs setting.)

4. **Front the loopback service.** Two options:

   ```bash
   # Plain http over the (already-encrypted) tailnet — no certificate needed:
   tailscale serve --bg --http=80 localhost:8765
   #   → http://<mac>.<tailnet>.ts.net/

   # Or HTTPS, if you also enable "HTTPS Certificates" in the admin console:
   tailscale serve --bg 8765
   #   → https://<mac>.<tailnet>.ts.net/
   ```

   Use `serve`, **never `funnel`** — funnel would publish this RCE-capable
   service to the public internet.

   Check / undo:

   ```bash
   tailscale serve status
   tailscale serve --http=80 off      # or: tailscale serve reset
   ```

5. **Open on the phone** (any network):

   ```
   http://<mac>.<tailnet>.ts.net/auth#token=<token>     # or https:// if you used certs
   ```

The `--http=80` form serves plaintext, but only inside the WireGuard tunnel, so
it stays private; the session cookie is set without `Secure` and works.

## Troubleshooting

- **`serve` hangs or "No serve config"** → Serve isn't enabled on the tailnet
  (step 3). Run `tailscale serve --http=80 localhost:8765` once and follow the
  printed link.
- **Phone can't reach the `.ts.net` name** → MagicDNS not enabled, or the phone
  isn't connected to the tailnet.
- **401 after `/auth`** → the token in the URL fragment is wrong/stale; re-copy
  it from `config.toml` or the installer output.
