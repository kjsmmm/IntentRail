"""Event-sourced current-intent projections and deterministic delta application."""

import copy
from pathlib import Path

from .constants import CERTAINTIES, CONTRACT_STATUSES, ITEM_KINDS, KIND_TO_FIELD, SCHEMA_VERSION
from .errors import IntentConflict, PermissionRequired, RecoveryRequired, StaleVersion, StateNotFound, UsageError
from .events import append_event, build_event, find_idempotent, read_events
from .impact import propagate_impact
from .semantics import is_active, normalize_semantics, set_certainty, set_lifecycle
from .util import atomic_write_json, new_id, utc_now


SEMANTIC_FIELDS = [
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
    "status",
]


def normalize_source(value):
    if isinstance(value, str):
        value = {"kind": value}
    if not isinstance(value, dict):
        raise UsageError("source must be an object or source kind string")
    kind = value.get("kind")
    if kind not in {"user", "agent", "trusted_project_source", "external_untrusted_content", "system"}:
        raise UsageError("Unsupported source kind: {0}".format(kind))
    return copy.deepcopy(value)


def normalize_item(raw, kind, version, source=None, source_ref=None, default_state=None):
    if kind not in ITEM_KINDS:
        raise UsageError("Unsupported intent item kind: {0}".format(kind))
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
        raise UsageError("Intent items require non-empty text.")
    item = copy.deepcopy(raw)
    item["id"] = item.get("id") or new_id()
    item["kind"] = kind
    default_certainty = default_state if default_state in CERTAINTIES else "inferred"
    normalize_semantics(item, default_certainty=default_certainty)
    item["source"] = normalize_source(item.get("source") or source or {"kind": "agent"})
    item["source_ref"] = item.get("source_ref") or source_ref or "unspecified"
    item["scope"] = item.get("scope") or "task"
    item["created_version"] = int(item.get("created_version") or version)
    item["updated_version"] = version
    item["supersedes"] = list(item.get("supersedes") or [])
    item["depends_on"] = list(item.get("depends_on") or [])
    item["invalidated_by"] = list(item.get("invalidated_by") or [])
    item["tags"] = list(item.get("tags") or [])
    return item


