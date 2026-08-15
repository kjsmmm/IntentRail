"""Append-only event log, hash-chain validation, and tail repair."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from .constants import INTENT_STATES, OPERATIONS, SCHEMA_VERSION
from .errors import RecoveryRequired, UsageError
from .util import append_jsonl, canonical_bytes, new_id, sha256_value, utc_now


def build_event(
    contract_id,
    sequence,
    operation,
    entity_kind,
    entity_id,
    before,
    after,
    source,
    source_ref,
    intent_state,
    version_before,
    version_after,
    reversible,
    inverse_of,
    idempotency_key,
    previous_hash,
    reconciliation_id=None,
    reconciliation_index=None,
    reconciliation_size=None,
    timestamp=None,
):
    if operation not in OPERATIONS:
        raise UsageError("Unsupported event operation: {0}".format(operation))
    if intent_state is not None and intent_state not in INTENT_STATES:
        raise UsageError("Unsupported intent_state: {0}".format(intent_state))
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": new_id(),
        "contract_id": contract_id,
        "sequence": sequence,
        "timestamp": timestamp or utc_now(),
        "operation": operation,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "before": before,
        "after": after,
        "source": source,
        "source_ref": source_ref,
        "intent_state": intent_state,
        "contract_version_before": version_before,
        "contract_version_after": version_after,
        "reversible": bool(reversible),
        "inverse_of": inverse_of,
        "idempotency_key": idempotency_key,
        "previous_hash": previous_hash,
        "reconciliation_id": reconciliation_id,
        "reconciliation_index": reconciliation_index,
        "reconciliation_size": reconciliation_size,
    }
    event["event_hash"] = sha256_value(event)
    return event


def verify_event_hash(event):
    unsigned = dict(event)
    claimed = unsigned.pop("event_hash", None)
    return isinstance(claimed, str) and claimed == sha256_value(unsigned)


def read_events(path, repair_tail=False):
    path = Path(path)
    if not path.exists():
        return [], None
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    events = []
    valid_bytes = b""
    repair = None
    for index, line in enumerate(lines):
        is_last = index == len(lines) - 1
        complete = line.endswith(b"\n") or line.endswith(b"\r\n")
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if is_last and repair_tail:
                repair = _repair_tail(path, valid_bytes, "invalid-json")
                break
            raise RecoveryRequired(
                "Event log contains invalid JSON.",
                details={"path": str(path), "line": index + 1, "error": str(exc)},
            )
        if is_last and not complete:
            if repair_tail:
                repair = _repair_tail(path, valid_bytes, "incomplete-line")
                break
            raise RecoveryRequired(
                "Event log ends with an incomplete line.",
                details={"path": str(path), "line": index + 1},
                recovery_actions=["Run intentrail validate --repair-tail."],
            )
        events.append(event)
        valid_bytes += line
    validate_chain(events)
    return events, repair


def validate_chain(events):
    previous_hash = None
    previous_version = 0
    contract_id = None
    seen_ids = set()
    seen_keys = set()
    reconciliation_groups = {}
    for position, event in enumerate(events, 1):
        if not isinstance(event, dict) or not verify_event_hash(event):
            raise RecoveryRequired("Event hash mismatch.", details={"sequence": position})
        if event.get("sequence") != position:
            raise RecoveryRequired("Event sequence is not continuous.", details={"sequence": position})
        if event.get("previous_hash") != previous_hash:
            raise RecoveryRequired("Event hash chain is broken.", details={"sequence": position})
        if event.get("contract_version_before") != previous_version:
            raise RecoveryRequired("Contract version chain is broken.", details={"sequence": position})
        if event.get("contract_version_after") != previous_version + 1:
            raise RecoveryRequired("Event must advance the contract by one version.", details={"sequence": position})
        if contract_id is None:
            contract_id = event.get("contract_id")
        elif event.get("contract_id") != contract_id:
            raise RecoveryRequired("Event log mixes multiple contracts.", details={"sequence": position})
        event_id = event.get("event_id")
        key = event.get("idempotency_key")
        if event_id in seen_ids or key in seen_keys:
            raise RecoveryRequired("Event log contains duplicate identifiers.", details={"sequence": position})
        seen_ids.add(event_id)
        seen_keys.add(key)
        reconciliation_id = event.get("reconciliation_id")
        if reconciliation_id is not None:
            index = event.get("reconciliation_index")
            size = event.get("reconciliation_size")
            if not isinstance(index, int) or not isinstance(size, int) or index < 1 or size < 1:
                raise RecoveryRequired("Reconciliation event has invalid group metadata.", details={"sequence": position})
            group = reconciliation_groups.setdefault(reconciliation_id, {"size": size, "indices": [], "sequences": []})
            if group["size"] != size:
                raise RecoveryRequired("Reconciliation group size is inconsistent.", details={"reconciliation_id": reconciliation_id})
            group["indices"].append(index)
            group["sequences"].append(position)
        previous_hash = event["event_hash"]
        previous_version = event["contract_version_after"]
    for reconciliation_id, group in reconciliation_groups.items():
        expected = list(range(1, group["size"] + 1))
        if sorted(group["indices"]) != expected or group["sequences"] != list(range(min(group["sequences"]), min(group["sequences"]) + group["size"])):
            raise RecoveryRequired("Reconciliation group is incomplete or non-contiguous.", details={"reconciliation_id": reconciliation_id})


def append_event(path, event):
    append_jsonl(path, event)


def append_events_atomic(path, events):
    """Append a validated event batch with one atomic file replacement."""
    from .util import atomic_write_text

    path = Path(path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        raise RecoveryRequired("Event log ends with an incomplete line.")
    addition = "".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for event in events
    )
    atomic_write_text(path, existing + addition)


def find_idempotent(events, key):
    for event in events:
        if event.get("idempotency_key") == key:
            return event
    return None


def _repair_tail(path, valid_bytes, reason):
    path = Path(path)
    backup = path.with_name(path.name + ".tail-backup-" + utc_now().replace(":", ""))
    shutil.copy2(str(path), str(backup))
    fd, temporary = tempfile.mkstemp(prefix=".{0}.".format(path.name), suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(valid_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"reason": reason, "backup": str(backup), "retained_bytes": len(valid_bytes)}
