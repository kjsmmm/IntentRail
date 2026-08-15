#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11,<4"
# dependencies = []
# ///
"""Marketplace bootstrap: managed CLI first, bundled source only as fallback."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="intentrail-bootstrap")
    parser.add_argument("command", nargs="?", choices=["hook"])
    parser.add_argument("--host", required=True, choices=["codex", "claude-code", "copilot-cli"])
    parser.add_argument("--event", required=True)
    args = parser.parse_args(argv)
    payload = sys.stdin.buffer.read()
    current = Path(__file__).resolve()
    for candidate in _cli_candidates():
        if not candidate.is_file() or candidate.resolve() == current:
            continue
        completed = subprocess.run(
            [str(candidate), "hook", "--host", args.host, "--event", args.event],
            input=payload,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode == 0:
            sys.stdout.buffer.write(completed.stdout)
            return 0
    if sys.version_info < (3, 11):
        return _degraded(args.host, args.event, "IntentRail requires uv or a managed IntentRail CLI; run 'intentrail doctor'.")
    engine = _plugin_root() / "skills" / "intentrail" / "scripts"
    if not engine.is_dir():
        return _degraded(args.host, args.event, "IntentRail bundled runtime is missing; reinstall and run 'intentrail doctor'.")
    sys.path.insert(0, str(engine))
    try:
        from intentrail_core.host_adapter import process_host_hook
        document = json.loads(payload.decode("utf-8")) if payload.strip() else {}
        output = process_host_hook(args.host, document, args.event)
    except Exception as exc:
        return _degraded(args.host, args.event, "IntentRail bootstrap failed ({0}); run 'intentrail doctor'.".format(type(exc).__name__))
    sys.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def _cli_candidates():
    seen = set()
    for path in _manifest_files():
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("cli_path")
        except (OSError, json.JSONDecodeError):
            continue
        if value:
            candidate = Path(value).expanduser()
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                yield candidate
    for path in _locator_files():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            candidate = Path(value).expanduser()
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                yield candidate
    found = shutil.which("intentrail")
    if found and found not in seen:
        yield Path(found)


def _locator_files():
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    yield base / "IntentRail" / "cli-path.txt"


def _manifest_files():
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    yield base / "IntentRail" / "install.json"


def _plugin_root():
    for name in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "COPILOT_PLUGIN_ROOT"):
        if os.environ.get(name):
            return Path(os.environ[name]).resolve()
    return Path(__file__).resolve().parents[1]


def _degraded(host, event, message):
    if event.lower().replace("-", "") == "pretooluse":
        if host == "copilot-cli":
            output = {"permissionDecision": "deny", "permissionDecisionReason": message}
        else:
            output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": message}}
    else:
        output = {"systemMessage": message}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
