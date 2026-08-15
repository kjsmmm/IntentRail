"""Managed CLI installation, host Hook rendering, and runtime diagnostics."""

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import uuid
from base64 import b64decode
from pathlib import Path

from .constants import PRODUCT_VERSION
from .errors import InstallationError, UsageError


HOSTS = ("codex", "claude-code", "copilot-cli", "generic-agent-skills")
HOOK_HOSTS = HOSTS[:3]
HOST_EXECUTABLES = {"codex": "codex", "claude-code": "claude", "copilot-cli": "copilot"}
OWNER = "intentrail-owned"
INSTALL_MANIFEST_SCHEMA_VERSION = "1.0.0"
BUNDLE_NAME = "runtime_bundle.json"
HOOK_MARKERS = ("{{INTENTRAIL_COMMAND}}", "{{INTENTRAIL_COMMAND_UNIX}}", "{{INTENTRAIL_COMMAND_WINDOWS}}")


def user_home():
    return Path.home().resolve()


def install_manifest_path(scope, root):
    if scope == "repo":
        return Path(root).resolve() / ".intentrail-install.json"
    return _user_data_root() / "install.json"


def cli_locator_path(scope, root):
    if scope == "repo":
        return Path(root).resolve() / ".intentrail-cli"
    return _user_data_root() / "cli-path.txt"


def detect_hosts(root=None, path=None):
    """Detect usable hosts without treating a generic project folder as a host."""
    root = Path(root or os.getcwd()).resolve()
    detected = []
    hints = {
        "codex": [root / ".codex", user_home() / ".codex"],
        "claude-code": [root / ".claude", user_home() / ".claude"],
        "copilot-cli": [user_home() / ".copilot"],
    }
    for host, executable in HOST_EXECUTABLES.items():
        if shutil.which(executable, path=path) or any(item.exists() for item in hints[host]):
            detected.append(host)
    return detected or ["generic-agent-skills"]


def parse_hosts(value, root=None, path=None):
    if isinstance(value, (list, tuple)):
        hosts = list(value)
    elif value == "auto":
        hosts = detect_hosts(root, path)
    elif value == "all":
        hosts = list(HOSTS)
    else:
        hosts = [item.strip() for item in str(value).split(",") if item.strip()]
    unknown = set(hosts) - set(HOSTS)
    if unknown:
        raise UsageError("Unknown hosts: {0}".format(", ".join(sorted(unknown))))
    if not hosts:
        raise UsageError("At least one host is required.")
    return list(dict.fromkeys(hosts))


def resolve_cli_path(explicit=None):
    """Resolve the stable tool shim installed by uv/pipx, never a Python interpreter."""
    if explicit is not None:
        candidates = [explicit]
    else:
        candidates = [os.environ.get("INTENTRAIL_CLI"), shutil.which("intentrail")]
        invoked = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
        if invoked and invoked.name.lower() in {"intentrail", "intentrail.exe"}:
            candidates.append(str(invoked))
        scripts = Path(sysconfig.get_path("scripts"))
        candidates.extend(str(scripts / name) for name in ("intentrail.exe", "intentrail"))
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if _is_stable_cli_executable(path):
            return path
    raise InstallationError(
        "A stable IntentRail CLI executable could not be resolved.",
        recovery_actions=["Install with 'uv tool install intentrail' or 'pipx install intentrail', then run 'intentrail install --hosts auto'."],
    )


def _is_stable_cli_executable(path):
    path = Path(path)
    if not path.is_file() or path.name.lower().startswith(("python", "py.exe")):
        return False
    if os.name == "nt" and path.suffix.lower() != ".exe":
        return False
    return True


