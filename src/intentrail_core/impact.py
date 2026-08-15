"""Conservative impact propagation over explicit dependencies and scopes."""

from .constants import KIND_TO_FIELD
from .semantics import is_active, set_lifecycle


DERIVED_KINDS = {"decision", "assumption", "completed_work"}


def propagate_impact(contract, changed_ids, changed_scopes, event_id, version):
    """Invalidate explicit dependents; flag unlinked same-scope derived items for review."""
    stale = []
    needs_review = []
    changed_ids = set(changed_ids or [])
    changed_scopes = {scope for scope in (changed_scopes or []) if scope}
    for item in _active_items(contract):
        if item.get("id") in changed_ids or item.get("kind") not in DERIVED_KINDS:
            continue
        dependencies = set(item.get("depends_on") or [])
        if dependencies & changed_ids:
            set_lifecycle(item, "stale")
            item["invalidated_by"] = list(dict.fromkeys((item.get("invalidated_by") or []) + [event_id]))
            item["updated_version"] = version
            stale.append(item["id"])
        elif not dependencies and item.get("scope") in changed_scopes:
            set_lifecycle(item, "needs_review")
            item["invalidated_by"] = list(dict.fromkeys((item.get("invalidated_by") or []) + [event_id]))
            item["updated_version"] = version
            needs_review.append(item["id"])
    return {"stale": stale, "needs_review": needs_review}


def _active_items(contract):
    objective = contract.get("objective")
    if is_active(objective):
        yield objective
    for field in set(KIND_TO_FIELD.values()):
        if field == "objective":
            continue
        for item in contract.get(field, []):
            if is_active(item):
                yield item
