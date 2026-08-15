# Security Policy

## Supported versions

IntentRail is pre-1.0. Security fixes are applied to the latest published minor version. Older development snapshots are not supported.

## Reporting a vulnerability

Do not publish reports that contain credentials, private task state, exploitable Hook payloads, or user file contents. Use GitHub private vulnerability reporting on the IntentRail repository after publication. If private reporting is unavailable, open a public issue containing no sensitive or exploit-ready detail and request a private contact channel.

Include the affected version and host, reproduction conditions, expected impact, and whether the issue crosses one of these boundaries:

- untrusted content becoming confirmed user intent;
- repository-controlled data selecting an executable;
- Hook command injection or unsafe path handling;
- installer ownership escape, overwrite, or deletion;
- Gate bypass for destructive, external, release, or final actions;
- handoff or checkpoint disclosure of secrets, absolute paths, or conversation content.

## Security invariants

- Managed Hooks execute the verified absolute `intentrail` CLI path.
- Repository files are not trusted as executable locators.
- Marketplace launchers prefer a managed CLI and use bundled source only as a constrained fallback.
- External content cannot confirm, revoke, or override direct user intent.
- Uninstall removes only files recorded as IntentRail-owned and never removes `.intentrail/` task state.
- Release artifacts are reproducible where practical and covered by SHA-256 checksums and a release manifest.
