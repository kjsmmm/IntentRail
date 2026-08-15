"""Deterministic verification of Agent-issued Gate leases and action tickets."""

import copy
from datetime import timedelta
from pathlib import Path

from .bindings import bind_context
from .constants import HIGH_RISK_ACTIONS, READ_ONLY_HOOK_CLASSES, SCHEMA_VERSION
from .contracts import load_reconciled
from .errors import GateBlocked, StateNotFound, UsageError
from .locks import FileLock
from .util import atomic_write_json, new_id, parse_time, read_json, sha256_value, utc_now
from .semantics import is_active


def issue_lease(store, payload):
    _require_object(payload, "Lease input")
    if payload.get("decision") != "PASS":
        raise GateBlocked("A lease can only be issued after an Agent Gate decision of PASS.")
    contract_id = payload.get("contract_id") or store.resolve_contract_id()
    contract, _ = load_reconciled(store, contract_id)
    config = store.load_config()
    if contract["status"] in {"paused", "recovery-required"}:
        raise GateBlocked("Cannot issue a lease while the contract is {0}.".format(contract["status"]))
    expected_version = payload.get("contract_version")
    if expected_version != contract["version"]:
        raise GateBlocked("Lease contract version is stale.")
    if payload.get("event_head_hash") != contract["event_head_hash"]:
        raise GateBlocked("Lease event head is stale.")
    binding_id = payload.get("binding_id")
    turn_id = payload.get("turn_or_prompt_id")
    scopes = payload.get("allowed_scopes")
    if not isinstance(binding_id, str) or not isinstance(turn_id, str) or not isinstance(scopes, list) or not scopes:
        raise UsageError("Lease requires binding_id, turn_or_prompt_id, and non-empty allowed_scopes.")
    intent_refs = list(payload.get("intent_refs") or [])
    decision_refs = list(payload.get("decision_refs") or [])
    if intent_refs or decision_refs:
        _validate_action_basis(contract, intent_refs, decision_refs)
    issued = parse_time(utc_now())
    expires = issued + timedelta(seconds=int(config.get("turn_lease_ttl_seconds", 600)))
    lease = {
        "schema_version": SCHEMA_VERSION,
        "lease_id": new_id(),
        "decision": "PASS",
        "contract_id": contract_id,
        "contract_version": contract["version"],
        "event_head_hash": contract["event_head_hash"],
        "binding_id": binding_id,
        "turn_or_prompt_id": turn_id,
        "allowed_scopes": list(dict.fromkeys(scopes)),
        "action_summary": payload.get("action_summary"),
        "intent_refs": list(dict.fromkeys(intent_refs)),
        "decision_refs": list(dict.fromkeys(decision_refs)),
        "affected_scopes": list(dict.fromkeys(payload.get("affected_scopes") or [])),
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }
    lease["lease_hash"] = sha256_value(lease)
    runtime = store.state_root / "runtime" / binding_id
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime / "lease.json", lease)
    return lease


def issue_ticket(store, payload):
    _require_object(payload, "Ticket input")
    try:
        lease = find_lease(store, payload.get("lease_id"), payload.get("binding_id"))
    except StateNotFound:
        raise GateBlocked("The parent Gate lease is missing or no longer active.")
    validate_lease(store, lease)
    action_class = payload.get("action_class")
    if action_class not in HIGH_RISK_ACTIONS:
        raise UsageError("Unsupported high-risk action_class: {0}".format(action_class))
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise UsageError("Action ticket requires non-empty targets.")
    summary = payload.get("action_summary")
    intent_refs = payload.get("intent_refs")
    decision_refs = list(payload.get("decision_refs") or [])
    affected_scopes = list(payload.get("affected_scopes") or [])
    if not isinstance(summary, str) or not summary.strip():
        raise UsageError("High-risk action tickets require action_summary.")
    if not isinstance(intent_refs, list) or not intent_refs:
        raise UsageError("High-risk action tickets require non-empty intent_refs.")
    contract, _ = load_reconciled(store, lease["contract_id"])
    _validate_action_basis(contract, intent_refs, decision_refs)
    fingerprint = payload.get("target_fingerprint") or fingerprint_targets(targets)
    issued = parse_time(utc_now())
    ttl = int(store.load_config().get("action_ticket_ttl_seconds", 300))
    expires = issued + timedelta(seconds=ttl)
    ticket = {
        "schema_version": SCHEMA_VERSION,
        "ticket_id": new_id(),
        "lease_id": lease["lease_id"],
        "decision": "PASS",
        "contract_id": lease["contract_id"],
        "contract_version": lease["contract_version"],
        "binding_id": lease["binding_id"],
        "action_class": action_class,
        "action_summary": summary,
        "intent_refs": list(dict.fromkeys(intent_refs)),
        "decision_refs": list(dict.fromkeys(decision_refs)),
        "affected_scopes": list(dict.fromkeys(affected_scopes)),
        "targets": list(targets),
        "target_fingerprint": fingerprint,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "consumed_at": None,
    }
    ticket["ticket_hash"] = sha256_value(ticket)
    path = store.state_root / "runtime" / lease["binding_id"] / "tickets" / "{0}.json".format(ticket["ticket_id"])
    atomic_write_json(path, ticket)
    return ticket


