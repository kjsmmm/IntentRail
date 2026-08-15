#!/usr/bin/env python3
"""Build the complete GitHub Release asset set in a clean staging area."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from build_distributions import build, load_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist"
SOURCE_DATE_EPOCH = "1577836800"
IGNORED_SOURCE_NAMES = {
    ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__",
    "build", "dist", ".coverage", "htmlcov",
}


class ReleaseBuildError(RuntimeError):
    pass


def build_release(output=DEFAULT_OUTPUT):
    config = load_config()
    version = config["product_version"]
    output = Path(output).resolve()
    _require_repo_child(output)
    stage = (ROOT / "build" / ("release-stage-" + version)).resolve()
    _require_repo_child(stage)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        host_output = stage / "hosts"
        host_result = build(host_output, create_archives=True)
        source_copy = stage / "source"
        shutil.copytree(ROOT, source_copy, ignore=_source_ignore)
        python_output = stage / "python-dist"
        python_output.mkdir()
        environment = dict(os.environ)
        environment["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
        completed = subprocess.run(
            [
                sys.executable, "-m", "build", str(source_copy),
                "--wheel", "--sdist", "--no-isolation", "--outdir", str(python_output),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        if completed.returncode != 0:
            raise ReleaseBuildError("Python distribution build failed:\n{0}".format(completed.stdout.strip()))
        wheels = list(python_output.glob("intentrail-{0}-*.whl".format(version)))
        sdists = list(python_output.glob("intentrail-{0}.tar.gz".format(version)))
        if len(wheels) != 1:
            raise ReleaseBuildError("Expected exactly one IntentRail wheel, found {0}.".format(len(wheels)))
        if len(sdists) != 1:
            raise ReleaseBuildError("Expected exactly one IntentRail source distribution, found {0}.".format(len(sdists)))

        publish = stage / "publish"
        publish.mkdir()
        assets = []
        for package in host_result["packages"]:
            archive = Path(package["archive"])
            _validate_host_archive(archive)
            target = publish / archive.name
            shutil.copy2(archive, target)
            assets.append(target)
        wheel = publish / wheels[0].name
        shutil.copy2(wheels[0], wheel)
        _validate_wheel(wheel, version)
        assets.append(wheel)
        sdist = publish / sdists[0].name
        shutil.copy2(sdists[0], sdist)
        _validate_sdist(sdist, version)
        assets.append(sdist)
        release_manifest = publish / "release-manifest.json"
        release_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "product": config["product"],
                    "product_version": version,
                    "schema_compatibility": config["schema_compatibility"],
                    "canonical_skills": config["canonical_skills"],
                    "canonical_content_hash": host_result["canonical_content_hash"],
                    "runtime_source_hash": host_result["runtime_source_hash"],
                    "artifacts": [
                        {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
                        for path in sorted(assets, key=lambda item: item.name)
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        assets.append(release_manifest)
        checksums = publish / "SHA256SUMS.txt"
        checksums.write_text(
            "".join("{0}  {1}\n".format(_sha256(path), path.name) for path in sorted(assets, key=lambda item: item.name)),
            encoding="utf-8",
            newline="\n",
        )

        _replace_output(output, publish, stage)
        return {
            "product_version": version,
            "canonical_content_hash": host_result["canonical_content_hash"],
            "runtime_source_hash": host_result["runtime_source_hash"],
            "output": str(output),
            "assets": [
                {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in sorted(output.iterdir(), key=lambda item: item.name)
                if path.is_file()
            ],
        }
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _source_ignore(directory, names):
    ignored = []
    for name in names:
        if name in IGNORED_SOURCE_NAMES or name.endswith(".egg-info") or name.startswith(".venv"):
            ignored.append(name)
        elif name.endswith((".pyc", ".pyo")) or name in {".DS_Store", "Thumbs.db"}:
            ignored.append(name)
    return ignored


def _validate_host_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    polluted = [
        name for name in names
        if any(part.endswith(".egg-info") or part == "__pycache__" for part in Path(name).parts)
        or name.endswith((".pyc", ".pyo", ".DS_Store", "Thumbs.db"))
    ]
    if polluted:
        raise ReleaseBuildError("Host archive contains build pollution: {0}".format(polluted[0]))


def _validate_wheel(path, version):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ReleaseBuildError("Wheel metadata is missing or ambiguous.")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    required = {
        "intentrail_core/installer.py",
        "intentrail_core/runtime_bundle.json",
        "intentrail-{0}.dist-info/entry_points.txt".format(version),
    }
    if not required.issubset(names):
        raise ReleaseBuildError("Wheel is missing required runtime files.")
    if "Version: {0}".format(version) not in metadata.splitlines():
        raise ReleaseBuildError("Wheel metadata version does not match the release.")


def _validate_sdist(path, version):
    root = "intentrail-{0}/".format(version)
    with tarfile.open(path, "r:gz") as archive:
        names = set(archive.getnames())
    required = {
        root + "pyproject.toml",
        root + "README.md",
        root + "src/intentrail_core/installer.py",
        root + "src/intentrail_core/runtime_bundle.json",
    }
    if not required.issubset(names):
        raise ReleaseBuildError("Source distribution is missing required runtime or metadata files.")


def _replace_output(output, publish, stage):
    backup = stage / "previous-dist"
    if output.exists():
        os.replace(str(output), str(backup))
    try:
        os.replace(str(publish), str(output))
    except Exception:
        if backup.exists() and not output.exists():
            os.replace(str(backup), str(output))
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_repo_child(path):
    path = Path(path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        raise ReleaseBuildError("Release paths must stay inside the IntentRail repository.")
    if path == ROOT.resolve():
        raise ReleaseBuildError("Refusing to use the repository root as a release path.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_release(args.output)
    except (OSError, ValueError, ReleaseBuildError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print("Release build failed: {0}".format(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "data": result}, sort_keys=True))
    else:
        print("Built IntentRail {0} release assets in {1}".format(result["product_version"], result["output"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
