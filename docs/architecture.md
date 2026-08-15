# Architecture and source ownership

IntentRail is a tool-backed Agent Skill suite. Agent Skills decide when and how to align work; the CLI performs deterministic state transitions and installation.

## Layers

```text
User message or explicit Skill request
                 |
                 v
Canonical Agent Skills in skills/
                 |
                 v
Managed intentrail CLI in src/intentrail_core/
                 |
                 v
Event log, materialized contract, Gate, checkpoint, and verification
```

Host adapters do not contain an independent copy of the semantic protocol. The release builder combines the canonical Skills, runtime, and thin host templates into installable packages.

## Source-of-truth rules

| Concern | Canonical location | Generated consumers |
| --- | --- | --- |
| Agent behavior and triggering | `skills/` | skills.sh installs, native host packages |
| Deterministic runtime | `src/intentrail_core/` | wheel, source distribution, Marketplace fallback |
| Host contracts | `adapters/` | Codex, Claude Code, Copilot, and generic packages |
| Data contracts | `schemas/` | runtime validation and compatibility checks |
| Product/version map | `distribution/canonical.json` | build metadata and release manifest |

Generated packages are disposable. Every host package records the canonical Skill hash and runtime source hash, and validation fails when either copy drifts.

## Distribution surfaces

1. PyPI distributes the managed `intentrail` CLI and its embedded canonical installation bundle.
2. Agent Skills repositories expose the five vendor-neutral Skills for discovery and semantic use.
3. Native Marketplace packages include host manifests, Hooks, Skills, and a release-bundled runtime fallback.
4. GitHub Releases provide wheel, source distribution, host archives, a machine-readable manifest, and checksums.

The five Skills, CLI, Schema, and adapters use one coordinated product version because they operate on the same state protocol. Host support levels may differ, but host packages are not independently versioned products.

## Maintenance gates

- Deterministic unit and integration tests run on every change.
- Package parity tests compare canonical hashes across all generated hosts.
- Real-host checks verify native Hook input/output and trust prompts before release claims change.
- Multi-turn product cases test activation precision, intent reconciliation, stale-route invalidation, recovery, and final verification. These are product regressions rather than an academic benchmark.