def install_or_upgrade(action, hosts, scope, root, cli_path=None, dry_run=False):
    if action not in {"install", "upgrade"}:
        raise UsageError("Unsupported installation action: {0}".format(action))
    root = Path(root).resolve()
    cli = resolve_cli_path(cli_path)
    manifest_path = install_manifest_path(scope, root)
    previous = _read_json(manifest_path, default={})
    if previous:
        _validate_install_manifest(previous, scope, root)
    if action == "upgrade" and not previous:
        raise InstallationError("No prior IntentRail installation manifest exists; run install first.")
    hosts = list(previous.get("hosts") or []) if action == "upgrade" and hosts == "auto" else parse_hosts(hosts, root)

    with tempfile.TemporaryDirectory(prefix="intentrail-packages-") as temporary:
        package_roots = {}
        for host in hosts:
            package = Path(temporary) / host
            materialize_package(host, package)
            if host in HOOK_HOSTS:
                render_managed_hooks(package, host, cli)
            package_roots[host] = package
        plan = make_plan(action, hosts, scope, root, package_roots, cli, previous)
        conflicts = [item for item in plan["operations"] if item["state"] == "conflict"]
        if conflicts:
            raise InstallationError("Unowned target conflict: {0}".format(conflicts[0]["target"]))
        if dry_run:
            for item in plan["operations"]:
                item.pop("source", None)
            return plan
        return _apply_plan(plan, previous)


def make_plan(action, hosts, scope, root, package_roots, cli_path, previous=None):
    operations = []
    trust = []
    previous = previous or {}
    for host in hosts:
        plan = target_plan(host, scope, root, package_roots[host])
        for source, target in plan["copies"]:
            state = _copy_state(source, target, previous)
            operations.append({"kind": "copy", "host": host, "source": str(source), "target": str(target), "state": state})
        for kind, target, value in plan["configs"]:
            state = _config_state(kind, target, value)
            operations.append({"kind": kind, "host": host, "target": str(target), "value": value, "state": state})
        if host in {"codex", "claude-code"}:
            trust.append({"host": host, "required": True, "instruction": _trust_instruction(host)})
    return {
        "action": action,
        "scope": scope,
        "root": str(Path(root).resolve()),
        "product_version": PRODUCT_VERSION,
        "cli_path": str(Path(cli_path).resolve()),
        "runtime_backend": "managed-cli",
        "manifest": str(install_manifest_path(scope, root)),
        "locator": str(cli_locator_path(scope, root)),
        "hosts": list(hosts),
        "operations": operations,
        "trust": trust,
        "preserve": [str(Path(root).resolve() / ".intentrail"), "all non-IntentRail host configuration"],
    }


def target_plan(host, scope, root, package_root):
    root = Path(root).resolve()
    home = user_home()
    if host == "codex":
        plugin = (root / "plugins" / "intentrail") if scope == "repo" else (home / "plugins" / "intentrail")
        marketplace = (root / ".agents" / "plugins" / "marketplace.json") if scope == "repo" else (home / ".agents" / "plugins" / "marketplace.json")
        source = "./plugins/intentrail"
        return {"copies": [(package_root, plugin)], "configs": [("codex-marketplace", marketplace, source)]}
    if host == "claude-code":
        plugin = (root / ".claude" / "skills" / "intentrail") if scope == "repo" else (home / ".claude" / "skills" / "intentrail")
        return {"copies": [(package_root, plugin)], "configs": []}
    if host == "copilot-cli":
        base = (root / ".github") if scope == "repo" else (home / ".copilot")
        plugin = base / "intentrail-plugin"
        copies = [(package_root, plugin)]
        for name in _bundle()["canonical_skills"]:
            copies.append((package_root / "skills" / name, base / "skills" / name))
        return {"copies": copies, "configs": [("copilot-hooks", base / "hooks" / "intentrail.json", str(plugin))]}
    base = (root / ".agents" / "skills") if scope == "repo" else (home / ".agents" / "skills")
    return {"copies": [(package_root / "skills" / name, base / name) for name in _bundle()["canonical_skills"]], "configs": []}


