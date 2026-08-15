# Platform support

Verified against current public documentation on 2026-08-14.

| Host | Skill package | Lifecycle events packaged | Gate output | Distribution target |
| --- | --- | --- | --- | --- |
| Codex | Native plugin Skills | SessionStart, UserPromptSubmit, PreToolUse, Pre/PostCompact, Stop, SessionEnd | Claude-compatible `hookSpecificOutput`; deny only on Gate failure | Full |
| Claude Code | Native plugin Skills | SessionStart, UserPromptSubmit, PreToolUse, Pre/PostCompact, Stop, SessionEnd | `hookSpecificOutput.permissionDecision=deny` | Full |
| GitHub Copilot CLI | Plugin plus standard Skill/Hook install | sessionStart, userPromptSubmitted, preToolUse, sessionEnd | Root `permissionDecision=deny` | Full |
| Generic Agent Skills | Canonical Skill directories | None guaranteed by the standard | No deterministic host interception | Standard |

## Adapter behavior

- Managed installations bind Hooks to the absolute uv/pipx `intentrail` CLI path; Hook configuration never invokes a system Python interpreter.
- Marketplace-direct packages use bundled Bash/PowerShell launchers plus a dependency-free PEP 723 bootstrap and report a degraded state when no valid runtime can be resolved.

- Session and prompt events bind the host session to the selected contract and inject a compact, bounded local-state summary.
- A prompt event records the current turn. A lease from an earlier turn is rejected even if its TTL has not expired.
- Read-only tools pass silently. Side-effect tools require a current binding and Gate lease while an active contract exists.
- High-risk tools require one exact, unconsumed ticket matching binding, action class, and deterministic target fingerprint.
- `PreCompact` writes a semantic checkpoint through the canonical engine. It never copies business files or blocks compaction to hide failure.
- Dormant projects are not initialized by Hooks and their tools are not blocked.
- Hook JSON input is limited to 1 MiB. Secret-like command fragments are redacted before target material enters runtime credentials.
- Unexpected `PreToolUse` validation failures deny the one call; non-tool lifecycle failures remain advisory.

## Honest support reporting

Packaging and synthetic end-to-end tests establish that adapters translate documented payloads, all distributions contain identical canonical Skills, installed Hooks reference one managed CLI, and dormant Hook probes return native output. Native Hook trust is intentionally controlled by each host and is not inferred from files on disk. `doctor` therefore uses `Full-candidate` until native trust and live host behavior are verified during release acceptance.

Primary references:

- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI Codex Hooks](https://learn.chatgpt.com/docs/hooks)
- [Claude Code plugins](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code Hooks](https://code.claude.com/docs/en/hooks)
- [GitHub Copilot CLI plugins](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference)
- [GitHub Copilot Hooks](https://docs.github.com/en/copilot/reference/hooks-reference)
