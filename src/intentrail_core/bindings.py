"""Non-semantic host context and turn bindings to canonical contracts."""

from pathlib import Path

from .constants import SCHEMA_VERSION
from .errors import StateNotFound, UsageError
from .util import atomic_write_json, new_id, read_json, utc_now


HOSTS = {"codex", "claude-code", "copilot-cli", "generic-agent-skills"}


def bind_context(store, host, payload):
    if host not in HOSTS:
        raise UsageError("Unknown host: {0}".format(host))
    context_id = payload.get("context_id")
    if not isinstance(context_id, str) or not context_id:
        raise UsageError("Context binding requires context_id.")
    contract_id = store.resolve_contract_id(payload.get("contract_id"))
    existing = find_context_binding(store, host, context_id, required=False)
    binding_id = payload.get("binding_id") or (existing or {}).get("binding_id") or new_id()
    path = store.state_root / "bindings" / "{0}.json".format(binding_id)
    now = utc_now()
    if path.exists():
        binding = read_json(path)
        if binding.get("host") != host or binding.get("context_id") != context_id:
            raise UsageError("binding_id is already owned by another host context.")
        binding["contract_id"] = contract_id
        binding["updated_at"] = now
    else:
        binding = {
            "schema_version": SCHEMA_VERSION,
            "binding_id": binding_id,
            "host": host,
            "context_id": context_id,
            "contract_id": contract_id,
            "created_at": now,
            "updated_at": now,
        }
    with store.project_lock():
        atomic_write_json(path, binding)
    return binding


def find_context_binding(store, host, context_id, required=True):
    """Return the newest binding for one host session without changing state."""
    if not context_id:
        if required:
            raise StateNotFound("Host context id is missing.")
        return None
    matches = []
    for path in (store.state_root / "bindings").glob("*.json"):
        binding = read_json(path)
        if binding.get("host") == host and binding.get("context_id") == context_id:
            matches.append(binding)
    if not matches:
        if required:
            raise StateNotFound("No IntentRail binding exists for this host context.")
        return None
    return max(matches, key=lambda item: item.get("updated_at", ""))


def observe_turn(store, binding_id, turn_or_prompt_id):
    """Record the current host turn so an older lease cannot cross turn boundaries."""
    if not isinstance(turn_or_prompt_id, str) or not turn_or_prompt_id:
        raise UsageError("turn_or_prompt_id must be a non-empty string.")
    runtime = store.state_root / "runtime" / binding_id
    runtime.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": SCHEMA_VERSION,
        "binding_id": binding_id,
        "turn_or_prompt_id": turn_or_prompt_id,
        "updated_at": utc_now(),
    }
    atomic_write_json(runtime / "current-turn.json", document)
    return document


def current_turn(store, binding_id, required=True):
    path = Path(store.state_root) / "runtime" / binding_id / "current-turn.json"
    if not path.exists():
        if required:
            raise StateNotFound("No current IntentRail turn is recorded for this binding.")
        return None
    return read_json(path)
