"""C1/C2 sanitized handoff export, inspection, and untrusted import."""

import copy
import json
import re
from pathlib import Path

from .constants import SCHEMA_VERSION
from .contracts import create_contract, load_reconciled
from .errors import SensitiveContent, UsageError
from .util import (
    atomic_write_json,
    atomic_write_text,
    new_id,
    read_json,
    sha256_bytes,
    sha256_value,
    utc_now,
    validate_relative_path,
)
from .semantics import is_active, normalize_semantics


_SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("credential-assignment", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*[^\s,;]{8,}")),
]
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|/(?:Users|home|etc|var|opt|root)/)")


def export_handoff(store, contract_id, output, mode="c1", reviewed_by_user=False):
    if mode not in {"c1", "c2"}:
        raise UsageError("handoff mode must be c1 or c2")
    contract, _ = load_reconciled(store, contract_id)
    raw_evidence = contract.get("evidence") or _latest_checkpoint_evidence(store, contract_id)
    evidence = [] if mode == "c2" else _sanitize_evidence(raw_evidence, store.project_root)
    package = {
        "schema_version": SCHEMA_VERSION,
        "handoff_id": new_id(),
        "created_at": utc_now(),
        "source_project_id": contract["project_id"],
        "source_contract_id": contract_id,
        "source_contract_version": contract["version"],
        "source_revision": contract.get("source_revision"),
        "objective": _handoff_item(contract.get("objective")),
        "constraints": [_handoff_item(item) for item in contract.get("constraints", []) if is_active(item)],
        "acceptance_criteria": [_handoff_item(item) for item in contract.get("acceptance_criteria", []) if is_active(item)],
        "decisions": [_handoff_item(item) for item in contract.get("decisions", []) if is_active(item)],
        "completed_work": [item.get("text", "") for item in contract.get("completed_work", [])],
        "open_questions": [_handoff_item(item) for item in contract.get("questions", []) if item.get("lifecycle") not in {"superseded", "revoked"}],
        "next_action": contract.get("next_material_action"),
        "do_not_repeat": list(contract.get("do_not_repeat", [])),
        "evidence": evidence,
        "redaction": {
            "profile": mode.upper(),
            "scanner_version": "1.0.0",
            "excluded_categories": [
                "absolute_paths",
                "credentials",
                "event_log",
                "conversation_transcript",
                "runtime_credentials",
                "file_bodies",
            ],
            "reviewed_by_user": bool(reviewed_by_user),
        },
    }
    findings = scan_sensitive(package)
    if findings:
        raise SensitiveContent(
            "Handoff export was blocked by the sensitive-content scanner.",
            details={"findings": findings},
            recovery_actions=["Remove or redact the flagged content and preview again.", "Use C2 mode to omit evidence locators."],
        )
    package["package_digest"] = sha256_value(package)
    output_path = Path(output).resolve()
    atomic_write_json(output_path, package)
    sidecar = output_path.with_name(output_path.name + ".sha256")
    file_digest = sha256_bytes(output_path.read_bytes())
    atomic_write_text(sidecar, file_digest + "  " + output_path.name + "\n")
    return {
        "handoff": package,
        "output": str(output_path),
        "sidecar": str(sidecar),
        "requires_recipient_validation": True,
    }


def inspect_handoff(path):
    path = Path(path).resolve()
    package = read_json(path)
    _verify_package(package)
    findings = scan_sensitive(package)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_valid = None
    if sidecar.exists():
        sidecar_valid = sidecar.read_text(encoding="utf-8").split()[0] == sha256_bytes(path.read_bytes())
        if not sidecar_valid:
            raise SensitiveContent("Handoff sidecar digest does not match the package.")
    return {
        "valid": not findings,
        "handoff": package,
        "sensitive_findings": findings,
        "sidecar_valid": sidecar_valid,
        "trust": "untrusted-candidate",
    }


