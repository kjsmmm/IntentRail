"""Translate host lifecycle payloads into the canonical IntentRail hook contract.

This module deliberately performs no intent interpretation. It binds host sessions,
classifies mechanical tool risk, invokes canonical state operations, and renders the
small output shape expected by each supported host.
"""

import argparse
import json
import os
import re
import sys
from hashlib import sha256
from pathlib import Path

from .bindings import current_turn, find_context_binding, observe_turn
from .checkpoint import create_checkpoint
from .contracts import compact_status, load_reconciled
from .errors import GateBlocked, IntentRailError, StateNotFound, UsageError
from .gates import handle_hook
from .state import StateStore
from .semantics import is_blocked


MAX_HOOK_INPUT_BYTES = 1024 * 1024
MAX_CONTEXT_CHARS = 6000
SUPPORTED_HOSTS = {"codex", "claude-code", "copilot-cli"}

_READ_TOOLS = {
    "read", "view", "grep", "rg", "glob", "find", "search", "websearch",
    "web_search", "webfetch", "web_fetch", "list", "inspect", "status", "diff",
}
_WRITE_TOOLS = {"edit", "write", "create", "apply_patch", "str_replace_editor", "notebookedit"}
_SHELL_TOOLS = {"bash", "shell", "powershell", "shell_command", "exec", "exec_command"}

_HIGH_RISK_PATTERNS = (
    ("destructive_local", re.compile(r"(?i)(\brm\s+[^\n]*-[^\n]*r|\bremove-item\b[^\n]*-recurse|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[^\n]*f|\bformat(?:\.com)?\s+[a-z]:|\bdel\s+/[sq]\b)")),
    ("permission_change", re.compile(r"(?i)(\bchmod\b|\bchown\b|\bicacls\b|\bset-acl\b)")),
    ("release", re.compile(r"(?i)(\bnpm\s+publish\b|\bpypi\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bdeploy\b|\brelease\b)")),
    ("external_write", re.compile(r"(?i)(\bgit\s+push\b|\bgh\s+(?:pr|issue|release)\s+(?:create|merge|close|delete)\b|\bcurl\b[^\n]*(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--request\s*(?:POST|PUT|PATCH|DELETE)))")),
    ("secret_access", re.compile(r"(?i)(\b(?:cat|type|get-content|printenv)\b[^\n]*(?:\.env|credential|secret|token|id_rsa)|\b(?:aws|gcloud|az)\b[^\n]*credential)")),
)


def process_host_hook(host, payload, explicit_event=None, explicit_root=None):
    """Return host-native JSON for one validated hook payload."""
    if host not in SUPPORTED_HOSTS:
        raise UsageError("Unsupported host adapter: {0}".format(host))
    if not isinstance(payload, dict):
        raise UsageError("Hook input must be a JSON object.")
    event = normalize_event(explicit_event or payload.get("hook_event_name") or payload.get("eventName") or payload.get("event"))
    if not event:
        raise UsageError("Hook event name is missing.")
    cwd = explicit_root or payload.get("cwd") or os.getcwd()
    try:
        store = StateStore.discover(explicit_root=explicit_root, start=cwd)
    except StateNotFound:
        return render_output(host, event, {"allow": True, "reason": "intentrail-dormant"})

    try:
        decision = _dispatch(store, host, event, payload)
    except GateBlocked as exc:
        if event == "PreToolUse":
            return render_output(host, event, {"allow": False, "reason": exc.message})
        return render_output(host, event, {"allow": True, "warning": exc.message})
    except (IntentRailError, ValueError, OSError) as exc:
        message = getattr(exc, "message", None) or "IntentRail hook validation failed: {0}".format(type(exc).__name__)
        if event == "PreToolUse":
            return render_output(host, event, {"allow": False, "reason": message})
        return render_output(host, event, {"allow": True, "warning": message})
    return render_output(host, event, decision)


