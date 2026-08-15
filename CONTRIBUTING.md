# Contributing to IntentRail

IntentRail changes must preserve both deterministic state correctness and Agent interaction quality.

## Development setup

Use Python `>=3.11,<4` in an isolated development environment:

```text
python -m pip install -e ".[test]"
python tools/check.py
```

Normal users do not need to configure a system Python; this section applies only to contributors and source builds.

## Source ownership

- `src/intentrail_core/` is the only Python runtime source.
- `skills/` is the only vendor-neutral Skill source.
- `adapters/` contains thin host manifests and Hook templates.
- `dist/`, wheel contents, and host packages are generated and must not be edited directly.
- `distribution/canonical.json` defines the coordinated product version, host set, and compatibility boundary.

Do not copy Skill instructions into host adapters. Add a host-specific exception only when the host contract requires it, and cover it with an adapter test.

## Required checks

Before opening a pull request:

1. Run `python tools/check.py`.
2. Add or update deterministic tests for runtime changes.
3. Add a multi-turn product regression case for new activation, reconciliation, recovery, or Gate behavior.
4. Run real-host checks when changing a Hook, manifest, installer path, or trust boundary.
5. Update `CHANGELOG.md` for user-visible behavior.

Mechanical safety constraints belong in code or CI, not only in Skill prose. Never weaken provenance, path ownership, atomic reconciliation, or Gate checks to make a scenario pass.

## Pull requests

Keep changes focused and explain:

- the user-visible intent-alignment behavior;
- affected hosts and compatibility level;
- state or Schema migration impact;
- evidence from tests or real-host validation.