def _validate_install_manifest(manifest, scope, root):
    """Reject forged or stale manifests before reading, executing, or deleting recorded paths."""
    if not isinstance(manifest, dict) or manifest.get("owner") != OWNER:
        raise InstallationError("IntentRail installation manifest ownership is invalid.")
    if manifest.get("schema_version") != INSTALL_MANIFEST_SCHEMA_VERSION:
        raise InstallationError("IntentRail installation manifest schema is unsupported.")
    if manifest.get("scope") != scope:
        raise InstallationError("IntentRail installation manifest scope does not match this command.")
    root = Path(root).resolve()
    if scope == "repo" and _path_key(manifest.get("root", "")) != _path_key(root):
        raise InstallationError("IntentRail installation manifest root does not match this repository.")
    declared_hosts = manifest.get("hosts")
    if not isinstance(declared_hosts, list) or not declared_hosts or set(declared_hosts) - set(HOSTS):
        raise InstallationError("IntentRail installation manifest contains invalid hosts.")

    expected_paths = {}
    expected_configs = {}
    validation_source = Path(tempfile.gettempdir()) / "intentrail-manifest-validation-source"
    for host in declared_hosts:
        plan = target_plan(host, scope, root, validation_source)
        expected_paths[host] = {_path_key(target) for _, target in plan["copies"]}
        expected_configs[host] = {
            (kind, _path_key(target)): value
            for kind, target, value in plan["configs"]
        }

    seen_paths = {host: set() for host in declared_hosts}
    for item in manifest.get("installed", []):
        if not isinstance(item, dict) or item.get("host") not in expected_paths or not item.get("path"):
            raise InstallationError("IntentRail installation manifest contains an invalid installed record.")
        key = _path_key(item["path"])
        if key not in expected_paths[item["host"]] or key in seen_paths[item["host"]]:
            raise InstallationError("IntentRail installation manifest contains an unexpected installed path.")
        seen_paths[item["host"]].add(key)
    if any(seen_paths[host] != expected_paths[host] for host in declared_hosts):
        raise InstallationError("IntentRail installation manifest is missing expected installed paths.")

    seen_configs = {host: set() for host in declared_hosts}
    for item in manifest.get("configs", []):
        if not isinstance(item, dict) or item.get("host") not in expected_configs or not item.get("path"):
            raise InstallationError("IntentRail installation manifest contains an invalid configuration record.")
        key = (item.get("kind"), _path_key(item["path"]))
        expected_value = expected_configs[item["host"]].get(key)
        if expected_value is None or key in seen_configs[item["host"]]:
            raise InstallationError("IntentRail installation manifest contains an unexpected configuration path.")
        actual_value = item.get("value")
        values_match = (
            actual_value == expected_value
            if item.get("kind") == "codex-marketplace"
            else _path_key(actual_value or "") == _path_key(expected_value)
        )
        if not values_match:
            raise InstallationError("IntentRail installation manifest contains an unexpected configuration value.")
        seen_configs[item["host"]].add(key)
    if any(seen_configs[host] != set(expected_configs[host]) for host in declared_hosts):
        raise InstallationError("IntentRail installation manifest is missing expected configuration records.")


def _path_key(value):
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def materialize_package(host, target):
    bundle = _bundle()
    if host not in bundle["hosts"]:
        raise InstallationError("Runtime bundle does not contain host: {0}".format(host))
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    for relative, encoded in bundle["hosts"][host]["files"].items():
        destination = target / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b64decode(encoded.encode("ascii")))
    return target


def render_managed_hooks(package_root, host, cli_path):
    command = _quote_command(cli_path)
    replacements = {marker: command for marker in HOOK_MARKERS}
    _render_json_templates(package_root, replacements)
    unresolved = _find_markers(package_root)
    if unresolved:
        raise InstallationError("Managed Hook rendering left unresolved command markers.", details={"files": unresolved})


