---
name: intentrail-status
description: Explicitly show the current IntentRail objective, active constraints, stale or review-needed decisions, unresolved conflicts, recent intent changes, and next material action. Use only when the user asks to inspect, explain, or compare tracked intent state; do not invoke implicitly.
---

# IntentRail Status

Resolve the sibling `intentrail` Skill and its trusted managed CLI. Run `intentrail status --compact --json` against the project root. Run `intentrail diff --json` when the user asks what changed. If no trusted CLI resolves, preserve state and ask the user to run `intentrail doctor`.

Do not initialize state when none exists. Report that no tracked contract exists and suggest `$intentrail` only if the task would benefit from tracking.

Translate internal fields into: current objective, confirmed constraints, temporary assumptions, stale items, items needing review, unresolved conflicts, acceptance criteria, recent changes, and next action. Use `explain --item` when the user asks why. Keep provenance distinctions intact and never modify state.
