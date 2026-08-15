---
name: intentrail-resume
description: Explicitly restore and continue from validated IntentRail contract or checkpoint state after interruption, context compaction, Agent handoff, or task switching. Use only when the user asks to resume or continue tracked work; do not invoke implicitly.
---

# IntentRail Resume

Resolve the sibling `intentrail` Skill and its trusted managed CLI. Run `intentrail validate --json` before resuming. If no trusted CLI resolves, ask the user to run `intentrail doctor`. If validation reports recovery required, stop high-risk work and explain the recovery action.

Resume with `intentrail resume --contract <id> --json` or `intentrail resume --checkpoint <id> --json`. Do not roll back project files. Show only the current objective, completed work, recent material changes, next action, and operations that must not be repeated. Continue directly when no conflict remains; do not demand ceremonial confirmation.
