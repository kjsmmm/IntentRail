"""Explain current intent items and Gate action bases without exposing raw prompts."""

from .contracts import find_item, load_reconciled
from .errors import GateBlocked, StateNotFound, UsageError
from .gates import find_ticket, validate_ticket


def explain(store, payload):
    if not isinstance(payload, dict):
        raise UsageError("Explain input must be an object.")
    contract_id = payload.get("contract_id") or store.resolve_contract_id()
    contract, events = load_reconciled(store, contract_id)
    item_id = payload.get("item_id")
    ticket_id = payload.get("ticket_id")
    if item_id:
        item = find_item(contract, item_id)
        if item is None:
            raise StateNotFound("Intent item not found: {0}".format(item_id))
        dependents = []
        for field in ["decisions", "assumptions", "completed_work"]:
            for candidate in contract.get(field, []):
                if item_id in (candidate.get("depends_on") or []):
                    dependents.append(_item_view(candidate))
        related_events = [
            _event_view(event) for event in events
            if event.get("entity_id") == item_id or item_id in ((event.get("after") or {}).get("supersedes", []) if isinstance(event.get("after"), dict) else [])
        ]
        return {"type": "item", "item": _item_view(item), "dependents": dependents, "events": related_events}
    if ticket_id:
        ticket, _ = find_ticket(store, ticket_id)
        valid = True
        reason = "Action basis and parent lease are current."
        try:
            validate_ticket(store, ticket)
        except GateBlocked as exc:
            valid = False
            reason = getattr(exc, "message", str(exc))
        return {"type": "action", "ticket": ticket, "valid": valid, "reason": reason}
    return {
        "type": "contract",
        "contract_id": contract_id,
        "version": contract["version"],
        "event_head_hash": contract["event_head_hash"],
        "stale": [_item_view(item) for item in _items(contract) if item.get("lifecycle") == "stale"],
        "needs_review": [_item_view(item) for item in _items(contract) if item.get("lifecycle") == "needs_review"],
        "conflicts": [_item_view(item) for item in _items(contract) if item.get("lifecycle") == "conflicted"],
    }


def _items(contract):
    if isinstance(contract.get("objective"), dict):
        yield contract["objective"]
    for field in ["deliverables", "constraints", "acceptance_criteria", "decisions", "questions", "assumptions", "completed_work", "superseded_items"]:
        yield from contract.get(field, [])


def _item_view(item):
    return {key: item.get(key) for key in ["id", "kind", "text", "certainty", "lifecycle", "scope", "source_ref", "supersedes", "depends_on", "invalidated_by"]}


def _event_view(event):
    return {key: event.get(key) for key in ["event_id", "operation", "source_ref", "contract_version_after", "reconciliation_id", "timestamp"]}
