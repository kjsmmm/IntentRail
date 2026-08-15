#!/usr/bin/env python3
"""Developer compatibility wrapper; formal users run `intentrail install`."""

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from intentrail_core.errors import IntentRailError
from intentrail_core.installer import doctor, install_or_upgrade, parse_hosts, uninstall


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["install", "upgrade"]:
        command = commands.add_parser(name)
        _common(command)
        command.add_argument("--cli-path", required=True, help="Absolute managed IntentRail CLI path")
        command.add_argument("--dry-run", action="store_true")
    uninstall_parser = commands.add_parser("uninstall")
    _common(uninstall_parser)
    uninstall_parser.add_argument("--dry-run", action="store_true")
    doctor_parser = commands.add_parser("doctor")
    _common(doctor_parser)
    doctor_parser.add_argument("--no-hook-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        hosts = parse_hosts(args.hosts, args.root)
        if args.command in {"install", "upgrade"}:
            result = install_or_upgrade(args.command, hosts, args.scope, args.root, args.cli_path, args.dry_run)
        elif args.command == "uninstall":
            result = uninstall(hosts, args.scope, args.root, args.dry_run)
        else:
            result = doctor(hosts, args.scope, args.root, not args.no_hook_test)
        envelope = {"ok": True, "command": args.command, "data": result}
        code = 0
    except (IntentRailError, OSError, ValueError) as exc:
        envelope = {"ok": False, "command": args.command, "error": {"code": getattr(exc, "code", "INSTALLATION_ERROR"), "message": str(exc)}}
        code = 1
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True))
    return code


def _common(parser):
    parser.add_argument("--hosts", "--host", dest="hosts", default="auto")
    parser.add_argument("--scope", choices=["repo", "user"], default="user")
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--json", action="store_true")


if __name__ == "__main__":
    raise SystemExit(main())