def create_contract(store, payload):
    if not isinstance(payload, dict):
        raise UsageError("Contract input must be a JSON object.")
    index = store.load_index()
    objective_raw = payload.get("objective")
    if objective_raw is None:
        raise UsageError("Contract creation requires objective.")
    contract_id = payload.get("contract_id") or new_id()
    if store.contract_dir(contract_id).exists():
        raise IntentConflict("Contract already exists: {0}".format(contract_id))
    source = normalize_source(payload.get("source") or {"kind": "user"})
    source_ref = payload.get("source_ref") or "contract-create"
    default_state = payload.get("default_state") or ("confirmed" if source["kind"] == "user" else "inferred")
    now = utc_now()
    contract = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": contract_id,
        "project_id": index["project_id"],
        "version": 1,
        "status": payload.get("status") or "active",
        "created_at": now,
        "updated_at": now,
        "objective": normalize_item(objective_raw, "objective", 1, source, source_ref, default_state),
        "deliverables": _normalize_list(payload.get("deliverables"), "deliverable", 1, source, source_ref, default_state),
        "constraints": _normalize_mixed_list(payload.get("constraints"), "constraint", 1, source, source_ref, default_state),
        "acceptance_criteria": _normalize_list(payload.get("acceptance_criteria"), "acceptance_criterion", 1, source, source_ref, default_state),
        "decisions": _normalize_list(payload.get("decisions"), "decision", 1, source, source_ref, default_state),
        "questions": _normalize_list(payload.get("questions"), "question", 1, source, source_ref, "assumed"),
        "assumptions": _normalize_list(payload.get("assumptions"), "assumption", 1, source, source_ref, "assumed"),
        "completed_work": _normalize_list(payload.get("completed_work"), "completed_work", 1, source, source_ref, default_state),
        "superseded_items": [],
        "current_stage": payload.get("current_stage"),
        "next_material_action": payload.get("next_material_action"),
        "last_event_id": None,
        "event_head_hash": None,
    }
    if contract["status"] not in CONTRACT_STATUSES:
        raise UsageError("Unsupported contract status: {0}".format(contract["status"]))
    if payload.get("title"):
        contract["title"] = str(payload["title"])
    if payload.get("evidence") is not None:
        contract["evidence"] = copy.deepcopy(payload.get("evidence"))
    if payload.get("do_not_repeat") is not None:
        contract["do_not_repeat"] = list(payload.get("do_not_repeat"))
    if payload.get("source_revision") is not None:
        contract["source_revision"] = str(payload.get("source_revision"))
    _enforce_contract_trust(contract)
    store.contract_dir(contract_id).mkdir(parents=True, exist_ok=False)
    (store.contract_dir(contract_id) / "checkpoints").mkdir()
    (store.contract_dir(contract_id) / "backups").mkdir()
    initial_snapshot = copy.deepcopy(contract)
    event = build_event(
        contract_id=contract_id,
        sequence=1,
        operation="ADD",
        entity_kind="contract",
        entity_id=contract_id,
        before=None,
        after=initial_snapshot,
        source=source,
        source_ref=source_ref,
        intent_state=contract["objective"]["state"],
        version_before=0,
        version_after=1,
        reversible=False,
        inverse_of=None,
        idempotency_key=payload.get("idempotency_key") or "create:{0}".format(contract_id),
        previous_hash=None,
        timestamp=now,
    )
    contract["last_event_id"] = event["event_id"]
    contract["event_head_hash"] = event["event_hash"]
    append_event(store.events_path(contract_id), event)
    atomic_write_json(store.contract_path(contract_id), contract)
    store.upsert_contract_index(contract)
    store.select_contract(contract_id)
    return {"contract": contract, "event": event, "first_persistence_notice": True}


def apply_change(store, contract_id, payload, inverse_of=None):
    if not isinstance(payload, dict):
        raise UsageError("Event input must be a JSON object.")
    idempotency_key = payload.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise UsageError("Mutating events require idempotency_key.")
    with store.contract_lock(contract_id):
        contract, events = load_reconciled(store, contract_id)
        duplicate = find_idempotent(events, idempotency_key)
        if duplicate:
            return {"contract": contract, "event": duplicate, "duplicate": True}
        expected = payload.get("expected_version")
        if expected is None:
            raise UsageError("Mutating events require expected_version.")
        if int(expected) != contract["version"]:
            raise StaleVersion(
                "Expected contract version {0}, found {1}.".format(expected, contract["version"]),
                details={"expected": expected, "actual": contract["version"]},
            )
        operation = str(payload.get("operation") or "").upper()
        entity_kind = payload.get("entity_kind") or _infer_kind(payload.get("after"))
        entity_id = payload.get("entity_id")
        source = normalize_source(payload.get("source") or {"kind": "agent"})
        source_ref = payload.get("source_ref") or "event-input"
        before = copy.deepcopy(payload.get("before"))
        if before is None and entity_id:
            before = copy.deepcopy(find_item(contract, entity_id))
        raw_after = payload.get("after")
        if operation == "CONFIRM" and raw_after is None:
            raw_after = before
        after = _prepare_after(raw_after, operation, entity_kind, entity_id, contract["version"] + 1, source, source_ref, payload.get("intent_state"))
        _enforce_change_trust(operation, source, before, after)
        event = build_event(
            contract_id=contract_id,
            sequence=len(events) + 1,
            operation=operation,
            entity_kind=entity_kind,
            entity_id=entity_id or (after.get("id") if isinstance(after, dict) else None),
            before=before,
            after=after,
            source=source,
            source_ref=source_ref,
            intent_state=(after.get("state") if isinstance(after, dict) else payload.get("intent_state")),
            version_before=contract["version"],
            version_after=contract["version"] + 1,
            reversible=payload.get("reversible", operation in {"ADD", "MODIFY", "REVOKE", "CONFIRM", "PAUSE", "RESUME"}),
            inverse_of=inverse_of,
            idempotency_key=idempotency_key,
            previous_hash=events[-1]["event_hash"],
        )
        updated = apply_event_to_contract(contract, event)
        append_event(store.events_path(contract_id), event)
        atomic_write_json(store.contract_path(contract_id), updated)
    store.upsert_contract_index(updated)
    return {"contract": updated, "event": event, "duplicate": False}


