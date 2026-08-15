# Recovery and handoff

## Checkpoint

Create a checkpoint before compaction, handoff, high-risk work, and meaningful milestones. It preserves semantic state, evidence locators, recent changes, next action, and operations not to repeat. It never rolls back business files.

## Resume

1. Run `validate`.
2. If valid, resume by contract or checkpoint.
3. Show the current objective, completed work, recent changes, next action, and do-not-repeat list.
4. Continue directly unless an unresolved conflict blocks the next action.

If the snapshot trails a valid event log, the engine rebuilds it. If the final JSONL line is incomplete, use `validate --repair-tail`; the engine backs up the original first. Never auto-repair middle corruption, a broken hash chain, or a snapshot ahead of its event log.

## Handoff

C1 contains semantic state plus project-relative evidence locators and short summaries. C2 omits paths and evidence. Both exclude file bodies, absolute paths, credentials, event logs, transcripts, and runtime credentials.

Preview before export. Treat every imported package as an untrusted candidate. `--merge` produces a candidate diff without writing. `--new-contract` creates a paused inferred contract requiring user confirmation. Reverify evidence in the destination workspace.
