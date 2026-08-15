"""Record Agent judgments against the latest acceptance criteria."""

from .contracts import apply_change, compact_status, load_reconciled
from .errors import StaleVersion, UsageError
from .semantics import is_active


def verify_result(store, payload):
    if not isinstance(payload, dict):
        raise UsageError("Verification input must be a JSON object.")
    contract_id = payload.get("contract_id") or store.resolve_contract_id()
    contract, _ = load_reconciled(store, contract_id)
    if payload.get("expected_version") != contract["version"]:
        raise StaleVersion("Verification must target the latest contract version.")
    results = payload.get("criteria")
    if not isinstance(results, list):
        raise UsageError("Verification requires a criteria array.")
    blocked_criteria = [
        item["id"] for item in contract.get("acceptance_criteria", [])
        if item.get("lifecycle") in {"conflicted", "stale", "needs_review"}
    ]
    expected_ids = {item["id"] for item in contract.get("acceptance_criteria", []) if is_active(item)}
    reported = {item.get("criterion_id") for item in results if isinstance(item, dict)}
    missing = sorted(expected_ids - reported)
    invalid = [item for item in results if not isinstance(item, dict) or item.get("status") not in {"pass", "fail", "not_in_scope"}]
    passed = not blocked_criteria and not missing and not invalid and all(isinstance(item, dict) and item.get("status") == "pass" for item in results)
    operation = "VERIFY_PASS" if passed else "VERIFY_FAIL"
    result = apply_change(
        store,
        contract_id,
        {
            "operation": operation,
            "entity_kind": "contract",
            "entity_id": contract_id,
            "after": {"criteria": results, "summary": payload.get("summary")},
            "source": payload.get("source") or {"kind": "agent"},
            "source_ref": payload.get("source_ref") or "verify-command",
            "expected_version": contract["version"],
            "idempotency_key": payload.get("idempotency_key") or "verify:{0}:{1}".format(contract_id, contract["version"]),
            "reversible": False,
        },
    )
    contract = result["contract"]
    completion = None
    if passed and payload.get("complete") is True:
        completion = apply_change(
            store,
            contract_id,
            {
                "operation": "COMPLETE",
                "entity_kind": "contract",
                "entity_id": contract_id,
                "after": {"verification_event": result["event"]["event_id"]},
                "source": {"kind": "agent"},
                "source_ref": "verified-completion",
                "expected_version": contract["version"],
                "idempotency_key": "complete:{0}:{1}".format(contract_id, contract["version"]),
                "reversible": False,
            },
        )
        contract = completion["contract"]
    return {
        "passed": passed,
        "missing_criteria": missing,
        "invalid_results": invalid,
        "blocked_criteria": blocked_criteria,
        "contract": compact_status(contract),
        "verification_event": result["event"],
        "completion_event": completion["event"] if completion else None,
    }