def import_handoff(store, path, new_contract=False, merge=False):
    if new_contract == merge:
        raise UsageError("Choose exactly one of --new-contract or --merge.")
    inspected = inspect_handoff(path)
    package = inspected["handoff"]
    if inspected["sensitive_findings"]:
        raise SensitiveContent("Handoff import contains sensitive content.")
    candidate = {
        "objective": _import_item(package.get("objective")),
        "constraints": [_import_item(item) for item in package.get("constraints", [])],
        "acceptance_criteria": [_import_item(item) for item in package.get("acceptance_criteria", [])],
        "decisions": [_import_item(item) for item in package.get("decisions", [])],
        "questions": [_import_item(item) for item in package.get("open_questions", [])],
        "completed_work": [
            {"text": text, "state": "inferred", "source": {"kind": "external_untrusted_content"}, "source_ref": "handoff:{0}".format(package["handoff_id"])}
            for text in package.get("completed_work", [])
        ],
        "next_material_action": package.get("next_action"),
        "source": {"kind": "external_untrusted_content"},
        "source_ref": "handoff:{0}".format(package["handoff_id"]),
        "default_state": "inferred",
        "status": "paused",
        "title": "Imported handoff candidate",
    }
    if merge:
        current_id = store.resolve_contract_id()
        current, _ = load_reconciled(store, current_id)
        return {
            "mode": "candidate-diff",
            "contract_id": current_id,
            "current_version": current["version"],
            "candidate": candidate,
            "written": False,
            "requires_user_resolution": True,
        }
    result = create_contract(store, candidate)
    return {
        "mode": "new-untrusted-contract",
        "contract": result["contract"],
        "written": True,
        "requires_user_confirmation": True,
    }


def scan_sensitive(value):
    findings = []

    def walk(node, location="$"):
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, location + "." + key)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, "{0}[{1}]".format(location, index))
        elif isinstance(node, str):
            if _ABSOLUTE_PATH.search(node):
                findings.append({"location": location, "category": "absolute-path"})
            for category, pattern in _SECRET_PATTERNS:
                if pattern.search(node):
                    findings.append({"location": location, "category": category})

    walk(value)
    return findings


def _sanitize_evidence(items, project_root):
    result = []
    for item in items:
        if not isinstance(item, dict) or not validate_relative_path(item.get("path")):
            raise SensitiveContent("Evidence paths must be project-relative POSIX paths.")
        allowed = {
            "path": item["path"],
            "symbol_or_anchor": item.get("symbol_or_anchor"),
            "summary": str(item.get("summary") or "")[:1000],
            "source_revision": item.get("source_revision"),
            "file_digest": item.get("file_digest"),
            "verification_status": item.get("verification_status") or "unverified",
        }
        target = (Path(project_root) / item["path"]).resolve()
        try:
            target.relative_to(Path(project_root).resolve())
        except ValueError:
            raise SensitiveContent("Evidence path escapes the project root.")
        if target.is_file():
            allowed["file_digest"] = sha256_bytes(target.read_bytes())
            allowed["verification_status"] = "verified"
        result.append(allowed)
    return result


def _handoff_item(item):
    if item is None:
        return None
    return {key: copy.deepcopy(item[key]) for key in ["id", "kind", "text", "state", "certainty", "lifecycle", "scope"]}


def _import_item(item):
    if item is None:
        return None
    imported = copy.deepcopy(item)
    imported.pop("id", None)
    imported["state"] = "inferred"
    imported["certainty"] = "inferred"
    imported["lifecycle"] = "active"
    imported["source"] = {"kind": "external_untrusted_content"}
    imported["source_ref"] = "handoff-import"
    return normalize_semantics(imported)


def _verify_package(package):
    if not isinstance(package, dict) or package.get("schema_version") != SCHEMA_VERSION:
        raise UsageError("Unsupported handoff schema.")
    claimed = package.get("package_digest")
    unsigned = dict(package)
    unsigned.pop("package_digest", None)
    if claimed != sha256_value(unsigned):
        raise SensitiveContent("Handoff package digest mismatch.")


def _latest_checkpoint_evidence(store, contract_id):
    index_path = store.contract_dir(contract_id) / "checkpoints" / "index.json"
    if not index_path.exists():
        return []
    index = read_json(index_path)
    entries = index.get("checkpoints", [])
    if not entries:
        return []
    checkpoint_id = entries[-1].get("checkpoint_id")
    path = index_path.parent / "{0}.json".format(checkpoint_id)
    if not path.exists():
        return []
    return read_json(path).get("evidence", [])
