#!/usr/bin/env python3
"""Build reproducible host packages from the one canonical Skill Suite."""

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import sys
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "distribution" / "canonical.json"
DEFAULT_OUTPUT = ROOT / "dist"
RUNTIME_SOURCE = ROOT / "src" / "intentrail_core"
RUNTIME_BUNDLE = RUNTIME_SOURCE / "runtime_bundle.json"
BUNDLED_RUNTIME = Path("skills") / "intentrail" / "scripts" / "intentrail_core"
SHARED_BOOTSTRAPS = ("intentrail_bootstrap.py", "intentrail_bootstrap.sh", "intentrail_bootstrap.ps1")
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
IGNORED_TREE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
IGNORED_FILE_NAMES = {".DS_Store", "Thumbs.db"}


class BuildError(RuntimeError):
    pass


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root):
    root = Path(root)
    result = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if _ignored_tree_path(path, root) or path.resolve() == RUNTIME_BUNDLE.resolve():
            continue
        result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def manifest_hash(manifest):
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_skill_manifest(config):
    selected = {}
    for name in config["canonical_skills"]:
        source = ROOT / "skills" / name
        if not (source / "SKILL.md").is_file():
            raise BuildError("Missing canonical Skill: {0}".format(name))
        for relative, digest in tree_manifest(source).items():
            selected["{0}/{1}".format(name, relative)] = digest
    return selected


def runtime_source_manifest():
    if not (RUNTIME_SOURCE / "__init__.py").is_file():
        raise BuildError("IntentRail runtime source is missing")
    return tree_manifest(RUNTIME_SOURCE)