def apply_event_to_contract(contract, event):
    operation = event["operation"]
    updated = copy.deepcopy(contract)
    if event["sequence"] == 1 and event["entity_kind"] == "contract":
        updated = copy.deepcopy(event["after"])
    elif operation == "ADD":
        _add_item(updated, event["after"])
    elif operation in {"MODIFY", "RESOLVE"}:
        changed = _replace_item(updated, event["entity_id"], event["after"], supersede=True)
        propagate_impact(
            updated,
            [event["entity_id"]],
            [changed.get("scope"), (event.get("after") or {}).get("scope")],
            event["event_id"],
            event["contract_version_after"],
        )
        if changed.get("kind") in {"objective", "deliverable", "constraint", "acceptance_criterion", "preference", "prohibition"}:
            updated["next_material_action"] = None
    elif operation == "REVOKE":
        changed = _revoke_item(updated, event["entity_id"])
        propagate_impact(updated, [event["entity_id"]], [changed.get("scope")], event["event_id"], event["contract_version_after"])
        if changed.get("kind") in {"objective", "deliverable", "constraint", "acceptance_criterion", "preference", "prohibition"}:
            updated["next_material_action"] = None
    elif operation == "CONFIRM":
        item = find_item(updated, event["entity_id"])
        if item is None:
            raise RecoveryRequired("CONFIRM targets a missing item.")
        set_certainty(item, "confirmed")
        item["updated_version"] = event["contract_version_after"]
    elif operation == "CONFLICT":
        existing = find_item(updated, event["entity_id"]) if event["entity_id"] else None
        if existing:
            set_lifecycle(existing, "conflicted")
            existing["updated_version"] = event["contract_version_after"]
        if event["after"] and (existing is None or event["after"].get("id") != existing.get("id")):
            _add_item(updated, event["after"])
    elif operation == "DEFER":
        if event["after"]:
            deferred = copy.deepcopy(event["after"])
            set_certainty(deferred, "assumed")
            deferred.setdefault("tags", []).append("deferred")
            _add_item(updated, deferred)
    elif operation == "PROGRESS":
        if not isinstance(event.get("after"), dict):
            raise RecoveryRequired("PROGRESS event requires a progress object.")
        for field in ["current_stage", "next_material_action"]:
            if field in event["after"]:
                value = event["after"][field]
                if value is not None and not isinstance(value, str):
                    raise RecoveryRequired("PROGRESS field must be a string or null: {0}".format(field))
                updated[field] = value
    elif operation == "PAUSE":
        updated["status"] = "paused"
    elif operation == "RESUME":
        updated["status"] = "active"
        if event["entity_kind"] == "contract" and isinstance(event["after"], dict):
            _restore_semantic(updated, event["after"])
    elif operation == "COMPLETE":
        updated["status"] = "completed"
    elif operation == "ARCHIVE":
        updated["status"] = "archived"
    elif operation == "UNDO":
        if event["entity_kind"] != "contract" or not isinstance(event["after"], dict):
            raise RecoveryRequired("UNDO event lacks a semantic snapshot.")
        _restore_semantic(updated, event["after"])
    elif operation in {"CHECKPOINT", "VERIFY_PASS", "VERIFY_FAIL", "MIGRATE"}:
        pass
    else:
        raise RecoveryRequired("Cannot replay operation: {0}".format(operation))
    updated["version"] = event["contract_version_after"]
    updated["updated_at"] = event["timestamp"]
    updated["last_event_id"] = event["event_id"]
    updated["event_head_hash"] = event["event_hash"]
    return updated