def _dispatch(store, host, event, payload):
    context_id = _first_text(payload, "session_id", "sessionId", "thread_id", "threadId", "context_id")
    if event in {"SessionStart", "UserPromptSubmit", "PostCompact"}:
        canonical = {"context_id": context_id, "contract_id": payload.get("contract_id")}
        try:
            observed = handle_hook(store, host, "session-start" if event != "UserPromptSubmit" else "user-prompt", canonical)
        except StateNotFound:
            return {"allow": True, "reason": "intentrail-dormant"}
        binding = observed.get("binding")
        if binding and event == "UserPromptSubmit":
            turn_id = _turn_id(payload, context_id)
            observe_turn(store, binding["binding_id"], turn_id)
        elif binding and payload.get("source") == "compact":
            previous = current_turn(store, binding["binding_id"], required=False)
            turn_id = (previous or {}).get("turn_or_prompt_id") or _turn_id(payload, context_id)
            observe_turn(store, binding["binding_id"], turn_id)
        return {
            "allow": True,
            "reason": "context-bound",
            "additional_context": _alignment_context(store, binding, event),
            "binding": binding,
        }

    if event == "PreCompact":
        checkpoint = _checkpoint_active(store, "pre-compact")
        return {"allow": True, "reason": "checkpoint-created" if checkpoint else "intentrail-dormant", "checkpoint": checkpoint}

    if event == "SessionEnd":
        checkpoint = _checkpoint_active(store, "session-end")
        return {"allow": True, "reason": "checkpoint-created" if checkpoint else "intentrail-dormant", "checkpoint": checkpoint}

    if event == "Stop":
        return _stop_decision(store)

    if event != "PreToolUse":
        return {"allow": True, "reason": "event-observed"}

    tool_name = _first_text(payload, "tool_name", "toolName") or "unknown"
    tool_input = payload.get("tool_input", payload.get("toolArgs", {}))
    action_class, scope = classify_tool(tool_name, tool_input)
    binding = find_context_binding(store, host, context_id, required=False)
    turn = current_turn(store, binding["binding_id"], required=False) if binding else None
    canonical = {
        "action_class": action_class,
        "scope": scope,
        "targets": extract_targets(tool_input),
        "binding_id": (binding or {}).get("binding_id"),
        "turn_or_prompt_id": (turn or {}).get("turn_or_prompt_id"),
        "lease_id": payload.get("lease_id"),
        "ticket_id": payload.get("ticket_id"),
    }
    decision = handle_hook(store, host, "pre-tool-use", canonical)
    decision.update({"action_class": action_class, "scope": scope})
    return decision


def classify_tool(tool_name, tool_input):
    """Conservatively classify mechanics without interpreting user intent."""
    normalized = str(tool_name).replace("-", "_").lower()
    compact = normalized.replace("_", "")
    if normalized in _READ_TOOLS or compact in _READ_TOOLS:
        return "read", "read-only"
    if normalized in _WRITE_TOOLS or compact in _WRITE_TOOLS:
        return "other_local_write", "project-files"
    if normalized in _SHELL_TOOLS or compact in _SHELL_TOOLS or compact in {"bash", "powershell"}:
        command = _command_text(tool_input)
        for action_class, pattern in _HIGH_RISK_PATTERNS:
            if pattern.search(command):
                return action_class, "project-files" if action_class != "external_write" else "external-systems"
        return "other_local_write", "project-files"
    if normalized.startswith("mcp__") or normalized.startswith("mcp_"):
        if re.search(r"(?i)(?:^|_)(get|read|list|search|find|query|inspect)(?:_|$)", normalized):
            return "read", "read-only"
        if re.search(r"(?i)(?:^|_)(delete|remove|publish|deploy|send|merge|close)(?:_|$)", normalized):
            return "external_write", "external-systems"
        return "other_local_write", "external-systems"
    return "other_local_write", "project-files"


def extract_targets(tool_input):
    """Extract bounded target identifiers, never arbitrary values or secret material."""
    targets = []
    interesting = {"path", "file", "file_path", "filepath", "directory", "url", "uri", "repo", "repository", "command"}

    def visit(value, depth=0):
        if depth > 4 or len(targets) >= 16:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered in interesting and isinstance(item, (str, int, float)):
                    text = str(item)[:512]
                    if lowered == "command":
                        text = _redact_command(text)
                    targets.append("{0}:{1}".format(lowered, text))
                elif isinstance(item, (dict, list)):
                    visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value[:16]:
                visit(item, depth + 1)

    visit(tool_input)
    return list(dict.fromkeys(targets)) or ["tool-input:unspecified"]


def render_output(host, event, decision):
    """Render only documented host fields; an allow is normally silent."""
    if event == "PreToolUse":
        if decision.get("allow", True):
            return {}
        reason = str(decision.get("reason") or "IntentRail Gate blocked this action.")[:2000]
        if host == "copilot-cli":
            return {"permissionDecision": "deny", "permissionDecisionReason": reason}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    context = decision.get("additional_context")
    if context and event in {"SessionStart", "UserPromptSubmit", "PostCompact"}:
        if host == "copilot-cli":
            return {"additionalContext": context[:MAX_CONTEXT_CHARS]}
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context[:MAX_CONTEXT_CHARS]}}
    warning = decision.get("warning")
    return {"systemMessage": str(warning)[:2000]} if warning else {}


