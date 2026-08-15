"""Canonical certainty/lifecycle helpers with a v1 state projection."""

from .constants import CERTAINTIES, INTENT_STATES, LIFECYCLES
from .errors import UsageError


def normalize_semantics(item, default_certainty="inferred", default_lifecycle="active"):
    legacy = item.get("state")
    certainty = item.get("certainty")
    lifecycle = item.get("lifecycle")
    if certainty is None and legacy in CERTAINTIES:
        certainty = legacy
    if lifecycle is None and legacy in LIFECYCLES - {"active"}:
        lifecycle = legacy
    certainty = certainty or default_certainty
    lifecycle = lifecycle or default_lifecycle
    if certainty not in CERTAINTIES:
        raise UsageError("Unsupported intent certainty: {0}".format(certainty))
    if lifecycle not in LIFECYCLES:
        raise UsageError("Unsupported intent lifecycle: {0}".format(lifecycle))
    item["certainty"] = certainty
    item["lifecycle"] = lifecycle
    item["state"] = legacy_state(item)
    return item


def legacy_state(item):
    lifecycle = item.get("lifecycle", "active")
    return item.get("certainty", "inferred") if lifecycle == "active" else lifecycle


def set_certainty(item, certainty):
    if certainty not in CERTAINTIES:
        raise UsageError("Unsupported intent certainty: {0}".format(certainty))
    item["certainty"] = certainty
    item["state"] = legacy_state(item)
    return item


def set_lifecycle(item, lifecycle):
    if lifecycle not in LIFECYCLES:
        raise UsageError("Unsupported intent lifecycle: {0}".format(lifecycle))
    item["lifecycle"] = lifecycle
    item["state"] = legacy_state(item)
    return item


def is_active(item):
    return isinstance(item, dict) and item.get("lifecycle", "active") == "active"


def is_blocked(item):
    return isinstance(item, dict) and item.get("lifecycle") in {"conflicted", "stale", "needs_review"}


def validate_legacy_state(item):
    state = item.get("state")
    if state is not None and state not in INTENT_STATES:
        raise UsageError("Unsupported legacy intent state: {0}".format(state))
