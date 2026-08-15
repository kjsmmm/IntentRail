# Drift Gate

Return exactly one semantic decision: `PASS`, `UPDATE`, `CLARIFY`, or `BLOCK`.

- `PASS`: the action is supported by active intent and has no blocking dependency.
- `UPDATE`: a user change has not entered the event stream; reconcile and rerun.
- `CLARIFY`: different interpretations materially change the result and no safe reversible default exists.
- `BLOCK`: the action violates active intent or depends on conflicted, stale, superseded, or revoked support.

Do not run a full Gate for ordinary read-only inspection.

## Level 1: turn and scope

For ordinary reversible local writes, issue a short lease bound to the contract version, event head, host binding, current turn, and allowed scopes. Any intent update invalidates the lease.

## Level 2: action basis

Require an action basis for route-changing choices, destructive changes, external writes, permission changes, secret access, releases, long batches, final delivery, and actions affected by `stale` or `needs_review` items.

Provide a concise action summary, affected scopes, at least one confirmed active `intent_ref`, and any supporting active `decision_refs`. Put this basis on the lease for a route-changing ordinary write. For a mechanically high-risk call, also provide exact targets and issue a one-shot ticket. References to inactive, unconfirmed, or non-intent items must fail closed.

Hooks classify mechanical risk and verify credentials only. They never interpret user intent, sign their own credentials, or replace host permission and safety controls.
