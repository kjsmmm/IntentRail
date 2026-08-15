#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
host=""
event=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --host) host=$2; shift 2 ;;
    --event) event=$2; shift 2 ;;
    *) shift ;;
  esac
done

find_locator() {
  data_root=${XDG_DATA_HOME:-"$HOME/.local/share"}
  if [ -f "$data_root/IntentRail/cli-path.txt" ]; then
    head -n 1 "$data_root/IntentRail/cli-path.txt"
  fi
}

cli=$(find_locator || true)
if [ -n "$cli" ] && [ -x "$cli" ]; then
  exec "$cli" hook --host "$host" --event "$event"
fi
if command -v intentrail >/dev/null 2>&1; then
  exec intentrail hook --host "$host" --event "$event"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --script "$script_dir/intentrail_bootstrap.py" --host "$host" --event "$event"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$script_dir/intentrail_bootstrap.py" --host "$host" --event "$event"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$script_dir/intentrail_bootstrap.py" --host "$host" --event "$event"
fi

message="IntentRail runtime unavailable; install with uv tool or pipx, then run intentrail doctor."
if [ "$event" = "PreToolUse" ]; then
  if [ "$host" = "copilot-cli" ]; then
    printf '{"permissionDecision":"deny","permissionDecisionReason":"%s"}\n' "$message"
  else
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$message"
  fi
else
  printf '{"systemMessage":"%s"}\n' "$message"
fi
