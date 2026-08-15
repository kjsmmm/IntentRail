#!/usr/bin/env python3
"""Run the local pre-commit gate mirrored by CI."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "skills.sh.json",
    "src/intentrail_core/cli.py",
    "skills/intentrail/SKILL.md",
    "distribution/canonical.json",
)
IGNORED_SOURCE_PARTS = {
    ".git", "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".intentrail"
}


def run(command):
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    output = []
    for line in process.stdout:
        print(line, end="")
        output.append(line)
    returncode = process.wait()
    if returncode:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            tail = "".join(output[-30:]).strip()
            escaped = tail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::error title=IntentRail pre-commit gate failed::{0}".format(escaped))
        raise SystemExit(returncode)


def audit_source():
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("Missing required publishable files: " + ", ".join(missing))
    if (ROOT / "skills" / "intentrail" / "scripts" / "intentrail_core").exists():
        raise SystemExit("Canonical runtime leaked back into the public Skill source")
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(
            part in IGNORED_SOURCE_PARTS or part.startswith(".venv") or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        if path.suffix.lower() in {".md", ".py", ".json", ".toml", ".yml", ".yaml", ".sh", ".ps1"}:
            text = path.read_text(encoding="utf-8")
            if "G:\\code\\Learning" in text or "C:\\Users\\lenovo" in text:
                raise SystemExit("Local absolute path leaked into source: " + relative.as_posix())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-release", action="store_true")
    parser.add_argument("--release-output", default="build/precommit-release")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        raise SystemExit("IntentRail development checks require Python 3.11 or newer")
    try:
        import setuptools  # noqa: F401 - verifies the no-isolation release backend is available
    except ModuleNotFoundError:
        raise SystemExit('Missing release backend; run: python -m pip install -e ".[test]"')
    audit_source()
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"])
    if not args.skip_release:
        output = (ROOT / args.release_output).resolve()
        try:
            output.relative_to(ROOT)
        except ValueError:
            raise SystemExit("Release audit output must stay inside the repository")
        run([sys.executable, "tools/build_release.py", "--output", str(output), "--json"])
    print("IntentRail pre-commit checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
