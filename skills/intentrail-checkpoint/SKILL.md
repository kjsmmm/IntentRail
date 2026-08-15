---
name: intentrail-checkpoint
description: Explicitly create an IntentRail semantic recovery checkpoint or prepare a sanitized C1/C2 handoff package. Use when the user asks to save task state, prepare for context compaction, transfer work to another Agent or device, or preserve a milestone; do not invoke implicitly.
---

# IntentRail Checkpoint

Resolve the sibling `intentrail` Skill and use its trusted managed CLI. If no trusted CLI resolves, do not synthesize or edit checkpoint state; ask the user to run `intentrail doctor`.

For a local recovery point, run `checkpoint create --purpose <purpose> --json`. Explain that it preserves semantic task state and does not roll back business files.

For handoff, first show a compact preview containing the objective, active constraints, acceptance criteria, progress, next action, and included evidence locators. Exclude absolute paths, file bodies, event logs, conversation transcripts, runtime data, and credentials. After user review, run `handoff export --mode c1 --reviewed --output <file> --json`; use C2 when the user wants no paths or evidence locators.