def build(output=DEFAULT_OUTPUT, create_archives=True):
    config = load_config()
    write_runtime_bundle(config)
    _validate_source_versions(config)
    output = Path(output).resolve()
    _require_safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    canonical = canonical_skill_manifest(config)
    canonical_hash = manifest_hash(canonical)
    runtime = runtime_source_manifest()
    runtime_hash = manifest_hash(runtime)
    built = []
    for host, host_config in config["hosts"].items():
        package_name = "intentrail-{0}-{1}".format(host, config["product_version"])
        target = output / package_name
        if target.exists():
            shutil.rmtree(str(target))
        adapter = ROOT / host_config["adapter"]
        if adapter.exists():
            shutil.copytree(str(adapter), str(target))
        else:
            target.mkdir(parents=True)
        skills_target = target / "skills"
        skills_target.mkdir(exist_ok=True)
        for name in config["canonical_skills"]:
            shutil.copytree(
                str(ROOT / "skills" / name),
                str(skills_target / name),
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                    "*.pyc", "*.pyo", "*.egg-info", ".DS_Store", "Thumbs.db", RUNTIME_BUNDLE.name,
                ),
            )
        bundled_runtime = target / BUNDLED_RUNTIME
        shutil.copytree(
            str(RUNTIME_SOURCE),
            str(bundled_runtime),
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                "*.pyc", "*.pyo", "*.egg-info", ".DS_Store", "Thumbs.db", RUNTIME_BUNDLE.name,
            ),
        )
        if host != "generic-agent-skills":
            scripts = target / "scripts"
            scripts.mkdir(exist_ok=True)
            for name in SHARED_BOOTSTRAPS:
                shutil.copy2(str(ROOT / "adapters" / "shared" / name), str(scripts / name))
            _render_marketplace_hooks(target, host)
        build_info = {
            "product": config["product"],
            "product_version": config["product_version"],
            "schema_compatibility": config["schema_compatibility"],
            "host": host,
            "support_target": host_config["target"],
            "runtime": {
                "managed_hook": "absolute-intentrail-cli",
                "marketplace_bootstrap": host != "generic-agent-skills",
                "bundled_fallback": True,
                "resolution": config.get("runtime_resolution", []),
            },
            "canonical_content_hash": canonical_hash,
            "canonical_files": canonical,
            "runtime_source_hash": runtime_hash,
            "generated": True,
        }
        (target / "intentrail-build.json").write_text(
            json.dumps(build_info, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_package(target, host, config, canonical_hash, runtime_hash)
        archive = None
        if create_archives:
            archive = output / (package_name + ".zip")
            if archive.exists():
                archive.unlink()
            _write_reproducible_zip(target, archive)
        built.append({"host": host, "directory": str(target), "archive": str(archive) if archive else None})
    return {
        "product_version": config["product_version"],
        "canonical_content_hash": canonical_hash,
        "runtime_source_hash": runtime_hash,
        "packages": built,
    }


def validate_package(package_root, host, config=None, expected_hash=None, expected_runtime_hash=None):
    config = config or load_config()
    package_root = Path(package_root)
    host_config = config["hosts"][host]
    manifest_path = package_root / host_config["manifest"]
    if not manifest_path.is_file():
        raise BuildError("{0}: missing manifest {1}".format(host, host_config["manifest"]))
    polluted = [
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and _ignored_tree_path(path, package_root)
    ]
    if polluted:
        raise BuildError("{0}: package contains ignored build artifacts: {1}".format(host, ", ".join(polluted[:5])))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if host != "generic-agent-skills":
        if manifest.get("name") != config["plugin_name"]:
            raise BuildError("{0}: plugin name mismatch".format(host))
        if manifest.get("version") != config["product_version"]:
            raise BuildError("{0}: plugin version mismatch".format(host))
        if not (package_root / "scripts" / "intentrail_bootstrap.py").is_file():
            raise BuildError("{0}: Marketplace bootstrap is missing".format(host))
        hook_documents = [path for path in package_root.rglob("*.json") if "hook" in path.name.lower() or "hooks" in path.parts]
        for path in hook_documents:
            text = path.read_text(encoding="utf-8")
            if "{{INTENTRAIL_COMMAND" in text:
                raise BuildError("{0}: unresolved Hook command marker".format(host))
            if any(token in text for token in ("python3 ", "python ", "py -3 ")):
                raise BuildError("{0}: formal Hook invokes a system Python interpreter".format(host))
    actual = {}
    for name in config["canonical_skills"]:
        skill_root = package_root / "skills" / name
        if not (skill_root / "SKILL.md").is_file():
            raise BuildError("{0}: missing Skill {1}".format(host, name))
        for relative, digest in tree_manifest(skill_root).items():
            if name == "intentrail" and relative.startswith("scripts/intentrail_core/"):
                continue
            actual["{0}/{1}".format(name, relative)] = digest
    actual_hash = manifest_hash(actual)
    expected_hash = expected_hash or manifest_hash(canonical_skill_manifest(config))
    if actual_hash != expected_hash:
        raise BuildError("{0}: bundled Skills drifted from canonical source".format(host))
    build_info = json.loads((package_root / "intentrail-build.json").read_text(encoding="utf-8"))
    if build_info.get("canonical_content_hash") != expected_hash:
        raise BuildError("{0}: build metadata hash mismatch".format(host))
    bundled_runtime = package_root / BUNDLED_RUNTIME
    actual_runtime_hash = manifest_hash(tree_manifest(bundled_runtime))
    expected_runtime_hash = expected_runtime_hash or manifest_hash(runtime_source_manifest())
    if actual_runtime_hash != expected_runtime_hash:
        raise BuildError("{0}: bundled runtime drifted from canonical source".format(host))
    if build_info.get("runtime_source_hash") != expected_runtime_hash:
        raise BuildError("{0}: runtime build metadata hash mismatch".format(host))
    return {
        "valid": True,
        "host": host,
        "canonical_content_hash": actual_hash,
        "runtime_source_hash": actual_runtime_hash,
    }


def _validate_source_versions(config):
    sys.path.insert(0, str(ROOT / "src"))
    from intentrail_core.constants import PRODUCT_VERSION

    if config["product_version"] != PRODUCT_VERSION:
        raise BuildError("canonical.json and engine PRODUCT_VERSION differ")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project.get("project", {}).get("version") != PRODUCT_VERSION:
        raise BuildError("pyproject.toml and engine PRODUCT_VERSION differ")
    for host, host_config in config["hosts"].items():
        if host == "generic-agent-skills":
            continue
        manifest = json.loads((ROOT / host_config["adapter"] / host_config["manifest"]).read_text(encoding="utf-8"))
        if manifest.get("version") != PRODUCT_VERSION:
            raise BuildError("{0} adapter manifest version differs from engine".format(host))


def write_runtime_bundle(config=None):
    """Embed raw canonical packages for the PyPI CLI installer."""
    config = config or load_config()
    hosts = {}
    for host, host_config in config["hosts"].items():
        files = {}
        adapter = ROOT / host_config["adapter"]
        if adapter.exists():
            for path in sorted(item for item in adapter.rglob("*") if item.is_file()):
                files[path.relative_to(adapter).as_posix()] = _encoded(path.read_bytes())
        for name in config["canonical_skills"]:
            source = ROOT / "skills" / name
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                if _ignored_tree_path(path, source) or path.resolve() == RUNTIME_BUNDLE.resolve():
                    continue
                relative = Path("skills") / name / path.relative_to(source)
                files[relative.as_posix()] = _encoded(path.read_bytes())
        for path in sorted(item for item in RUNTIME_SOURCE.rglob("*") if item.is_file()):
            if _ignored_tree_path(path, RUNTIME_SOURCE) or path.resolve() == RUNTIME_BUNDLE.resolve():
                continue
            relative = BUNDLED_RUNTIME / path.relative_to(RUNTIME_SOURCE)
            files[relative.as_posix()] = _encoded(path.read_bytes())
        if host != "generic-agent-skills":
            for name in SHARED_BOOTSTRAPS:
                path = ROOT / "adapters" / "shared" / name
                files[(Path("scripts") / name).as_posix()] = _encoded(path.read_bytes())
        hosts[host] = {"files": files}
    document = {
        "product_version": config["product_version"],
        "schema_compatibility": config["schema_compatibility"],
        "canonical_skills": config["canonical_skills"],
        "canonical_content_hash": manifest_hash(canonical_skill_manifest(config)),
        "runtime_source_hash": manifest_hash(runtime_source_manifest()),
        "hosts": hosts,
    }
    temporary = RUNTIME_BUNDLE.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(RUNTIME_BUNDLE))
    return RUNTIME_BUNDLE


def _encoded(value):
    return base64.b64encode(value).decode("ascii")


def _ignored_tree_path(path, root):
    relative = Path(path).relative_to(root)
    if any(part in IGNORED_TREE_NAMES or part.endswith(".egg-info") for part in relative.parts):
        return True
    return relative.name in IGNORED_FILE_NAMES or relative.suffix in {".pyc", ".pyo"}


def _render_marketplace_hooks(package_root, host):
    variable = "${CLAUDE_PLUGIN_ROOT}" if host == "claude-code" else "${PLUGIN_ROOT}"
    replacements = {
        "{{INTENTRAIL_COMMAND_UNIX}}": 'sh "{0}/scripts/intentrail_bootstrap.sh"'.format(variable),
        "{{INTENTRAIL_COMMAND_WINDOWS}}": 'powershell -NoProfile -ExecutionPolicy Bypass -File "{0}/scripts/intentrail_bootstrap.ps1"'.format(variable),
        "{{INTENTRAIL_COMMAND}}": 'sh "{0}/scripts/intentrail_bootstrap.sh"'.format(variable),
    }
    for path in Path(package_root).rglob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        document = _replace_strings(document, replacements)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _replace_strings(value, replacements):
    if isinstance(value, str):
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        return value
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def _require_safe_output(output):
    root = ROOT.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        raise BuildError("Build output must stay inside the IntentRail repository.")
    if output == root:
        raise BuildError("Refusing to use the repository root as build output.")


def _write_reproducible_zip(source, archive):
    source = Path(source)
    with zipfile.ZipFile(str(archive), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = Path(source.name) / path.relative_to(source)
            info = zipfile.ZipInfo(relative.as_posix(), FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.suffix in {".py", ".sh"} else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            bundle.writestr(info, path.read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--no-archives", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build(args.output, create_archives=not args.no_archives)
    except (BuildError, OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print("Build failed: {0}".format(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "data": result}, sort_keys=True))
    else:
        print("Built {0} IntentRail packages with canonical hash {1}".format(len(result["packages"]), result["canonical_content_hash"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
