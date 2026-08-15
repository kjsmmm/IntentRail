# Product regression cases

IntentRail evaluations protect user-visible behavior; they are not intended as a paper benchmark.

`cases/stage2-regression.json` covers material intent addition, revision, revocation, redirection, conflict, long-context noise, recovery, untrusted content, dormancy, atomic reconciliation, route invalidation, and stale high-risk actions.

Use three evidence levels:

1. **Deterministic CI** exercises state transitions, Schemas, Gates, installation, and package parity on every change.
2. **Host contract checks** install generated packages and verify native Hook input/output for every affected host before release.
3. **Forward behavior checks** run the multi-turn cases with a real Agent without giving it the expected answer. Record activation, emitted operations, user-visible interaction, final action, host, model, version, and evidence.

A release must not claim a host as fully verified solely because deterministic tests pass. Forward checks should include both positive activation cases and the `simple-task-no-trigger` negative control.
