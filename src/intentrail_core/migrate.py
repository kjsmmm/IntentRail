"""Explicit, backed-up forward migration to the v2 intent semantics."""

import json
import shutil
from pathlib import Path

from .constants import SCHEMA_VERSION
from .contracts import reconstruct
from .errors import MigrationRequired
from .semantics import normalize_semantics
from .util import atomic_write_json, atomic_write_text, read_json, sha256_value, utc_now
from .validate import validate_project


def migrate(store, target):
    if target != SCHEMA_VERSION:
        raise MigrationRequired(
            "No migration path is implemented to {0}.".format(target),
            recovery_actions=["Use a version of IntentRail that supports the requested schema."],
        )
    index = read_json(store.index_path)
    source_version = index.get("schema_version")
    if source_version == SCHEMA_VERSION:
        validation = validate_project(store)
        if not validation["valid"]:
            raise MigrationRequired("Current state does not validate.", details=validation["errors"])
        return {"from": SCHEMA_VERSION, "to": SCHEMA_VERSION, "changed": False, "validation": validation}
    if source_version != "1.0.0":
        raise MigrationRequired("Unsupported source schema: {0}".format(source_version))

    paths = [store.index_path, store.config_path, store.precedents_path, store.state_root / "contracts", store.state_root / "bindings", store.state_root / "runtime"]
    backup = store.create_backup(paths, "schema-v1-to-v2")
    try:
        for contract_dir in sorted((store.state_root / "contracts").glob("*")):
            if not contract_dir.is_dir():
                continue
            events = _migrate_events(contract_dir / "events.jsonl")
            if events:
                atomic_write_json(contract_dir / "contract.json", reconstruct(events))
            _migrate_checkpoints(contract_dir / "checkpoints")
        for path in [store.index_path, store.config_path, store.precedents_path]:
            _upgrade_json_file(path)
        for path in (store.state_root / "bindings").glob("*.json"):
            _upgrade_json_file(path)
        runtime = store.state_root / "runtime"
        if runtime.exists():
            shutil.rmtree(str(runtime))
        runtime.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise MigrationRequired(
            "Schema migration failed after creating a backup.",
            details={"type": type(exc).__name__, "backup": backup["backup_root"]},
            recovery_actions=["Restore the backed-up state and retry with a compatible IntentRail version."],
        )
    validation = validate_project(store)
    if not validation["valid"]:
        raise MigrationRequired("Migrated state failed validation.", details={"errors": validation["errors"], "backup": backup["backup_root"]})
    return {"from": source_version, "to": SCHEMA_VERSION, "changed": True, "backup": backup, "validation": validation}


def _migrate_events(path):
    path = Path(path)
    if not path.exists():
        return []
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    previous_hash = None
    for event in events:
        _upgrade_document(event)
        event["schema_version"] = SCHEMA_VERSION
        event.setdefault("reconciliation_id", None)
        event.setdefault("reconciliation_index", None)
        event.setdefault("reconciliation_size", None)
        event["previous_hash"] = previous_hash
        event.pop("event_hash", None)
        event["event_hash"] = sha256_value(event)
        previous_hash = event["event_hash"]
    text = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for event in events)
    atomic_write_text(path, text)
    return events


def _migrate_checkpoints(directory):
    directory = Path(directory)
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        document = read_json(path)
        _upgrade_document(document)
        document["schema_version"] = SCHEMA_VERSION
        if "checkpoint_hash" in document:
            document.pop("checkpoint_hash", None)
            document["checkpoint_hash"] = sha256_value(document)
        atomic_write_json(path, document)


def _upgrade_json_file(path):
    path = Path(path)
    if not path.exists():
        return
    document = read_json(path)
    _upgrade_document(document)
    document["schema_version"] = SCHEMA_VERSION
    if path.name in {"index.json", "config.json", "precedents.json"}:
        document["updated_at"] = utc_now()
    atomic_write_json(path, document)


def _upgrade_document(value):
    if isinstance(value, dict):
        if "schema_version" in value:
            value["schema_version"] = SCHEMA_VERSION
        if _looks_like_item(value):
            normalize_semantics(value)
            value.setdefault("depends_on", [])
            value.setdefault("invalidated_by", [])
            value.setdefault("supersedes", [])
            value.setdefault("tags", [])
        for child in list(value.values()):
            _upgrade_document(child)
    elif isinstance(value, list):
        for child in value:
            _upgrade_document(child)


def _looks_like_item(value):
    return all(key in value for key in ["id", "kind", "text", "source", "source_ref"])
