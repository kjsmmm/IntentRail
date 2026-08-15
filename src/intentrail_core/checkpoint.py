"""Immutable semantic checkpoints and explicit recovery."""

import copy
from pathlib import Path

from .constants import SCHEMA_VERSION
from .contracts import SEMANTIC_FIELDS, apply_change, compact_status, load_reconciled
from .errors import RecoveryRequired, StateNotFound, UsageError
from .events import read_events
from .util import atomic_write_json, new_id, read_json, sha256_value, utc_now


def create_checkpoint(store, contract_id, purpose="manual", evidence=None):
    contract, events = load_reconciled(store, contract_id)
    result = apply_change(
        store,
        contract_id,
        {
            "operation": "CHECKPOINT",
            "entity_kind": "contract",
            "entity_id": contract_id,
            "after": {"purpose": purpose},
            "source": {"kind": "agent"},
            "source_ref": "checkpoint-command",
            "expected_version": contract["version"],
            "idempotency_key": "checkpoint:{0}:{1}:{2}".format(contract_id, contract["version"], purpose),
            "reversible": False,
        },
    )
    contract = result["contract"]
    checkpoint_id = new_id()
    recent = [
        "v{0} {1} {2}".format(event["contract_version_after"], event["operation"], event["entity_kind"])
        for event in events[-5:]
    ]
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "contract_id": contract_id,
        "contract_version": contract["version"],
        "created_at": utc_now(),
        "objective": copy.deepcopy(contract.get("objective")),
        "constraints": copy.deepcopy(contract.get("constraints", [])),
        "acceptance_criteria": copy.deepcopy(contract.get("acceptance_criteria", [])),
        "completed_work": [item.get("text", "") for item in contract.get("completed_work", [])],
        "completed_work_items": copy.deepcopy(contract.get("completed_work", [])),
        "open_questions": copy.deepcopy(contract.get("questions", [])),
        "recent_changes": recent,
        "next_action": contract.get("next_material_action"),
        "do_not_repeat": [],
        "evidence": copy.deepcopy(evidence or []),
        "deliverables": copy.deepcopy(contract.get("deliverables", [])),
        "decisions": copy.deepcopy(contract.get("decisions", [])),
        "assumptions": copy.deepcopy(contract.get("assumptions", [])),
        "superseded_items": copy.deepcopy(contract.get("superseded_items", [])),
        "blocked_items": [
            copy.deepcopy(item)
            for field in ["decisions", "assumptions", "completed_work", "questions"]
            for item in contract.get(field, [])
            if item.get("lifecycle") in {"conflicted", "stale", "needs_review"}
        ],
        "current_stage": contract.get("current_stage"),
        "status": contract.get("status"),
        "purpose": purpose,
    }
    checkpoint["checkpoint_hash"] = sha256_value(checkpoint)
    checkpoints_dir = store.contract_dir(contract_id) / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoints_dir / "{0}.json".format(checkpoint_id)
    atomic_write_json(path, checkpoint)
    _append_index(store, contract_id, checkpoint, path)
    return {"checkpoint": checkpoint, "path": str(path), "contract": compact_status(contract)}


def list_checkpoints(store, contract_id):
    path = store.contract_dir(contract_id) / "checkpoints" / "index.json"
    if not path.exists():
        return {"contract_id": contract_id, "checkpoints": []}
    return read_json(path)


def find_checkpoint(store, checkpoint_id, contract_id=None):
    if contract_id:
        candidates = [store.contract_dir(contract_id) / "checkpoints" / "{0}.json".format(checkpoint_id)]
    else:
        candidates = list((store.state_root / "contracts").glob("*/checkpoints/{0}.json".format(checkpoint_id)))
    if len(candidates) != 1 or not candidates[0].exists():
        raise StateNotFound("Checkpoint not found: {0}".format(checkpoint_id))
    checkpoint = read_json(candidates[0])
    _verify_checkpoint(checkpoint)
    return checkpoint, candidates[0]


def show_checkpoint(store, checkpoint_id):
    checkpoint, path = find_checkpoint(store, checkpoint_id)
    return {"checkpoint": checkpoint, "path": str(path)}


def resume_contract(store, contract_id):
    contract, _ = load_reconciled(store, contract_id)
    store.select_contract(contract_id)
    if contract["status"] == "paused":
        result = apply_change(
            store,
            contract_id,
            {
                "operation": "RESUME",
                "entity_kind": "contract",
                "entity_id": contract_id,
                "source": {"kind": "user"},
                "source_ref": "resume-command",
                "expected_version": contract["version"],
                "idempotency_key": "resume:{0}:{1}".format(contract_id, contract["version"]),
            },
        )
        contract = result["contract"]
    return {"contract": compact_status(contract), "restored_business_files": False}


def resume_checkpoint(store, checkpoint_id):
    checkpoint, _ = find_checkpoint(store, checkpoint_id)
    contract_id = checkpoint["contract_id"]
    current, _ = load_reconciled(store, contract_id)
    semantic = {
        "objective": checkpoint.get("objective"),
        "deliverables": checkpoint.get("deliverables", []),
        "constraints": checkpoint.get("constraints", []),
        "acceptance_criteria": checkpoint.get("acceptance_criteria", []),
        "decisions": checkpoint.get("decisions", []),
        "questions": checkpoint.get("open_questions", []),
        "assumptions": checkpoint.get("assumptions", []),
        "completed_work": copy.deepcopy(checkpoint.get("completed_work_items")) or [
            _completed_item(text, current["version"] + 1) for text in checkpoint.get("completed_work", [])
        ],
        "superseded_items": checkpoint.get("superseded_items", []),
        "current_stage": checkpoint.get("current_stage"),
        "next_material_action": checkpoint.get("next_action"),
        "status": "active",
    }
    result = apply_change(
        store,
        contract_id,
        {
            "operation": "RESUME",
            "entity_kind": "contract",
            "entity_id": contract_id,
            "after": semantic,
            "source": {"kind": "user"},
            "source_ref": "checkpoint:{0}".format(checkpoint_id),
            "expected_version": current["version"],
            "idempotency_key": "resume-checkpoint:{0}:{1}".format(checkpoint_id, current["version"]),
            "reversible": True,
        },
    )
    store.select_contract(contract_id)
    return {
        "contract": compact_status(result["contract"]),
        "checkpoint_id": checkpoint_id,
        "restored_business_files": False,
        "do_not_repeat": checkpoint.get("do_not_repeat", []),
    }


def _append_index(store, contract_id, checkpoint, path):
    index_path = path.parent / "index.json"
    if index_path.exists():
        index = read_json(index_path)
    else:
        index = {
            "schema_version": SCHEMA_VERSION,
            "contract_id": contract_id,
            "updated_at": utc_now(),
            "checkpoints": [],
        }
    index["updated_at"] = utc_now()
    index["checkpoints"].append(
        {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "created_at": checkpoint["created_at"],
            "contract_version": checkpoint["contract_version"],
            "purpose": checkpoint["purpose"],
            "file_hash": checkpoint["checkpoint_hash"],
            "recoverable": True,
        }
    )
    atomic_write_json(index_path, index)


def _verify_checkpoint(checkpoint):
    claimed = checkpoint.get("checkpoint_hash")
    unsigned = dict(checkpoint)
    unsigned.pop("checkpoint_hash", None)
    if claimed != sha256_value(unsigned):
        raise RecoveryRequired("Checkpoint hash mismatch.")


def _completed_item(text, version):
    from .contracts import normalize_item

    return normalize_item(
        text,
        "completed_work",
        version,
        {"kind": "agent"},
        "checkpoint-resume",
        "inferred",
    )
