# IntentRail Agent Skills

This directory is the canonical, vendor-neutral Agent Skills surface for IntentRail. It contains one automatic alignment Skill and four explicit user controls:

| Skill | Purpose | Implicit activation |
| --- | --- | --- |
| `intentrail` | Reconcile material intent changes and stop stale execution routes | Yes |
| `intentrail-status` | Inspect the current objective, constraints, conflicts, and next action | No |
| `intentrail-checkpoint` | Create a semantic recovery point or sanitized handoff | No |
| `intentrail-resume` | Resume validated state after interruption or compaction | No |
| `intentrail-verify` | Verify work against the latest active acceptance criteria | No |

## Install from an Agent Skills repository

From a source checkout, list or install the Skills with a compatible ecosystem client:

```text
npx skills add . --list
npx skills add .
```

The same commands accept the public GitHub repository URL after publication. Skills-only installation provides discovery and the semantic interaction protocol. Deterministic state mutation, Hooks, Gates, managed upgrades, and `doctor` require the IntentRail CLI:

```text
uv tool install intentrail
intentrail install --hosts auto
```

Do not edit generated host packages under `dist/`. The files in this directory and `src/intentrail_core/` are assembled into host releases by `tools/build_distributions.py`.
