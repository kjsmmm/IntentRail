"""Stable public constants shared by every host adapter."""

PRODUCT_VERSION = "0.5.0"
SCHEMA_VERSION = "2.0.0"
SCHEMA_MAJOR = 2

EXIT_OK = 0
EXIT_OPERATION_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_STATE_NOT_FOUND = 3
EXIT_RECOVERY_REQUIRED = 4
EXIT_INTENT_CONFLICT = 5
EXIT_STALE_VERSION = 6
EXIT_PERMISSION_REQUIRED = 7
EXIT_UNSUPPORTED_CAPABILITY = 8
EXIT_MIGRATION_REQUIRED = 9
EXIT_GATE_BLOCKED = 10
EXIT_SENSITIVE_CONTENT = 11
EXIT_INTERNAL_ERROR = 12

CERTAINTIES = {"confirmed", "inferred", "assumed"}
LIFECYCLES = {"active", "conflicted", "superseded", "revoked", "stale", "needs_review"}
# Compatibility projection for v1 clients. Canonical logic uses certainty + lifecycle.
INTENT_STATES = CERTAINTIES | (LIFECYCLES - {"active"})
ITEM_KINDS = {
    "objective",
    "deliverable",
    "constraint",
    "acceptance_criterion",
    "decision",
    "question",
    "assumption",
    "completed_work",
    "preference",
    "prohibition",
}
OPERATIONS = {
    "ADD",
    "MODIFY",
    "REVOKE",
    "CONFIRM",
    "CONFLICT",
    "DEFER",
    "RESOLVE",
    "PROGRESS",
    "CHECKPOINT",
    "UNDO",
    "PAUSE",
    "RESUME",
    "VERIFY_PASS",
    "VERIFY_FAIL",
    "COMPLETE",
    "ARCHIVE",
    "MIGRATE",
}
CONTRACT_STATUSES = {"active", "paused", "completed", "archived", "recovery-required"}

KIND_TO_FIELD = {
    "objective": "objective",
    "deliverable": "deliverables",
    "constraint": "constraints",
    "acceptance_criterion": "acceptance_criteria",
    "decision": "decisions",
    "question": "questions",
    "assumption": "assumptions",
    "completed_work": "completed_work",
    "preference": "constraints",
    "prohibition": "constraints",
}

READ_ONLY_HOOK_CLASSES = {"read", "search", "inspect", "list", "status", "diff", "validate"}
HIGH_RISK_ACTIONS = {
    "destructive_local",
    "external_write",
    "permission_change",
    "secret_access",
    "release",
    "other_high_risk",
}

DEFAULT_CONFIG = {
    "schema_version": SCHEMA_VERSION,
    "interaction_mode": "balanced",
    "activation_mode": "automatic",
    "persistence_policy": "delayed-auto",
    "sharing_policy": "local-only",
    "paused": False,
    "turn_lease_ttl_seconds": 600,
    "action_ticket_ttl_seconds": 300,
    "precedent_stale_days": 180,
    "checkpoint_policy": {
        "before_compaction": True,
        "before_handoff": True,
        "before_high_risk_action": True,
    },
    "host_capabilities": {},
}