def consume_ticket(store, ticket_id, expected_targets=None):
    ticket, path = find_ticket(store, ticket_id)
    runtime_lock = path.parent.parent / ".lock"
    with FileLock(runtime_lock):
        ticket = read_json(path)
        validate_ticket(store, ticket, expected_targets=expected_targets)
        unsigned = dict(ticket)
        unsigned.pop("ticket_hash", None)
        unsigned["consumed_at"] = utc_now()
        unsigned["ticket_hash"] = sha256_value(unsigned)
        atomic_write_json(path, unsigned)
    return unsigned


def validate_lease(store, lease):
    _verify_signed(lease, "lease_hash", "lease")
    if lease.get("decision") != "PASS":
        raise GateBlocked("Lease is not PASS.")
    if parse_time(lease["expires_at"]) <= parse_time(utc_now()):
        raise GateBlocked("Lease has expired.")
    contract, _ = load_reconciled(store, lease["contract_id"])
    if contract["version"] != lease.get("contract_version") or contract["event_head_hash"] != lease.get("event_head_hash"):
        raise GateBlocked("Lease is stale after a contract update.")
    return lease


def validate_ticket(store, ticket, expected_targets=None):
    _verify_signed(ticket, "ticket_hash", "ticket")
    if ticket.get("decision") != "PASS":
        raise GateBlocked("Ticket is not PASS.")
    if ticket.get("consumed_at") is not None:
        raise GateBlocked("Ticket has already been consumed.")
    if parse_time(ticket["expires_at"]) <= parse_time(utc_now()):
        raise GateBlocked("Ticket has expired.")
    try:
        lease = find_lease(store, ticket["lease_id"], ticket["binding_id"])
    except StateNotFound:
        raise GateBlocked("The ticket's parent lease is missing or no longer active.")
    validate_lease(store, lease)
    contract, _ = load_reconciled(store, ticket["contract_id"])
    _validate_action_basis(contract, ticket.get("intent_refs") or [], ticket.get("decision_refs") or [])
    if expected_targets is not None and fingerprint_targets(expected_targets) != ticket.get("target_fingerprint"):
        raise GateBlocked("Ticket targets do not match the requested action.")
    return ticket


def handle_hook(store, host, event_name, payload):
    _require_object(payload, "Hook event")
    if host not in {"codex", "claude-code", "copilot-cli", "generic-agent-skills"}:
        raise UsageError("Unknown host: {0}".format(host))
    normalized = event_name.lower().replace("_", "-")
    if normalized in {"prompt", "user-prompt", "session-start", "session-end", "stop"}:
        binding = None
        if normalized in {"prompt", "user-prompt", "session-start"} and payload.get("context_id"):
            binding = bind_context(store, host, payload)
        return {"allow": True, "host": host, "event": normalized, "action": "observe", "binding": binding}
    if normalized in {"compact", "pre-compact"}:
        return {"allow": True, "host": host, "event": normalized, "action": "checkpoint-required"}
    if normalized not in {"pre-tool-use", "pretooluse"}:
        raise UsageError("Unsupported hook event: {0}".format(event_name))
    action_class = payload.get("action_class") or "other_local_write"
    if action_class in READ_ONLY_HOOK_CLASSES:
        return {"allow": True, "reason": "read-only"}
    config = store.load_config()
    contract_id = payload.get("contract_id")
    try:
        contract_id = store.resolve_contract_id(contract_id)
        contract, _ = load_reconciled(store, contract_id)
    except StateNotFound:
        contract = None
    if contract is None:
        return {"allow": True, "reason": "intentrail-dormant"}
    if config.get("paused") or (contract and contract.get("status") == "paused"):
        return {"allow": True, "reason": "intentrail-paused"}
    if action_class in HIGH_RISK_ACTIONS:
        ticket_id = payload.get("ticket_id")
        if not ticket_id:
            ticket_id = find_matching_ticket(
                store,
                payload.get("binding_id"),
                action_class,
                payload.get("targets") or [],
            ).get("ticket_id")
        try:
            ticket = consume_ticket(store, ticket_id, expected_targets=payload.get("targets") or [])
        except StateNotFound:
            raise GateBlocked("High-risk action ticket is missing.")
        return {"allow": True, "reason": "ticket-consumed", "ticket_id": ticket["ticket_id"]}
    try:
        lease = find_lease(store, payload.get("lease_id"), payload.get("binding_id"))
    except StateNotFound:
        raise GateBlocked("A valid Gate lease is required for this action.")
    validate_lease(store, lease)
    requested_turn = payload.get("turn_or_prompt_id")
    if requested_turn and requested_turn != lease.get("turn_or_prompt_id"):
        raise GateBlocked("Gate lease belongs to an earlier host turn.")
    requested_scope = payload.get("scope")
    if requested_scope and requested_scope not in lease.get("allowed_scopes", []):
        raise GateBlocked("Requested action is outside the lease scope.")
    return {"allow": True, "reason": "lease-valid", "lease_id": lease["lease_id"]}