def doctor(hosts, scope, root, run_hooks=True, cli_path=None):
    root = Path(root).resolve()
    manifest_path = install_manifest_path(scope, root)
    manifest = _read_json(manifest_path, default={})
    if manifest:
        _validate_install_manifest(manifest, scope, root)
    hosts = list(manifest.get("hosts") or []) if hosts == "auto" and manifest.get("hosts") else parse_hosts(hosts, root)
    try:
        cli = resolve_cli_path(cli_path)
    except InstallationError:
        cli = None
    configured_cli_value = manifest.get("cli_path")
    configured_cli = Path(configured_cli_value).expanduser().resolve() if configured_cli_value else None
    cli_matches_manifest = not configured_cli or (cli and _path_key(cli) == _path_key(configured_cli))
    cli_exists = bool(cli and cli.is_file())
    version_probe = _probe_version(cli) if cli_exists else {"ok": False, "reason": "cli-missing"}
    reports = []
    for host in hosts:
        records = [item for item in manifest.get("installed", []) if item.get("host") == host]
        installed = bool(records) and all(Path(item["path"]).exists() for item in records)
        drifted = [item["path"] for item in records if Path(item["path"]).exists() and _path_hash(Path(item["path"])) != item.get("hash")]
        hook_rendered = host not in HOOK_HOSTS or (cli_matches_manifest and _installed_hooks_reference_cli(records, cli))
        hook_probe = {"ok": True, "reason": "not-applicable"}
        if host in HOOK_HOSTS and run_hooks and cli_exists and installed and hook_rendered:
            hook_probe = _probe_hook(cli, host)
        host_executable = shutil.which(HOST_EXECUTABLES.get(host, "")) if host in HOST_EXECUTABLES else None
        healthy = installed and not drifted and cli_exists and version_probe.get("ok") and hook_rendered and hook_probe.get("ok")
        if host == "generic-agent-skills":
            support = "Standard" if healthy else "Unsupported"
        elif healthy and not host_executable:
            support = "Standard"
        else:
            support = "Full-candidate" if healthy else "Unsupported"
        reports.append({
            "host": host,
            "support": support,
            "installed": installed,
            "host_executable": host_executable,
            "cli_path": str(cli) if cli else None,
            "configured_cli_path": str(configured_cli) if configured_cli else None,
            "cli_exists": cli_exists,
            "version_probe": version_probe,
            "hook_uses_absolute_cli": hook_rendered,
            "hook_probe": hook_probe,
            "drifted": drifted,
            "hook_trust": "not-machine-verifiable" if host in {"codex", "claude-code"} else "not-applicable",
            "next_action": _trust_instruction(host) if support == "Full-candidate" and host in {"codex", "claude-code"} else ("Install or expose the host executable, then rerun intentrail doctor." if support == "Standard" and host in HOOK_HOSTS else None),
        })
    return {
        "manifest": str(manifest_path),
        "runtime_backend": manifest.get("runtime_backend"),
        "cli_path": str(cli) if cli else None,
        "configured_cli_path": str(configured_cli) if configured_cli else None,
        "locator": str(cli_locator_path(scope, root)),
        "hosts": reports,
    }


