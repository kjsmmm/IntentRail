"""Atomic application of one Agent-produced semantic reconciliation batch."""

import copy

from .contracts import (
    _enforce_change_trust,
    _infer_kind,
    _prepare_after,
    apply_event_to_contract,
    find_item,
    load_reconciled,
    normalize_source,
)
from .errors import StaleVersion, UsageError
from .events import append_events_atomic, build_event
from .semantics import is_active
from .util import atomic_write_json, new_id


SEMANTIC_OPERATIONS = {"ADD", "MODIFY", "REVOKE", "CONFIRM", "CONFLICT", "DEFER", "RESOLVE"}


def apply_reconciliation(store, payload):
    if not isinstance(payload, dict):
        raise UsageError("Reconciliation input must be a JSON object.")
    contract_id = payload.get("contract_id") or store.resolve_contract_id()
    batch_key = payload.get("idempotency_key")
    changes = payload.get("changes")
    if not isinstance(batch_key, str) or not batch_key:
        raise UsageError("Reconciliation requires idempotency_key.")
    if not isinstance(changes, list) or not changes:
        raise UsageError("Reconciliation requires a non-empty changes array.")
    source = normalize_source(payload.get("source") or {"kind": "agent"})
    source_ref = payload.get("source_ref") or "reconciliation-input"
    reconciliation_id = payload.get("reconciliation_id") or new_id()

    with store.contract_lock(contract_id):
        contract, existing = load_reconciled(store, contract_id)
        committed = [event for event in existing if event.get("idempotency_key", "").startswith(batch_key + ":")]
        if committed:
            return {"contract": contract, "events": committed, "duplicate": True, "reconciliation_id": committed[0].get("reconciliation_id")}
        expected = payload.get("base_version")
        if expected is None or int(expected) != contract["version"]:
            raise StaleVersion(
                "Reconciliation base version is stale.",
                details={"expected": expected, "actual": contract["version"]},
            )

        updated = copy.deepcopy(contract)
        events = []
        previous_hash = existing[-1]["event_hash"]
        for index, change in enumerate(changes, 1):
            if not isinstance(change, dict):
                raise UsageError("Each reconciliation change must be an object.")
            operation = str(change.get("operation") or "").upper()
            if operation not in SEMANTIC_OPERATIONS:
                raise UsageError("Unsupported reconciliation operation: {0}".format(operation))
            entity_kind = change.get("entity_kind") or _infer_kind(change.get("after"))
            entity_id = change.get("entity_id") or change.get("target_id")
            before = copy.deepcopy(find_item(updated, entity_id)) if entity_id else None
            if operation in {"MODIFY", "REVOKE", "CONFIRM", "RESOLVE"} and before is None:
                raise UsageError("{0} requires an existing target_id.".format(operation))
            if before is not None and not is_active(before) and operation != "RESOLVE":
                raise UsageError("Reconciliation targets an inactive item: {0}".format(entity_id))
            raw_after = change.get("after")
            if operation == "CONFIRM" and raw_after is None:
                raw_after = before
            version_after = updated["version"] + 1
            after = _prepare_after(
                raw_after,
                operation,
                entity_kind,
                entity_id,
                version_after,
                source,
                source_ref,
                change.get("intent_state"),
            )
            _enforce_change_trust(operation, source, before, after)
            event = build_event(
                contract_id=contract_id,
                sequence=len(existing) + index,
                operation=operation,
                entity_kind=entity_kind,
                entity_id=entity_id or (after.get("id") if isinstance(after, dict) else None),
                before=before,
                after=after,
                source=source,
                source_ref=source_ref,
                intent_state=(after.get("state") if isinstance(after, dict) else change.get("intent_state")),
                version_before=updated["version"],
                version_after=version_after,
                reversible=True,
                inverse_of=None,
                idempotency_key="{0}:{1}".format(batch_key, index),
                previous_hash=previous_hash,
                reconciliation_id=reconciliation_id,
                reconciliation_index=index,
                reconciliation_size=len(changes),
            )
            updated = apply_event_to_contract(updated, event)
            events.append(event)
            previous_hash = event["event_hash"]

        append_events_atomic(store.events_path(contract_id), events)
        atomic_write_json(store.contract_path(contract_id), updated)
    store.upsert_contract_index(updated)
    impact = _impact_for_events(updated, {event["event_id"] for event in events})
    return {
        "contract": updated,
        "events": events,
        "duplicate": False,
        "reconciliation_id": reconciliation_id,
        "impact": impact,
        "unresolved": list(payload.get("unresolved") or []),
    }


def _impact_for_events(contract, event_ids):
    stale = []
    needs_review = []
    fields = ["decisions", "assumptions", "completed_work"]
    for field in fields:
        for item in contract.get(field, []):
            if not event_ids.intersection(item.get("invalidated_by") or []):
                continue
            if item.get("lifecycle") == "stale":
                stale.append(item["id"])
            elif item.get("lifecycle") == "needs_review":
                needs_review.append(item["id"])
    return {"stale": stale, "needs_review": needs_review}
