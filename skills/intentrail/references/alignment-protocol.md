# Alignment protocol

## Reconcile one user turn atomically

Compare the latest direct user message with the current projection and identify the smallest affected scope:

- `ADD`: a requirement can coexist with active items.
- `MODIFY`: a new item explicitly replaces one active item.
- `REVOKE`: an active item is cancelled without replacement.
- `CONFIRM`: the user adopts an inference or assumption.
- `CONFLICT`: requirements cannot coexist and replacement is unclear.
- `DEFER`: a decision is postponed.
- `RESOLVE`: the user settles a conflict.

Put all material changes from one message in one `reconcile` batch with `base_version`, a stable idempotency key, one `source_ref`, and a changes array. The engine applies all changes or none. Reload and retry after a stale-version response; never overwrite blindly.

Do not invent a `SWITCH` delta. Replace the objective with `MODIFY` when the same task changes; create or select another contract when the user starts an independent task.

## Separate certainty from lifecycle

Use `certainty` for epistemic status: `confirmed`, `inferred`, or `assumed`.

Use `lifecycle` for validity: `active`, `conflicted`, `superseded`, `revoked`, `stale`, or `needs_review`.

Never promote an Agent inference to confirmed intent. Preserve the distinction between replacement (`superseded`) and cancellation (`revoked`). The legacy `state` field is a compatibility projection, not the canonical decision field.

## Propagate impact conservatively

Attach `depends_on` only to Agent-derived decisions, assumptions, or completed-work claims whose validity depends on specific intent items.

- Explicit dependency on a replaced or revoked item becomes `stale`.
- An unlinked derived item in the affected scope becomes `needs_review`.
- Unrelated items stay unchanged.
- Never infer a dependency from keywords in deterministic code.

Tell the user only when the update changes the next action, creates a conflict, or invalidates meaningful work.