def uninstall(hosts, scope, root, dry_run=False):
    manifest_path = install_manifest_path(scope, root)
    manifest = _read_json(manifest_path, default=None)
    if not manifest or manifest.get("owner") != OWNER:
        raise InstallationError("No owned IntentRail installation manifest was found.")
    _validate_install_manifest(manifest, scope, root)
    hosts = list(manifest.get("hosts") or []) if hosts == "auto" else parse_hosts(hosts, root)
    selected = set(hosts)
    removals = [item for item in manifest.get("installed", []) if item.get("host") in selected]
    configs = [item for item in manifest.get("configs", []) if item.get("host") in selected]
    preview = {
        "action": "uninstall",
        "remove": [item["path"] for item in removals] + [item["path"] for item in configs],
        "preserve": [str(Path(root).resolve() / ".intentrail"), "all unowned files and configuration entries"],
    }
    if dry_run:
        return preview
    for item in configs:
        path = Path(item["path"])
        if item["kind"] == "codex-marketplace":
            _remove_codex_marketplace_entry(path, item["value"])
        elif item["kind"] == "copilot-hooks" and path.exists():
            path.unlink()
    for item in sorted(removals, key=lambda value: len(value["path"]), reverse=True):
        path = Path(item["path"])
        if path.exists() and _owned_marker(path).is_file():
            shutil.rmtree(str(path)) if path.is_dir() else path.unlink()
    remaining_installed = [item for item in manifest.get("installed", []) if item.get("host") not in selected]
    remaining_configs = [item for item in manifest.get("configs", []) if item.get("host") not in selected]
    if remaining_installed or remaining_configs:
        manifest["installed"] = remaining_installed
        manifest["configs"] = remaining_configs
        manifest["hosts"] = sorted({item["host"] for item in remaining_installed})
        _atomic_write_json(manifest_path, manifest)
    else:
        manifest_path.unlink(missing_ok=True)
        cli_locator_path(scope, root).unlink(missing_ok=True)
    return preview


def _apply_plan(plan, previous):
    manifest_path = Path(plan["manifest"])
    locator_path = Path(plan["locator"])
    previous_locator = locator_path.read_text(encoding="utf-8") if locator_path.exists() else None
    backup_root = manifest_path.parent / ".intentrail-install-backups" / _timestamp()
    installed = []
    configs = []
    try:
        for item in plan["operations"]:
            target = Path(item["target"])
            if item["state"] == "preserve-identical":
                continue
            if target.exists():
                backup = backup_root / "paths" / str(len(installed) + len(configs))
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(str(target), str(backup)) if target.is_dir() else shutil.copy2(str(target), str(backup))
                item["backup"] = str(backup)
            if item["kind"] == "copy":
                _atomic_copy(Path(item["source"]), target)
                marker = _owned_marker(target)
                marker.write_text(json.dumps({"owner": OWNER, "version": PRODUCT_VERSION}, sort_keys=True) + "\n", encoding="utf-8")
                installed.append({"host": item["host"], "path": str(target), "hash": _path_hash(target), "marker": str(marker)})
            elif item["kind"] == "codex-marketplace":
                _write_codex_marketplace(target, item["value"])
                configs.append({"host": item["host"], "kind": item["kind"], "path": str(target), "value": item["value"]})
            elif item["kind"] == "copilot-hooks":
                _write_copilot_hooks(target, Path(item["value"]))
                configs.append({"host": item["host"], "kind": item["kind"], "path": str(target), "value": item["value"]})
        manifest = {
            "schema_version": INSTALL_MANIFEST_SCHEMA_VERSION,
            "owner": OWNER,
            "product_version": PRODUCT_VERSION,
            "scope": plan["scope"],
            "root": plan["root"],
            "hosts": sorted(set((previous.get("hosts") or []) + plan["hosts"])),
            "cli_path": plan["cli_path"],
            "runtime_backend": "managed-cli",
            "installed": _merge_records(previous.get("installed", []), installed, "path"),
            "configs": _merge_records(previous.get("configs", []), configs, "path"),
            "last_backup": str(backup_root) if backup_root.exists() else None,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _atomic_write_json(manifest_path, manifest)
        locator = Path(plan["locator"])
        _atomic_write_text(locator, plan["cli_path"] + "\n")
        report = doctor(plan["hosts"], plan["scope"], plan["root"], run_hooks=True, cli_path=plan["cli_path"])
        failed = [item for item in report["hosts"] if item["support"] == "Unsupported"]
        if failed:
            raise InstallationError("Installed runtime failed doctor validation.", details={"hosts": failed})
        manifest["doctor"] = report
        _atomic_write_json(manifest_path, manifest)
        return {"installed": installed, "configs": configs, "manifest": str(manifest_path), "cli_path": plan["cli_path"], "doctor": report, "trust": plan["trust"]}
    except Exception:
        _restore_plan(plan)
        if previous:
            _atomic_write_json(manifest_path, previous)
        else:
            manifest_path.unlink(missing_ok=True)
        if previous_locator is not None:
            _atomic_write_text(locator_path, previous_locator)
        else:
            locator_path.unlink(missing_ok=True)
        raise


def _probe_version(cli):
    try:
        completed = _run_cli_process(cli, ["version", "--json"], text=True, capture_output=True, timeout=15)
        document = json.loads(completed.stdout)
        ok = completed.returncode == 0 and document.get("ok") and document.get("data", {}).get("product_version") == PRODUCT_VERSION
        return {"ok": bool(ok), "returncode": completed.returncode, "product_version": document.get("data", {}).get("product_version")}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": type(exc).__name__}


def _probe_hook(cli, host):
    payload = json.dumps({"session_id": "intentrail-doctor", "cwd": tempfile.gettempdir(), "tool_name": "Read", "tool_input": {"path": "README.md"}})
    try:
        completed = _run_cli_process(cli, ["hook", "--host", host, "--event", "PreToolUse"], input=payload, text=True, capture_output=True, timeout=15)
        document = json.loads(completed.stdout)
        return {"ok": completed.returncode == 0 and document == {}, "returncode": completed.returncode, "output": document}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": type(exc).__name__}


def _installed_hooks_reference_cli(records, cli):
    if not cli:
        return False
    needle = _quote_command(cli)
    hook_files = []
    for item in records:
        path = Path(item["path"])
        if path.is_dir():
            hook_files.extend(path.rglob("*hooks*.json"))
        elif "hook" in path.name.lower():
            hook_files.append(path)
    if not hook_files:
        return False
    for path in hook_files:
        commands = list(_hook_commands(_read_json(path, default={})))
        if not commands or any(needle not in command for command in commands):
            return False
    return True


def _run_cli_process(cli, arguments, **kwargs):
    command = [str(cli)] + list(arguments)
    return subprocess.run(command, check=False, **kwargs)


def _render_json_templates(root, replacements):
    for path in Path(root).rglob("*.json"):
        document = _read_json(path, default={})
        document = _replace_strings(document, replacements)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _find_markers(root):
    result = []
    for path in Path(root).rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in HOOK_MARKERS):
            result.append(str(path))
    return result


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


