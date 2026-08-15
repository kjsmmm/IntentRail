"""Project-local, user-confirmed precedents with explicit revocation."""

from datetime import timedelta

from .errors import StateNotFound, UsageError
from .util import atomic_write_json, new_id, parse_time, read_json, utc_now


def list_precedents(store):
    with store.project_lock():
        document = read_json(store.precedents_path)
        stale_days = int(store.load_config().get("precedent_stale_days", 180))
        now = parse_time(utc_now())
        changed = False
        for item in document.get("items", []):
            last = item.get("last_used_at") or item.get("confirmed_at")
            if item.get("status") == "active" and last and now - parse_time(last) >= timedelta(days=stale_days):
                item["status"] = "stale"
                changed = True
        if changed:
            document["updated_at"] = utc_now()
            atomic_write_json(store.precedents_path, document)
    return document


def confirm_precedent(store, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise UsageError("Precedent confirmation requires non-empty text.")
    if payload.get("confirmed_by_user") is not True:
        raise UsageError("Precedents require explicit user confirmation.")
    now = utc_now()
    item = {
        "id": payload.get("id") or new_id(),
        "text": payload["text"].strip(),
        "scope": payload.get("scope") or "project",
        "source_ref": payload.get("source_ref") or "explicit-user-confirmation",
        "confirmed_at": now,
        "last_used_at": now,
        "status": "active",
    }
    with store.project_lock():
        document = read_json(store.precedents_path)
        if any(existing.get("id") == item["id"] for existing in document.get("items", [])):
            raise UsageError("Precedent id already exists.")
        document.setdefault("items", []).append(item)
        document["updated_at"] = now
        atomic_write_json(store.precedents_path, document)
    return item


def revoke_precedent(store, precedent_id):
    with store.project_lock():
        document = read_json(store.precedents_path)
        for item in document.get("items", []):
            if item.get("id") == precedent_id:
                item["status"] = "revoked"
                item["revoked_at"] = utc_now()
                document["updated_at"] = item["revoked_at"]
                atomic_write_json(store.precedents_path, document)
                return item
    raise StateNotFound("Precedent not found: {0}".format(precedent_id))