def load_reconciled(store, contract_id):
    contract = store.load_contract(contract_id)
    events, _ = read_events(store.events_path(contract_id))
    if not events:
        raise RecoveryRequired("Contract event log is missing or empty.")
    head = events[-1]
    if contract.get("version") == head["contract_version_after"] and contract.get("event_head_hash") == head["event_hash"]:
        return contract, events
    if contract.get("version", 0) <= head["contract_version_after"]:
        rebuilt = reconstruct(events)
        atomic_write_json(store.contract_path(contract_id), rebuilt)
        store.upsert_contract_index(rebuilt)
        return rebuilt, events
    raise RecoveryRequired("Contract snapshot is ahead of its verified event log.")


def reconstruct(events, through_version=None):
    if not events:
        raise RecoveryRequired("Cannot reconstruct without events.")
    contract = None
    for event in events:
        if through_version is not None and event["contract_version_after"] > through_version:
            break
        if contract is None:
            if event["sequence"] != 1 or event["entity_kind"] != "contract":
                raise RecoveryRequired("First event is not a contract creation event.")
            contract = copy.deepcopy(event["after"])
        contract = apply_event_to_contract(contract, event)
    if contract is None:
        raise RecoveryRequired("Requested version is before contract creation.")
    return contract


def undo(store, contract_id, event_id=None):
    with store.contract_lock(contract_id):
        contract, events = load_reconciled(store, contract_id)
        target = None
        if event_id:
            target = next((event for event in events if event["event_id"] == event_id), None)
        else:
            target = next((event for event in reversed(events) if event.get("reversible") and event.get("operation") != "UNDO"), None)
        if target is None:
            raise StateNotFound("No reversible event was found.")
        if not target.get("reversible"):
            raise UsageError("The selected event is not reversible.")
        prior = reconstruct(events, through_version=target["contract_version_before"])
        semantic = {field: copy.deepcopy(prior.get(field)) for field in SEMANTIC_FIELDS}
        event = build_event(
            contract_id=contract_id,
            sequence=len(events) + 1,
            operation="UNDO",
            entity_kind="contract",
            entity_id=contract_id,
            before={field: copy.deepcopy(contract.get(field)) for field in SEMANTIC_FIELDS},
            after=semantic,
            source={"kind": "user"},
            source_ref="undo-command",
            intent_state=None,
            version_before=contract["version"],
            version_after=contract["version"] + 1,
            reversible=False,
            inverse_of=target["event_id"],
            idempotency_key="undo:{0}:{1}".format(target["event_id"], contract["version"]),
            previous_hash=events[-1]["event_hash"],
        )
        updated = apply_event_to_contract(contract, event)
        append_event(store.events_path(contract_id), event)
        atomic_write_json(store.contract_path(contract_id), updated)
    store.upsert_contract_index(updated)
    return {"contract": updated, "event": event, "undone_event": target["event_id"]}


def diff_versions(store, contract_id, from_version=None, to_version=None):
    _, events = load_reconciled(store, contract_id)
    latest = events[-1]["contract_version_after"]
    start = 1 if from_version is None else int(from_version)
    end = latest if to_version is None else int(to_version)
    if start < 1 or end < start or end > latest:
        raise UsageError("Invalid diff version range.")
    before = reconstruct(events, start)
    after = reconstruct(events, end)
    changes = []
    for event in events:
        version = event["contract_version_after"]
        if start < version <= end:
            changes.append(
                {
                    "version": version,
                    "operation": event["operation"],
                    "entity_kind": event["entity_kind"],
                    "entity_id": event["entity_id"],
                    "source_ref": event["source_ref"],
                    "timestamp": event["timestamp"],
                }
            )
    return {"from": before, "to": after, "changes": changes}