def _hook_commands(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"command", "commandWindows", "bash", "powershell"} and isinstance(item, str):
                yield item
            else:
                yield from _hook_commands(item)
    elif isinstance(value, list):
        for item in value:
            yield from _hook_commands(item)


def _quote_command(path):
    return '"{0}"'.format(Path(path).resolve().as_posix().replace('"', '\\"'))


def _bundle():
    path = Path(__file__).with_name(BUNDLE_NAME)
    if not path.is_file():
        raise InstallationError("IntentRail runtime bundle is missing; reinstall the package.")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("product_version") != PRODUCT_VERSION:
        raise InstallationError("IntentRail runtime bundle version does not match the CLI.")
    return document


def _user_data_root():
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or (user_home() / ".local" / "share"))
    return base / "IntentRail"


def _copy_state(source, target, manifest):
    if not target.exists():
        return "create"
    if not _is_owned(target, manifest):
        return "conflict"
    return "preserve-identical" if _path_hash(source) == _path_hash(target) else "replace-owned"


def _atomic_copy(source, target):
    if not source.exists():
        raise InstallationError("Distribution source is missing: {0}".format(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / (".intentrail-stage-" + uuid.uuid4().hex)
    shutil.copytree(str(source), str(stage)) if source.is_dir() else shutil.copy2(str(source), str(stage))
    old = target.parent / (".intentrail-old-" + uuid.uuid4().hex)
    if target.exists():
        os.replace(str(target), str(old))
    try:
        os.replace(str(stage), str(target))
    except Exception:
        if old.exists():
            os.replace(str(old), str(target))
        raise
    if old.exists():
        shutil.rmtree(str(old)) if old.is_dir() else old.unlink()


def _write_codex_marketplace(path, source):
    document = _read_json(path, default={"name": "intentrail-local", "interface": {"displayName": "IntentRail Local"}, "plugins": []})
    plugins = document.setdefault("plugins", [])
    existing = [item for item in plugins if item.get("name") == "intentrail"]
    if existing and not _codex_entry_owned(existing[0], source):
        raise InstallationError("Codex marketplace already has an unowned intentrail entry.")
    entry = {"name": "intentrail", "source": {"source": "local", "path": source}, "description": "IntentRail local plugin ({0})".format(OWNER), "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}
    document["plugins"] = [item for item in plugins if item.get("name") != "intentrail"] + [entry]
    _atomic_write_json(path, document)


def _remove_codex_marketplace_entry(path, source):
    if not path.exists():
        return
    document = _read_json(path, default={})
    document["plugins"] = [item for item in document.get("plugins", []) if not (item.get("name") == "intentrail" and _codex_entry_owned(item, source))]
    _atomic_write_json(path, document)


def _write_copilot_hooks(path, plugin_root):
    template = plugin_root / "hooks.json"
    document = _read_json(template, default=None)
    if document is None:
        raise InstallationError("Copilot Hook template is missing.")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, document)


def _config_state(kind, path, value):
    path = Path(path)
    if not path.exists():
        return "create"
    if kind == "codex-marketplace":
        entries = [item for item in _read_json(path, default={}).get("plugins", []) if item.get("name") == "intentrail"]
        if entries and _codex_entry_owned(entries[0], value):
            return "preserve-identical"
        return "modify-owned" if not entries else "conflict"
    if kind == "copilot-hooks":
        template = Path(value) / "hooks.json"
        return "preserve-identical" if template.exists() and _read_json(path, default={}) == _read_json(template, default={}) else "replace-owned"
    return "conflict"


def _codex_entry_owned(entry, source):
    return entry.get("source", {}).get("path") == source and OWNER in entry.get("description", "")


def _is_owned(path, manifest):
    return _owned_marker(path).is_file() or any(item.get("path") == str(path) for item in (manifest or {}).get("installed", []))


def _owned_marker(path):
    return path / ".intentrail-owned.json" if path.is_dir() else path.with_suffix(path.suffix + ".intentrail-owned.json")


def _path_hash(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {}
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = item.relative_to(path).as_posix()
        if relative.endswith(".intentrail-owned.json") or "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"}:
            continue
        manifest[relative] = hashlib.sha256(item.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise InstallationError("Invalid JSON configuration at {0}: {1}".format(path, exc))


def _atomic_write_json(path, document):
    _atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".intentrail-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore_plan(plan):
    for item in reversed(plan["operations"]):
        backup = item.get("backup")
        target = Path(item["target"])
        if not backup or not Path(backup).exists():
            if item.get("state") == "create" and target.exists():
                shutil.rmtree(str(target)) if target.is_dir() else target.unlink()
            continue
        if target.exists():
            shutil.rmtree(str(target)) if target.is_dir() else target.unlink()
        shutil.copytree(backup, str(target)) if Path(backup).is_dir() else shutil.copy2(backup, str(target))


def _merge_records(old, new, key):
    merged = {item[key]: item for item in old}
    merged.update({item[key]: item for item in new})
    return list(merged.values())


def _timestamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _trust_instruction(host):
    if host == "codex":
        return "Restart Codex, install IntentRail from the local marketplace, then review and trust its hooks."
    if host == "claude-code":
        return "Restart or reload Claude Code, inspect the IntentRail plugin, and approve its hooks."
    return None
