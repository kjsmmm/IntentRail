"""Typed failures mapped to the stable CLI exit-code contract."""

from . import constants


class IntentRailError(Exception):
    exit_code = constants.EXIT_OPERATION_FAILED
    code = "OPERATION_FAILED"

    def __init__(self, message, details=None, recovery_actions=None):
        super().__init__(message)
        self.message = message
        self.details = details
        self.recovery_actions = recovery_actions or []


class UsageError(IntentRailError):
    exit_code = constants.EXIT_USAGE_ERROR
    code = "USAGE_ERROR"


class StateNotFound(IntentRailError):
    exit_code = constants.EXIT_STATE_NOT_FOUND
    code = "STATE_NOT_FOUND"


class RecoveryRequired(IntentRailError):
    exit_code = constants.EXIT_RECOVERY_REQUIRED
    code = "RECOVERY_REQUIRED"


class IntentConflict(IntentRailError):
    exit_code = constants.EXIT_INTENT_CONFLICT
    code = "INTENT_CONFLICT"


class StaleVersion(IntentRailError):
    exit_code = constants.EXIT_STALE_VERSION
    code = "STALE_VERSION"


class PermissionRequired(IntentRailError):
    exit_code = constants.EXIT_PERMISSION_REQUIRED
    code = "PERMISSION_REQUIRED"


class UnsupportedCapability(IntentRailError):
    exit_code = constants.EXIT_UNSUPPORTED_CAPABILITY
    code = "UNSUPPORTED_CAPABILITY"


class MigrationRequired(IntentRailError):
    exit_code = constants.EXIT_MIGRATION_REQUIRED
    code = "MIGRATION_REQUIRED"


class GateBlocked(IntentRailError):
    exit_code = constants.EXIT_GATE_BLOCKED
    code = "GATE_BLOCKED"


class SensitiveContent(IntentRailError):
    exit_code = constants.EXIT_SENSITIVE_CONTENT
    code = "SENSITIVE_CONTENT"


class InstallationError(IntentRailError):
    code = "INSTALLATION_ERROR"