def compact_status(contract, events=None):
    status = {
        "contract_id": contract["contract_id"],
        "version": contract["version"],
        "status": contract["status"],
        "objective": _text(contract.get("objective")),
        "constraints": [_text(item) for item in contract.get("constraints", []) if is_active(item)],
        "acceptance_criteria": [_text(item) for item in contract.get("acceptance_criteria", []) if is_active(item)],
        "decisions": [_text(item) for item in contract.get("decisions", []) if is_active(item)],
        "temporary_assumptions": [_text(item) for item in contract.get("assumptions", []) if is_active(item) and item.get("certainty") in {"inferred", "assumed"}],
        "open_questions": [_text(item) for item in contract.get("questions", []) if item.get("lifecycle") in {"active", "conflicted"}],
        "completed_work": [_text(item) for item in contract.get("completed_work", []) if is_active(item)],
        "stale_items": [_summary(item) for item in _all_current_items(contract) if item.get("lifecycle") == "stale"],
        "needs_review": [_summary(item) for item in _all_current_items(contract) if item.get("lifecycle") == "needs_review"],
        "current_stage": contract.get("current_stage"),
        "next_material_action": contract.get("next_material_action"),
    }
    if events is not None:
        status["recent_changes"] = [
            {
                "version": event["contract_version_after"],
                "operation": event["operation"],
                "entity_kind": event["entity_kind"],
                "source_ref": event["source_ref"],
            }
            for event in events[-5:]
            if event["sequence"] > 1
        ]
    return status


def find_item(contract, item_id):
    objective = contract.get("objective")
    if isinstance(objective, dict) and objective.get("id") == item_id:
        return objective
    for field in KIND_TO_FIELD.values():
        if field == "objective":
            continue
        for item in contract.get(field, []):
            if item.get("id") == item_id:
                return item
    for item in contract.get("superseded_items", []):
        if item.get("id") == item_id:
            return item
    return None


def _prepare_after(raw, operation, entity_kind, entity_id, version, source, source_ref, intent_state):
    if operation in {"PAUSE", "RESUME", "COMPLETE", "ARCHIVE", "CHECKPOINT", "VERIFY_PASS", "VERIFY_FAIL", "MIGRATE", "UNDO", "PROGRESS"}:
        return copy.deepcopy(raw)
    if operation == "REVOKE" and raw is None:
        return None
    if not entity_kind:
        raise UsageError("Intent change requires entity_kind.")
    if entity_kind == "contract":
        return copy.deepcopy(raw)
    item = normalize_item(raw, entity_kind, version, source, source_ref, intent_state)
    if entity_id and operation == "CONFIRM":
        item["id"] = entity_id
    if operation == "CONFIRM":
        set_certainty(item, "confirmed")
    if operation == "CONFLICT":
        set_lifecycle(item, "conflicted")
    return item


def _add_item(contract, item):
    if not isinstance(item, dict):
        raise RecoveryRequired("ADD event has no intent item.")
    field = KIND_TO_FIELD.get(item.get("kind"))
    if field is None:
        raise RecoveryRequired("ADD event has unknown intent kind.")
    if field == "objective":
        current = contract.get("objective")
        if current and is_active(current):
            raise IntentConflict("Adding a second objective requires MODIFY or a new contract.")
        contract["objective"] = copy.deepcopy(item)
        return
    if find_item(contract, item.get("id")):
        raise RecoveryRequired("ADD event reuses an existing item id.")
    contract.setdefault(field, []).append(copy.deepcopy(item))


def _replace_item(contract, item_id, replacement, supersede):
    location = _find_location(contract, item_id)
    if location is None:
        raise RecoveryRequired("Event targets a missing item: {0}".format(item_id))
    field, position, old = location
    new_item = copy.deepcopy(replacement)
    if supersede:
        old_copy = copy.deepcopy(old)
        set_lifecycle(old_copy, "superseded")
        contract.setdefault("superseded_items", []).append(old_copy)
        new_item.setdefault("supersedes", []).append(old["id"])
    if field == "objective":
        contract["objective"] = new_item
    else:
        contract[field][position] = new_item
    return old


