# Installation and runtime lifecycle

## Normal installation

After the corresponding PyPI release is published:

```text
uv tool install intentrail
intentrail install --hosts auto
```

`pipx install intentrail` is an equivalent isolated alternative. Before the first PyPI publication, install the reviewed wheel from the matching GitHub Release using the fallback below. Python `>=3.11` remains an implementation detail of the uv/pipx environment; Agent Hooks do not resolve or invoke a system interpreter.

The installer performs these steps as one owned lifecycle operation:

1. Detect requested or locally available hosts.
2. Materialize the matching package from the wheel-embedded canonical bundle.
3. Resolve the absolute managed `intentrail` CLI path and reject Python interpreter paths; Windows managed Hooks require the native `intentrail.exe` launcher produced by uv/pipx and reject `.cmd`, `.bat`, and `.ps1` shims.
4. Render every managed Hook to `"<absolute-intentrail>" hook --host ... --event ...`.
5. Atomically install owned files and back up replaced owned content.
6. Write the install manifest and a small CLI locator for GUI processes with a reduced `PATH`.
7. Prewarm the CLI with `intentrail version --json`.
8. Execute a dormant `PreToolUse` Hook probe and validate its native `{}` output.
9. Report `Full-candidate`, `Standard`, or `Unsupported`; native host trust remains user-controlled.

Preview, diagnose, upgrade, and uninstall:

```text
intentrail install --hosts auto --dry-run
intentrail doctor --hosts auto
intentrail uninstall --hosts auto
```

Upgrade the managed tool with `uv tool upgrade intentrail` or `pipx upgrade intentrail`, then run `intentrail upgrade --hosts auto` to refresh owned host packages and absolute Hook bindings.

`--hosts` accepts `auto`, `all`, or a comma-separated subset of `codex`, `claude-code`, `copilot-cli`, and `generic-agent-skills`. User scope is the default; add `--scope repo --root <project>` for project scope.

## Agent Skills ecosystem installation

IntentRail keeps its five vendor-neutral Skills under `skills/` so compatible clients can discover them from the repository:

```text
npx skills add kjsmmm/IntentRail --list
npx skills add kjsmmm/IntentRail
```

For a local source checkout, replace `kjsmmm/IntentRail` with `.`. This installation surface is intended for discovery and semantic Skill use. It does not install the deterministic CLI or claim native Hook support. Install the CLI with uv or pipx and run `intentrail install --hosts auto` for managed state, Gate, lifecycle, and `doctor` behavior.

## GitHub Release and offline fallback

Each GitHub Release contains a wheel, source distribution, four host archives, `release-manifest.json`, and `SHA256SUMS.txt`. Verify the selected artifact against the checksum file before offline installation.

Install a downloaded wheel without configuring a system Python:

```text
uv tool install ./intentrail-<version>-py3-none-any.whl
intentrail install --hosts auto
```

Native Marketplace archives contain canonical Skills, host templates, thin launchers, and a bundled standard-library runtime fallback. Generated archives are release products and are not maintained as independent source trees.

## Runtime resolution

Managed Hooks always use the absolute CLI path. Marketplace-direct packages use their launchers and PEP 723 bootstrap in this order:

1. User installation manifest or CLI locator written outside the repository by `intentrail install`.
2. `intentrail` on `PATH`.
3. `uv run --quiet --script` for the bundled core.
4. A compatible system Python only as a development/compatibility fallback.
5. Explicit degraded output with `intentrail doctor` guidance.

The user locator solves the common case where a GUI host receives a different `PATH` from the user's terminal. It contains only the absolute CLI path, not credentials or project intent. Marketplace launchers do not execute repository-controlled locators or install manifests as trusted commands. Repo-scope managed Hooks already contain the verified absolute CLI path.

## Host locations

| Host | Repo scope | User scope | Activation note |
| --- | --- | --- | --- |
| Codex | `plugins/intentrail` plus `.agents/plugins/marketplace.json` | `~/plugins/intentrail` plus `~/.agents/plugins/marketplace.json` | Install from the local marketplace, then inspect and trust Hooks. |
| Claude Code | `.claude/skills/intentrail` | `~/.claude/skills/intentrail` | Restart/reload, inspect the plugin, and approve Hooks. |
| Copilot CLI | `.github/intentrail-plugin`, `.github/skills/`, `.github/hooks/intentrail.json` | `~/.copilot/intentrail-plugin`, `~/.copilot/skills/`, `~/.copilot/hooks/intentrail.json` | Confirm repository trust and inspect the installed Hook file. |
| Generic Agent Skills | `.agents/skills/` | `~/.agents/skills/` | Lifecycle behavior depends on the client; support remains Standard. |

## Ownership and rollback

- Installed directories carry `.intentrail-owned.json` and are recorded in the scope manifest.
- Existing same-name content without matching ownership is a hard conflict.
- Hook and marketplace JSON are parsed before modification.
- Failed doctor verification restores prior owned files, the previous manifest, and the previous CLI locator.
- Uninstall checks ownership again and leaves unowned content untouched.
- `.intentrail/` is never an installer target.