def find_lease(store, lease_id=None, binding_id=None):
    if binding_id:
        candidates = [store.state_root / "runtime" / binding_id / "lease.json"]
    else:
        candidates = list((store.state_root / "runtime").glob("*/lease.json"))
    matches = []
    for path in candidates:
        if not path.exists():
            continue
        lease = read_json(path)
        if lease_id is None or lease.get("lease_id") == lease_id:
            matches.append(lease)
    if len(matches) != 1:
        raise StateNotFound("Gate lease not found or ambiguous.")
    return matches[0]


def find_ticket(store, ticket_id):
    candidates = list((store.state_root / "runtime").glob("*/tickets/{0}.json".format(ticket_id)))
    if len(candidates) != 1:
        raise StateNotFound("Action ticket not found: {0}".format(ticket_id))
    return read_json(candidates[0]), candidates[0]


def find_matching_ticket(store, binding_id, action_class, targets):
    """Find one exact, unconsumed ticket when a host cannot carry ticket metadata."""
    if not binding_id:
        raise GateBlocked("High-risk action requires a bound host context.")
    expected = fingerprint_targets(targets)
    matches = []
    for path in (store.state_root / "runtime" / binding_id / "tickets").glob("*.json"):
        ticket = read_json(path)
        if ticket.get("action_class") != action_class or ticket.get("target_fingerprint") != expected:
            continue
        try:
            validate_ticket(store, ticket, expected_targets=targets)
        except GateBlocked:
            continue
        matches.append(ticket)
    if len(matches) != 1:
        raise GateBlocked("High-risk action requires one exact, unconsumed action ticket.")
    return matches[0]


def fingerprint_targets(targets):
    return sha256_value({"targets": list(targets)})


def _verify_signed(document, hash_field, label):
    claimed = document.get(hash_field) if isinstance(document, dict) else None
    unsigned = dict(document) if isinstance(document, dict) else {}
    unsigned.pop(hash_field, None)
    if claimed != sha256_value(unsigned):
        raise GateBlocked("{0} integrity check failed.".format(label.capitalize()))


def _require_object(value, label):
    if not isinstance(value, dict):
        raise UsageError("{0} must be a JSON object.".format(label))


def _validate_action_basis(contract, intent_refs, decision_refs):
    if not intent_refs:
        raise GateBlocked("Action basis has no active intent references.")
    active = {}
    for field in ["objective", "deliverables", "constraints", "acceptance_criteria", "decisions", "questions", "assumptions", "completed_work"]:
        values = [contract.get(field)] if field == "objective" else contract.get(field, [])
        for item in values:
            if isinstance(item, dict):
                active[item.get("id")] = item
    for item_id in intent_refs:
        item = active.get(item_id)
        if (
            item is None
            or not is_active(item)
            or item.get("kind") not in {"objective", "deliverable", "constraint", "acceptance_criterion", "preference", "prohibition"}
            or item.get("certainty") != "confirmed"
            or item.get("source", {}).get("kind") not in {"user", "trusted_project_source"}
        ):
            raise GateBlocked("Action basis references inactive or non-intent item: {0}".format(item_id))
    for item_id in decision_refs:
        item = active.get(item_id)
        if item is None or not is_active(item) or item.get("kind") != "decision":
            raise GateBlocked("Action basis references an inactive decision: {0}".format(item_id))