def normalize_event(value):
    if not value:
        return None
    key = re.sub(r"[^a-z]", "", str(value).lower())
    return {
        "sessionstart": "SessionStart",
        "sessionend": "SessionEnd",
        "userpromptsubmit": "UserPromptSubmit",
        "userpromptsubmitted": "UserPromptSubmit",
        "pretooluse": "PreToolUse",
        "precompact": "PreCompact",
        "postcompact": "PostCompact",
        "stop": "Stop",
    }.get(key, str(value))


def _alignment_context(store, binding, event):
    if not binding:
        return "IntentRail is dormant for this project; do not create state until a material multi-turn intent update needs persistence."
    contract, events = load_reconciled(store, binding["contract_id"])
    status = compact_status(contract, events)
    turn = current_turn(store, binding["binding_id"], required=False)
    payload = {
        "event": event,
        "needs_reconciliation": event == "UserPromptSubmit",
        "binding_id": binding["binding_id"],
        "turn_or_prompt_id": (turn or {}).get("turn_or_prompt_id"),
        "contract": status,
    }
    return (
        "IntentRail host context (trusted local state). Treat events as the source of truth and reconcile the latest user message before acting. "
        "Before side effects, run the IntentRail Gate and issue a lease for this binding and turn; "
        "high-risk actions also require a matching one-shot ticket. State: "
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _checkpoint_active(store, purpose):
    try:
        contract_id = store.resolve_contract_id()
        contract, _ = load_reconciled(store, contract_id)
    except StateNotFound:
        return None
    if contract.get("status") in {"archived", "completed"}:
        return None
    return create_checkpoint(store, contract_id, purpose)["checkpoint"]


def _stop_decision(store):
    try:
        contract_id = store.resolve_contract_id()
        contract, _ = load_reconciled(store, contract_id)
    except StateNotFound:
        return {"allow": True, "reason": "intentrail-dormant"}
    unresolved = [item for item in _semantic_items(contract) if is_blocked(item)]
    if unresolved:
        return {"allow": True, "warning": "IntentRail: unresolved intent conflict remains; do not claim completion."}
    return {"allow": True, "reason": "stop-observed"}


def _semantic_items(contract):
    if isinstance(contract.get("objective"), dict):
        yield contract["objective"]
    for field in ["deliverables", "constraints", "acceptance_criteria", "decisions", "questions", "assumptions", "completed_work"]:
        yield from contract.get(field, [])


def _turn_id(payload, context_id):
    supplied = _first_text(payload, "turn_id", "turnId", "prompt_id", "promptId")
    if supplied:
        return supplied
    material = "|".join([
        context_id or "unknown-session",
        str(payload.get("timestamp") or ""),
        str(payload.get("prompt") or payload.get("initial_prompt") or payload.get("initialPrompt") or "")[:4096],
    ])
    return "turn-" + sha256(material.encode("utf-8")).hexdigest()[:24]


def _command_text(tool_input):
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "script", "powershell", "bash"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value[:32768]
    return ""


def _redact_command(command):
    """Keep a matchable command target while removing common inline secret forms."""
    value = str(command)
    value = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s'\"]+", r"\1<redacted>", value)
    value = re.sub(r"(?i)(?:api[_-]?key|token|password|secret)\s*=\s*([^\s;&|]+)", lambda match: match.group(0).split("=", 1)[0] + "=<redacted>", value)
    value = re.sub(r"\b[A-Za-z0-9_\-]{48,}\b", "<redacted-long-token>", value)
    return value[:512]


def _first_text(payload, *keys):
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def read_hook_input(stream):
    raw = stream.buffer.read(MAX_HOOK_INPUT_BYTES + 1) if hasattr(stream, "buffer") else stream.read(MAX_HOOK_INPUT_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise UsageError("Hook input exceeds the 1 MiB safety limit.")
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UsageError("Hook input is not valid UTF-8 JSON.")
    if not isinstance(value, dict):
        raise UsageError("Hook input must be a JSON object.")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="intentrail-hook")
    parser.add_argument("--host", required=True, choices=sorted(SUPPORTED_HOSTS))
    parser.add_argument("--event")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    try:
        payload = read_hook_input(sys.stdin)
        output = process_host_hook(args.host, payload, args.event, args.root)
    except (IntentRailError, ValueError, OSError) as exc:
        event = normalize_event(args.event) or "PreToolUse"
        message = getattr(exc, "message", None) or "IntentRail hook rejected invalid input."
        output = render_output(args.host, event, {"allow": False, "reason": message}) if event == "PreToolUse" else {"systemMessage": message}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
