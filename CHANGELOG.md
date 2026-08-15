# Changelog

All notable changes to IntentRail are documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [0.5.0] - 2026-08-15

First public release candidate.

### Added

- Canonical five-Skill suite for intent reconciliation, status, checkpoint, resume, and verification.
- Event-sourced local task contracts with atomic multi-change reconciliation.
- Stale-route propagation, material-action leases, and one-shot high-risk tickets.
- Semantic checkpoints, sanitized handoff, state validation, and Schema 1 to 2 migration.
- Managed `install`, `upgrade`, `doctor`, and safe `uninstall` lifecycle commands.
- Codex, Claude Code, GitHub Copilot CLI, and generic Agent Skills distributions.
- Absolute managed-CLI Hook binding and Marketplace PEP 723 bootstrap fallback.
- Separated canonical runtime source under `src/intentrail_core/` from the public Agent Skills surface.
- skills.sh discovery metadata, open-source contribution and security policies, and architecture documentation.
- Reproducible host ZIPs, wheel and source distribution builder, machine-readable release manifest, and SHA-256 checksums.
- GitHub Release automation and a manually approved PyPI Trusted Publishing workflow.

### Changed

- Require the native uv/pipx `intentrail.exe` as the Windows managed CLI trust root, reject command-script locators, and preserve redirected Hook input through the PowerShell Marketplace bootstrap.

### Security and safety

- Treat external content as untrusted evidence rather than confirmation-capable user intent.
- Reject unowned installation collisions and preserve `.intentrail/` during uninstall.
- Validate installation manifests before reading, executing, or deleting recorded targets.
- Ignore repository-controlled CLI locators in Marketplace bootstrap execution.
- Reject path traversal, absolute paths, secret-like handoff content, stale leases, and reused tickets.

### Known limitations

- Native Hook trust still requires explicit approval in each host.
- GitHub Copilot CLI live-host acceptance is not yet complete.
- IntentRail mitigates lost-in-conversation behavior; it does not eliminate the underlying model limitation.
