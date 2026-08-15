#!/usr/bin/env python3
"""Thin Skill launcher for an installed or release-bundled IntentRail engine."""

import sys
from pathlib import Path


def _runtime_paths():
    current = Path(__file__).resolve()
    # Host release packages inject the fallback runtime next to this launcher.
    yield current.parent
    # Contributors can run the launcher directly from a source checkout.
    yield current.parents[3] / "src"


for candidate in _runtime_paths():
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from intentrail_core.cli import main
except ModuleNotFoundError as exc:
    if exc.name != "intentrail_core":
        raise
    print(
        "IntentRail CLI is not installed. Run 'uv tool install intentrail' "
        "and then 'intentrail install --hosts auto'.",
        file=sys.stderr,
    )
    raise SystemExit(8)


if __name__ == "__main__":
    raise SystemExit(main())
