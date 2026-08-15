"""State invariants and recoverable-tail validation."""

from pathlib import Path

from .constants import CERTAINTIES, CONTRACT_STATUSES, INTENT_STATES, KIND_TO_FIELD, LIFECYCLES, SCHEMA_VERSION
from .contracts import reconstruct
from .errors import RecoveryRequired
from .events import read_events
from .util import canonical_bytes, ensure_schema_compatible, read_json, sha256_value


def validate_project(store, contract_id=None, repair_tail=False):
    errors = []
    warnings = []
    repairs = []
    index = store.load_index()
    config = store.load_config()
    precedents = read_json(store.precedents_path)
    for document, label in [(index, "index"), (config, "config"), (precedents, "precedents")]:
        try:
            ensure_schema_compatible(document)
        except Exception as exc:
            errors.append({"component": label, "message": str(exc)})
    entries = index.get("contracts", [])
    target_ids = [contract_id] if contract_id else [entry.get("contract_id") for entry in entries]
    results = []
    for current_id in target_ids:
        result = validate_contract(store, current_id, repair_tail=repair_tail)
        results.append(result)
        errors.extend(result["errors"])
        warnings.extend(result["warnings"])
        if result.get("repair"):
            repairs.append(result["repair"])
    indexed = {entry.get("contract_id") for entry in entries}
    on_disk = {path.name for path in (store.state_root / "contracts").iterdir() if path.is_dir()}
    for orphan in sorted(on_disk - indexed):
        warnings.append({"component": "index", "message": "Unindexed contract directory", "contract_id": orphan})
    return {
        "valid": not errors,
        "schema_version": SCHEMA_VERSION,
        "contracts": results,
        "errors": errors,
        "warnings": warnings,
        "repairs": repairs,
    }


def validate_contract(store, contract_id, repair_tail=False):
    errors = []
    warnings = []
    repair = None
    try:
        contract = store.load_contract(contract_id)
        _validate_contract_shape(contract)
        events, repair = read_events(store.events_path(contract_id), repair_tail=repair_tail)
        if not events:
            raise RecoveryRequired("Event log is empty.")
        rebuilt = reconstruct(events)
        if canonical_bytes(contract) != canonical_bytes(rebuilt):
            errors.append({"component": "snapshot", "contract_id": contract_id, "message": "Snapshot differs from event reconstruction."})
        _validate_checkpoints(store, contract_id, warnings, errors)
    except Exception as exc:
        errors.append({"component": "contract", "contract_id": contract_id, "message": str(exc)})
    return {"contract_id": contract_id, "valid": not errors, "errors": errors, "warnings": warnings, "repair": repair}


def _validate_contract_shape(contract):
    required = {
        "schema_version",
        "contract_id",
        "project_id",
        "version",
        "status",
        "created_at",
        "updated_at",
        "objective",
        "deliverables",
        "constraints",
        "acceptance_criteria",
        "decisions",
        "questions",
        "assumptions",
        "completed_work",
        "superseded_items",
        "current_stage",
        "next_material_action",
        "last_event_id",
        "event_head_hash",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise RecoveryRequired("Contract is missing required fields.", details={"missing": missing})
    if contract.get("status") not in CONTRACT_STATUSES:
        raise RecoveryRequired("Contract has invalid status.")
    if not isinstance(contract.get("version"), int) or contract["version"] < 1:
        raise RecoveryRequired("Contract version is invalid.")
    items = []
    if contract.get("objective") is not None:
        items.append(contract["objective"])
    for field in set(KIND_TO_FIELD.values()) | {"superseded_items"}:
        if field != "objective":
            items.extend(contract.get(field, []))
    ids = set()
    for item in items:
        _validate_item(item)
        if item["id"] in ids and item.get("lifecycle") not in {"superseded", "revoked"}:
            raise RecoveryRequired("Contract contains duplicate active item ids.")
        ids.add(item["id"])


def _validate_item(item):
    required = {"id", "kind", "text", "state", "certainty", "lifecycle", "source", "source_ref", "scope", "created_version", "updated_version", "supersedes", "depends_on", "invalidated_by", "tags"}
    if not isinstance(item, dict) or required - set(item):
        raise RecoveryRequired("Intent item is malformed.")
    if item["state"] not in INTENT_STATES:
        raise RecoveryRequired("Intent item has invalid state.")
    if item["certainty"] not in CERTAINTIES or item["lifecycle"] not in LIFECYCLES:
        raise RecoveryRequired("Intent item has invalid certainty or lifecycle.")


def _validate_checkpoints(store, contract_id, warnings, errors):
    directory = store.contract_dir(contract_id) / "checkpoints"
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        if path.name == "index.json":
            continue
        try:
            document = read_json(path)
            claimed = document.get("checkpoint_hash")
            unsigned = dict(document)
            unsigned.pop("checkpoint_hash", None)
            if claimed != sha256_value(unsigned):
                raise RecoveryRequired("Checkpoint hash mismatch.")
        except Exception as exc:
            errors.append({"component": "checkpoint", "path": str(path), "message": str(exc)})