def _revoke_item(contract, item_id):
    location = _find_location(contract, item_id)
    if location is None:
        raise RecoveryRequired("REVOKE targets a missing item: {0}".format(item_id))
    field, position, old = location
    old_copy = copy.deepcopy(old)
    set_lifecycle(old_copy, "revoked")
    contract.setdefault("superseded_items", []).append(old_copy)
    if field == "objective":
        contract["objective"] = None
    else:
        del contract[field][position]
    return old


def _find_location(contract, item_id):
    objective = contract.get("objective")
    if isinstance(objective, dict) and objective.get("id") == item_id:
        return "objective", None, objective
    for field in set(KIND_TO_FIELD.values()):
        if field == "objective":
            continue
        for position, item in enumerate(contract.get(field, [])):
            if item.get("id") == item_id:
                return field, position, item
    return None


def _restore_semantic(contract, snapshot):
    for field in SEMANTIC_FIELDS:
        if field in snapshot:
            contract[field] = copy.deepcopy(snapshot[field])


def _normalize_list(values, kind, version, source, source_ref, default_state):
    return [normalize_item(value, kind, version, source, source_ref, default_state) for value in (values or [])]


def _normalize_mixed_list(values, default_kind, version, source, source_ref, default_state):
    result = []
    for value in values or []:
        kind = value.get("kind", default_kind) if isinstance(value, dict) else default_kind
        result.append(normalize_item(value, kind, version, source, source_ref, default_state))
    return result


def _infer_kind(after):
    return after.get("kind") if isinstance(after, dict) else None


def _text(item):
    return item.get("text") if isinstance(item, dict) else None


def _summary(item):
    return {"id": item.get("id"), "kind": item.get("kind"), "text": item.get("text"), "lifecycle": item.get("lifecycle")}


def _all_current_items(contract):
    if isinstance(contract.get("objective"), dict):
        yield contract["objective"]
    seen_fields = set()
    for field in KIND_TO_FIELD.values():
        if field == "objective" or field in seen_fields:
            continue
        seen_fields.add(field)
        for item in contract.get(field, []):
            yield item


def _enforce_contract_trust(contract):
    items = []
    if contract.get("objective"):
        items.append(contract["objective"])
    for field in set(KIND_TO_FIELD.values()):
        if field != "objective":
            items.extend(contract.get(field, []))
    for item in items:
        source_kind = item.get("source", {}).get("kind")
        if item.get("certainty") == "confirmed" and source_kind not in {"user", "trusted_project_source"}:
            raise PermissionRequired(
                "Only direct user intent or an explicitly trusted project source may create confirmed items.",
                details={"item_id": item.get("id"), "source_kind": source_kind},
            )


def _enforce_change_trust(operation, source, before, after):
    source_kind = source.get("kind")
    trusted = source_kind in {"user", "trusted_project_source"}
    if source_kind == "external_untrusted_content" and operation in {
        "MODIFY",
        "REVOKE",
        "CONFIRM",
        "CONFLICT",
        "RESOLVE",
        "PAUSE",
        "RESUME",
        "COMPLETE",
        "ARCHIVE",
        "UNDO",
        "PROGRESS",
    }:
        raise PermissionRequired(
            "External untrusted content cannot change active intent or lifecycle state.",
            details={"operation": operation},
        )
    if isinstance(after, dict) and after.get("certainty") == "confirmed" and not trusted:
        raise PermissionRequired(
            "Agent or external content cannot create confirmed intent.",
            details={"operation": operation, "source_kind": source_kind},
        )
    if operation == "CONFIRM" and not trusted:
        raise PermissionRequired("Confirmation requires a direct user or explicitly trusted project source.")
    if isinstance(before, dict) and before.get("certainty") == "confirmed" and operation in {"MODIFY", "REVOKE", "RESOLVE"} and not trusted:
        raise PermissionRequired(
            "Changing confirmed intent requires a direct user or explicitly trusted project source.",
            details={"operation": operation, "item_id": before.get("id")},
        )
