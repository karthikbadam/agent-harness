#!/usr/bin/env bash
# Fake `claude` shim for tests.
#
# Behavior controlled by env vars:
#   FAKE_CLAUDE_FIXTURE    — path to a jsonl file to stream to stdout
#   FAKE_CLAUDE_DELAY_MS   — sleep N ms between lines (default 0)
#   FAKE_CLAUDE_HANG       — if set, sleep forever after streaming (for stop tests)
#   FAKE_CLAUDE_EXIT       — exit code (default 0)
set -eu

# Drain argv silently; we don't validate the spec's flags here.
: "${FAKE_CLAUDE_FIXTURE:?FAKE_CLAUDE_FIXTURE is required}"

delay_ms="${FAKE_CLAUDE_DELAY_MS:-0}"

while IFS= read -r line; do
  # Directive lines start with '#' and are NOT printed. `# sleep N` sleeps
  # N seconds before continuing — useful for simulating a long-running
  # tool call between tool_use and tool_result.
  case "$line" in
    "# sleep "*)
      n="${line#"# sleep "}"
      sleep "$n"
      continue
      ;;
    "#"*)
      continue
      ;;
  esac
  printf '%s\n' "$line"
  if [ "$delay_ms" != "0" ]; then
    # busy sleep via python for ms precision (more portable than `sleep 0.001`)
    python3 -c "import time; time.sleep(${delay_ms}/1000.0)" 2>/dev/null || sleep "0.$(printf '%03d' "$delay_ms")"
  fi
done < "$FAKE_CLAUDE_FIXTURE"

if [ -n "${FAKE_CLAUDE_HANG:-}" ]; then
  # Wait for SIGTERM
  while :; do sleep 1; done
fi

exit "${FAKE_CLAUDE_EXIT:-0}"
