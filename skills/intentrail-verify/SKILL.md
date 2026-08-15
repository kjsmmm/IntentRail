---
name: intentrail-verify
description: Explicitly verify work against the latest IntentRail objective, constraints, and acceptance criteria before completion or delivery. Use when the user asks for validation, acceptance checking, readiness review, or confirmation that current work meets the latest request; do not invoke implicitly.
---

# IntentRail Verify

Resolve the sibling `intentrail` Skill and its trusted managed CLI. Load the latest compact status immediately before evaluation. If no trusted CLI resolves, ask the user to run `intentrail doctor`. Inspect real artifacts or tool evidence; do not accept a previous summary or imported handoff as proof.

Stop if an acceptance criterion is conflicted, stale, or needs review. Otherwise produce one result for every active criterion using `pass`, `fail`, or `not_in_scope`, with concise evidence. Submit it through `intentrail verify --input <json-or-> --json` using the current version. Mark complete only when every active criterion passes. Report passed, failed, out-of-scope, and completion-blocking items separately.
