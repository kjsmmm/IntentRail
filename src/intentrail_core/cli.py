"""Canonical IntentRail command-line interface."""

import argparse
import json
import os
import sys

from . import checkpoint as checkpoints
from . import installer
from .constants import EXIT_INTERNAL_ERROR, EXIT_OK, EXIT_OPERATION_FAILED, PRODUCT_VERSION, SCHEMA_VERSION
from .contracts import apply_change, compact_status, create_contract, diff_versions, load_reconciled, undo
from .explain import explain
from .errors import IntentRailError, UsageError
from .gates import consume_ticket, handle_hook, issue_lease, issue_ticket
from .handoff import export_handoff, import_handoff, inspect_handoff
from .host_adapter import classify_tool, extract_targets, normalize_event, process_host_hook, render_output
from .migrate import migrate
from .precedents import confirm_precedent, list_precedents, revoke_precedent
from .reconcile import apply_reconciliation
from .state import StateStore
from .util import load_input
from .validate import validate_project
from .verify import verify_result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def build_parser():
    parser = Parser(prog="intentrail", description="Evolving-intent reconciliation and stale-route control")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--scope", choices=["repo", "user"], default="repo")

    for name in ["install", "upgrade"]:
        lifecycle = commands.add_parser(name)
        lifecycle.add_argument("--hosts", "--host", dest="hosts", default="auto")
        lifecycle.add_argument("--scope", choices=["repo", "user"], default="user")
        lifecycle.add_argument("--cli-path")
        lifecycle.add_argument("--dry-run", action="store_true")
    uninstall_parser = commands.add_parser("uninstall")
    uninstall_parser.add_argument("--hosts", "--host", dest="hosts", default="auto")
    uninstall_parser.add_argument("--scope", choices=["repo", "user"], default="user")
    uninstall_parser.add_argument("--dry-run", action="store_true")
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--hosts", "--host", dest="hosts", default="auto")
    doctor_parser.add_argument("--scope", choices=["repo", "user"], default="user")
    doctor_parser.add_argument("--no-hook-test", action="store_true")

    contract = commands.add_parser("contract")
    contract_commands = contract.add_subparsers(dest="contract_command", required=True)
    create = contract_commands.add_parser("create")
    create.add_argument("--input", required=True)
    select = contract_commands.add_parser("select")
    select.add_argument("contract_id")

    event = commands.add_parser("event")
    event_commands = event.add_subparsers(dest="event_command", required=True)
    apply_parser = event_commands.add_parser("apply")
    apply_parser.add_argument("--input", required=True)

    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--input", required=True)

    status = commands.add_parser("status")
    status.add_argument("--contract")
    status.add_argument("--compact", action="store_true")
    progress = commands.add_parser("progress")
    progress.add_argument("--input", required=True)

    diff = commands.add_parser("diff")
    diff.add_argument("--contract")
    diff.add_argument("--from", dest="from_version", type=int)
    diff.add_argument("--to", dest="to_version", type=int)

    validate = commands.add_parser("validate")
    validate.add_argument("--contract")
    validate.add_argument("--repair-tail", action="store_true")

    checkpoint = commands.add_parser("checkpoint")
    checkpoint_commands = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_create = checkpoint_commands.add_parser("create")
    checkpoint_create.add_argument("--contract")
    checkpoint_create.add_argument("--purpose", default="manual")
    checkpoint_list = checkpoint_commands.add_parser("list")
    checkpoint_list.add_argument("--contract")
    checkpoint_show = checkpoint_commands.add_parser("show")
    checkpoint_show.add_argument("checkpoint_id")

    resume = commands.add_parser("resume")
    resume_target = resume.add_mutually_exclusive_group(required=True)
    resume_target.add_argument("--contract")
    resume_target.add_argument("--checkpoint")

    gate = commands.add_parser("gate")
    gate_commands = gate.add_subparsers(dest="gate_command", required=True)
    lease = gate_commands.add_parser("lease")
    lease.add_argument("--input", required=True)
    ticket = gate_commands.add_parser("ticket")
    ticket.add_argument("--input", required=True)
    consume = gate_commands.add_parser("consume")
    consume.add_argument("ticket_id")
    classify = gate_commands.add_parser("classify")
    classify.add_argument("--input", required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--input", required=True)

    explain_parser = commands.add_parser("explain")
    explain_parser.add_argument("--contract")
    explain_target = explain_parser.add_mutually_exclusive_group()
    explain_target.add_argument("--item")
    explain_target.add_argument("--ticket")

    undo_parser = commands.add_parser("undo")
    undo_parser.add_argument("--contract")
    undo_parser.add_argument("--event")
    revert_parser = commands.add_parser("revert")
    revert_parser.add_argument("--contract")
    revert_parser.add_argument("--event")

    for name in ["pause", "unpause"]:
        lifecycle = commands.add_parser(name)
        lifecycle.add_argument("--contract")

    mode = commands.add_parser("mode")
    mode.add_argument("interaction_mode", choices=["quiet", "balanced", "strict"])

    precedents = commands.add_parser("precedents")
    precedent_commands = precedents.add_subparsers(dest="precedents_command", required=True)
    precedent_commands.add_parser("list")
    precedent_confirm = precedent_commands.add_parser("confirm")
    precedent_confirm.add_argument("--input", required=True)
    precedent_revoke = precedent_commands.add_parser("revoke")
    precedent_revoke.add_argument("precedent_id")

    handoff = commands.add_parser("handoff")
    handoff_commands = handoff.add_subparsers(dest="handoff_command", required=True)
    handoff_export = handoff_commands.add_parser("export")
    handoff_export.add_argument("--contract")
    handoff_export.add_argument("--mode", choices=["c1", "c2"], default="c1")
    handoff_export.add_argument("--output", required=True)
    handoff_export.add_argument("--reviewed", action="store_true")
    handoff_inspect = handoff_commands.add_parser("inspect")
    handoff_inspect.add_argument("file")
    handoff_import = handoff_commands.add_parser("import")
    handoff_import.add_argument("file")
    import_mode = handoff_import.add_mutually_exclusive_group(required=True)
    import_mode.add_argument("--new-contract", action="store_true")
    import_mode.add_argument("--merge", action="store_true")

    hook = commands.add_parser("hook")
    hook.add_argument("--host", required=True)
    hook.add_argument("--event", required=True)
    hook.add_argument("--input", default="-")

    migrate_parser = commands.add_parser("migrate")
    migrate_parser.add_argument("--to", default=SCHEMA_VERSION)

    commands.add_parser("version")
    return parser


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw
    command_name = next((part for part in raw if not part.startswith("-")), "help")
    try:
        json_mode = _extract_flag(raw, "--json")
        explicit_root = _extract_option(raw, "--root")
        args = build_parser().parse_args(raw)
        if args.command == "hook":
            return _run_host_hook_command(args, explicit_root)
        data, message, exit_code = dispatch(args, explicit_root)
        envelope = _envelope(True, command_name, exit_code, message, data=data)
    except IntentRailError as exc:
        envelope = _envelope(
            False,
            command_name,
            exc.exit_code,
            exc.message,
            error={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "recovery_actions": exc.recovery_actions,
            },
        )
        exit_code = exc.exit_code
    except Exception as exc:
        message = "Unexpected internal error."
        envelope = _envelope(
            False,
            command_name,
            EXIT_INTERNAL_ERROR,
            message,
            error={"code": "INTERNAL_ERROR", "message": message, "details": {"type": type(exc).__name__}, "recovery_actions": []},
        )
        exit_code = EXIT_INTERNAL_ERROR
    _write_output(envelope, json_mode)
    return exit_code


def dispatch(args, explicit_root=None):
    if args.command == "version":
        return {"product_version": PRODUCT_VERSION, "schema_version": SCHEMA_VERSION}, "IntentRail version", EXIT_OK
    if args.command == "handoff" and args.handoff_command == "inspect":
        data = inspect_handoff(args.file)
        return data, "Handoff package is valid", EXIT_OK
    if args.command == "init":
        store = StateStore.discover(explicit_root, require_initialized=False)
        return store.init(args.scope), "IntentRail initialized", EXIT_OK
    if args.command in {"install", "upgrade"}:
        root = explicit_root or os.getcwd()
        result = installer.install_or_upgrade(args.command, args.hosts, args.scope, root, args.cli_path, args.dry_run)
        return result, "Installation plan" if args.dry_run else "IntentRail hosts installed", EXIT_OK
    if args.command == "uninstall":
        root = explicit_root or os.getcwd()
        return installer.uninstall(args.hosts, args.scope, root, args.dry_run), "IntentRail hosts uninstalled" if not args.dry_run else "Uninstall plan", EXIT_OK
    if args.command == "doctor":
        root = explicit_root or os.getcwd()
        report = installer.doctor(args.hosts, args.scope, root, run_hooks=not args.no_hook_test)
        healthy = all(item["support"] != "Unsupported" for item in report["hosts"])
        return report, "IntentRail runtime is healthy" if healthy else "IntentRail runtime is degraded", EXIT_OK if healthy else EXIT_OPERATION_FAILED
    store = StateStore.discover(explicit_root)

    if args.command == "contract":
        if args.contract_command == "create":
            return create_contract(store, load_input(args.input)), "Contract created", EXIT_OK
        return store.select_contract(args.contract_id), "Contract selected", EXIT_OK
    if args.command == "event":
        payload = load_input(args.input)
        contract_id = payload.get("contract_id") or store.resolve_contract_id()
        return apply_change(store, contract_id, payload), "Intent change applied", EXIT_OK
    if args.command == "reconcile":
        return apply_reconciliation(store, load_input(args.input)), "Intent changes reconciled", EXIT_OK
    if args.command == "status":
        contract_id = store.resolve_contract_id(args.contract)
        contract, events = load_reconciled(store, contract_id)
        return compact_status(contract, events) if args.compact else contract, "Current task state", EXIT_OK
    if args.command == "progress":
        payload = load_input(args.input)
        contract_id = payload.get("contract_id") or store.resolve_contract_id()
        progress_after = {key: payload.get(key) for key in ["current_stage", "next_material_action"] if key in payload}
        if not progress_after:
            raise UsageError("Progress requires current_stage and/or next_material_action.")
        if "expected_version" not in payload or not payload.get("idempotency_key"):
            raise UsageError("Progress requires expected_version and idempotency_key.")
        change = {
            "operation": "PROGRESS",
            "entity_kind": "contract",
            "entity_id": contract_id,
            "after": progress_after,
            "source": payload.get("source") or {"kind": "agent"},
            "source_ref": payload.get("source_ref") or "progress-command",
            "expected_version": payload["expected_version"],
            "idempotency_key": payload["idempotency_key"],
            "reversible": True,
        }
        return apply_change(store, contract_id, change), "Execution cursor updated", EXIT_OK
    if args.command == "diff":
        contract_id = store.resolve_contract_id(args.contract)
        return diff_versions(store, contract_id, args.from_version, args.to_version), "Contract diff", EXIT_OK
    if args.command == "validate":
        result = validate_project(store, args.contract, args.repair_tail)
        return result, "State is valid" if result["valid"] else "State validation failed", EXIT_OK if result["valid"] else EXIT_OPERATION_FAILED
    if args.command == "checkpoint":
        if args.checkpoint_command == "show":
            return checkpoints.show_checkpoint(store, args.checkpoint_id), "Checkpoint", EXIT_OK
        contract_id = store.resolve_contract_id(args.contract)
        if args.checkpoint_command == "list":
            return checkpoints.list_checkpoints(store, contract_id), "Checkpoints", EXIT_OK
        return checkpoints.create_checkpoint(store, contract_id, args.purpose), "Checkpoint created", EXIT_OK
    if args.command == "resume":
        if args.contract:
            return checkpoints.resume_contract(store, args.contract), "Contract resumed", EXIT_OK
        return checkpoints.resume_checkpoint(store, args.checkpoint), "Checkpoint resumed", EXIT_OK
    if args.command == "gate":
        if args.gate_command == "lease":
            return issue_lease(store, load_input(args.input)), "Gate lease issued", EXIT_OK
        if args.gate_command == "ticket":
            return issue_ticket(store, load_input(args.input)), "Action ticket issued", EXIT_OK
        if args.gate_command == "classify":
            payload = load_input(args.input)
            action_class, scope = classify_tool(payload.get("tool_name", "unknown"), payload.get("tool_input", {}))
            return {
                "action_class": action_class,
                "scope": scope,
                "targets": extract_targets(payload.get("tool_input", {})),
            }, "Action classified", EXIT_OK
        return consume_ticket(store, args.ticket_id), "Action ticket consumed", EXIT_OK
    if args.command == "verify":
        result = verify_result(store, load_input(args.input))
        return result, "Verification passed" if result["passed"] else "Verification failed", EXIT_OK if result["passed"] else EXIT_OPERATION_FAILED
    if args.command == "explain":
        return explain(store, {"contract_id": args.contract, "item_id": args.item, "ticket_id": args.ticket}), "IntentRail explanation", EXIT_OK
    if args.command in {"undo", "revert"}:
        contract_id = store.resolve_contract_id(args.contract)
        return undo(store, contract_id, args.event), "Intent update reverted", EXIT_OK
    if args.command in {"pause", "unpause"}:
        contract_id = store.resolve_contract_id(args.contract)
        contract, _ = load_reconciled(store, contract_id)
        operation = "PAUSE" if args.command == "pause" else "RESUME"
        result = apply_change(
            store,
            contract_id,
            {
                "operation": operation,
                "entity_kind": "contract",
                "entity_id": contract_id,
                "source": {"kind": "user"},
                "source_ref": args.command + "-command",
                "expected_version": contract["version"],
                "idempotency_key": "{0}:{1}:{2}".format(args.command, contract_id, contract["version"]),
            },
        )
        store.update_config({"paused": args.command == "pause"})
        return result, "IntentRail paused" if args.command == "pause" else "IntentRail resumed", EXIT_OK
    if args.command == "mode":
        config = store.update_config({"interaction_mode": args.interaction_mode})
        return {"interaction_mode": config["interaction_mode"]}, "Interaction mode updated", EXIT_OK
    if args.command == "precedents":
        if args.precedents_command == "list":
            return list_precedents(store), "Project precedents", EXIT_OK
        if args.precedents_command == "confirm":
            return confirm_precedent(store, load_input(args.input)), "Precedent confirmed", EXIT_OK
        return revoke_precedent(store, args.precedent_id), "Precedent revoked", EXIT_OK
    if args.command == "handoff":
        if args.handoff_command == "export":
            contract_id = store.resolve_contract_id(args.contract)
            return export_handoff(store, contract_id, args.output, args.mode, args.reviewed), "Handoff exported", EXIT_OK
        return import_handoff(store, args.file, args.new_contract, args.merge), "Handoff imported as untrusted candidate", EXIT_OK
    if args.command == "migrate":
        return migrate(store, args.to), "Migration check complete", EXIT_OK
    raise UsageError("Unsupported command.")


def _run_host_hook_command(args, explicit_root):
    """Hooks must emit host-native JSON, never the normal CLI envelope."""
    try:
        payload = load_input(args.input)
        output = process_host_hook(args.host, payload, args.event, explicit_root)
    except (IntentRailError, ValueError, OSError) as exc:
        event = normalize_event(args.event) or "PreToolUse"
        message = getattr(exc, "message", None) or "IntentRail Hook rejected invalid input."
        output = render_output(args.host, event, {"allow": False, "reason": message}) if event == "PreToolUse" else {"systemMessage": message}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return EXIT_OK


def _envelope(ok, command, exit_code, message, data=None, warnings=None, error=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok and exit_code == EXIT_OK),
        "command": command,
        "exit_code": exit_code,
        "message": message,
        "data": data,
        "warnings": warnings or [],
        "error": error,
    }


def _write_output(envelope, json_mode):
    if json_mode:
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        sys.stdout.write(envelope["message"] + "\n")
        if envelope.get("data") is not None:
            sys.stdout.write(json.dumps(envelope["data"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        if envelope.get("error") is not None:
            sys.stdout.write(json.dumps(envelope["error"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _extract_flag(argv, name):
    found = False
    while name in argv:
        argv.remove(name)
        found = True
    return found


def _extract_option(argv, name):
    if name not in argv:
        return None
    position = argv.index(name)
    if position + 1 >= len(argv):
        raise UsageError("{0} requires a value".format(name))
    value = argv[position + 1]
    del argv[position : position + 2]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
