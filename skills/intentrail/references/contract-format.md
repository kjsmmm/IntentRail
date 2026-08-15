# Deterministic interface

Use the managed `intentrail` CLI, `--root`, and `--json`. Pass `--input -` to read one JSON object from stdin. Do not select a Python interpreter or import an isolated uv/pipx environment from a system Python.

## Create a contract

```json
{
  "objective": "Ship the parser",
  "deliverables": ["Parser module"],
  "constraints": ["Use only the Python standard library"],
  "acceptance_criteria": ["All parser tests pass"],
  "source": {"kind": "user"},
  "source_ref": "user-turn-12",
  "idempotency_key": "task-parser-v1"
}
```

Items contain `certainty`, `lifecycle`, `scope`, provenance, `supersedes`, optional `depends_on`, and `invalidated_by`. `state` remains a read-compatible projection.

## Reconcile one user turn

```json
{
  "contract_id": "<uuid>",
  "base_version": 3,
  "idempotency_key": "turn-18-reconciliation",
  "source": {"kind": "user"},
  "source_ref": "user-turn-18",
  "changes": [
    {
      "operation": "MODIFY",
      "entity_kind": "constraint",
      "target_id": "<old-item-uuid>",
      "after": {"text": "Use PostgreSQL", "certainty": "confirmed", "scope": "database"}
    },
    {
      "operation": "ADD",
      "entity_kind": "acceptance_criterion",
      "after": {"text": "Migration tests pass", "certainty": "confirmed", "scope": "database"}
    }
  ],
  "unresolved": []
}
```

The batch is all-or-nothing. Use a stable idempotency key for retries. `MODIFY`, `REVOKE`, `CONFIRM`, and `RESOLVE` require an existing target. Use `event apply` only for one lifecycle/control event or a legacy integration.

## Issue Gate credentials

Ordinary reversible write lease:

```json
{
  "decision": "PASS",
  "contract_id": "<uuid>",
  "contract_version": 5,
  "event_head_hash": "<sha256>",
  "binding_id": "<uuid>",
  "turn_or_prompt_id": "turn-21",
  "allowed_scopes": ["project-files"],
  "action_summary": "Implement the selected parser route",
  "intent_refs": ["<active-constraint-uuid>"],
  "decision_refs": ["<active-decision-uuid>"],
  "affected_scopes": ["runtime"]
}
```

Omit the Action Basis fields for ordinary reversible writes. Include them when the write commits to a route.

High-risk action ticket:

```json
{
  "lease_id": "<uuid>",
  "binding_id": "<uuid>",
  "action_class": "release",
  "action_summary": "Publish the artifact requested by the active objective",
  "intent_refs": ["<active-objective-uuid>"],
  "decision_refs": ["<active-decision-uuid>"],
  "affected_scopes": ["release"],
  "targets": ["dist/output.zip"]
}
```

Run `gate classify` first for host-integrated high-risk calls and reuse its exact action class and targets. The engine verifies references and signs credentials; the Agent remains responsible for the semantic `PASS` judgment.

## Explain or revert

```text
intentrail explain --item <uuid> --root <root> --json
intentrail explain --ticket <uuid> --root <root> --json
intentrail revert [--event <uuid>] --root <root> --json
```

Explanations expose normalized provenance and dependencies, not full prompts. Revert appends an inverse event and never rewrites history.

Update the execution cursor after replanning:

```json
{
  "expected_version": 7,
  "idempotency_key": "progress-turn-23",
  "current_stage": "implementation",
  "next_material_action": "Implement the JSON storage adapter"
}
```

Run it through `intentrail progress --input <json-or->`. A material modification or revocation clears the previous next action so stale cursors are never silently retained.

## Verify

Submit one result for every active acceptance criterion. Verification fails while a criterion is `conflicted`, `stale`, or `needs_review`. Completion requires all active criteria to pass against current evidence.

## Migrate

Run `migrate --to 2.0.0` for v1 state. Migration creates a local backup, upgrades certainty/lifecycle fields, rebuilds event hashes, regenerates projections, and invalidates ephemeral runtime credentials.
