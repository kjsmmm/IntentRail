# IntentRail

**Keep AI agents aligned as your intent evolves.**

IntentRail is an intent-alignment Skill suite for long-running agent work. It helps an agent preserve the latest objective, incorporate new requirements, retire superseded decisions, and avoid continuing down a stale execution path after the user changes direction.

IntentRail is not general-purpose memory, conversation logging, or cloud synchronization. It is a local control layer for evolving user intent.

## Why IntentRail?

Real tasks rarely arrive as one complete prompt. Users disclose missing details, revise earlier choices, remove requirements, and sometimes redirect the task entirely. A long context window can retain all of those messages while the agent still acts on an outdated interpretation.

Two recent studies make this failure mode concrete:

- [LLMs Get Lost in Multi-Turn Conversation](https://www.microsoft.com/en-us/research/publication/llms-get-lost-in-multi-turn-conversation/) reports an average 39% performance drop across six generation tasks when the same requirements are revealed over multiple turns. The study finds that models often commit to a solution too early, make assumptions about missing details, and then over-rely on previous incorrect attempts.
- [LLMs Get Lost in Evolving User Intent](https://arxiv.org/abs/2607.20734) extends the setting beyond gradual disclosure to three common forms of intent change: incremental reveal, revision of an earlier requirement, and redirection to a related task. Strong models still degrade substantially as the number of intent transitions grows.

IntentRail treats this as an execution-control problem as well as a memory problem. The important question is not only *whether the conversation is still available*, but *which requirements are active now, which decisions became stale, and whether the next action is still supported by the user's latest intent*.

IntentRail does not claim to eliminate model-level lost-in-conversation behavior. It adds an external, inspectable alignment layer designed to reduce its practical impact.

## What IntentRail does

When a task becomes meaningfully multi-turn, IntentRail can:

- maintain a compact contract containing the current objective, active constraints, acceptance criteria, assumptions, conflicts, and next material action;
- reconcile additions, corrections, revocations, confirmations, deferrals, and conflict resolutions atomically;
- mark decisions and plans as stale or in need of review when their supporting intent changes;
- stop destructive, external, release, or final actions when they are based on superseded intent;
- create semantic checkpoints before context compaction, interruption, or handoff;
- verify the final result against the latest active requirements rather than the initial prompt.

The control loop is:

```text
user changes intent
        ↓
reconcile the active contract
        ↓
invalidate stale decisions and routes
        ↓
gate material actions
        ↓
checkpoint, resume, and verify against current intent
```

Simple questions and unambiguous one-step requests remain dormant and do not create IntentRail state.

## Install IntentRail

IntentRail ships as a managed CLI plus a public five-Skill suite. Normal users install the CLI in an isolated tool environment; Agent Hooks do not invoke or depend on a separately configured system Python.

### Recommended: uv

After the corresponding PyPI release is published:

```text
uv tool install intentrail
intentrail install --hosts auto
```

### Alternative: pipx

```text
pipx install intentrail
intentrail install --hosts auto
```

Before the first PyPI publication, install the reviewed wheel from the matching GitHub Release as described in [Installation and runtime lifecycle](docs/installation.md). Do not install an unrelated package that happens to use the same name.

The installer detects supported hosts, installs the matching Skills and lifecycle adapters, binds Hooks to the absolute managed `intentrail` executable, prewarms the runtime, and validates Hook input/output.

Confirm the installation with:

```text
intentrail doctor --hosts auto
```

User scope is the default. To keep an installation inside one project instead:

```text
intentrail install --hosts auto --scope repo --root <project>
```

### Agent Skills ecosystem

Compatible ecosystem clients can discover the five vendor-neutral Skills directly from the public repository:

```text
npx skills add kjsmmm/IntentRail --list
npx skills add kjsmmm/IntentRail
```

Skills-only installation supplies the interaction protocol but does not replace the deterministic CLI, managed Hooks, or `doctor`. Native Marketplace and offline users can use the host archives attached to a GitHub Release; those packages contain a constrained bundled-runtime fallback.

See [Installation and runtime lifecycle](docs/installation.md) for ecosystem installation, GitHub Release fallback, host locations, trust prompts, upgrades, rollback, and uninstall behavior.

## Use IntentRail

### Automatic use

After installation, the main `intentrail` Skill can activate when a multi-turn task receives a material change such as:

- “Keep the API stable, but replace MySQL with PostgreSQL.”
- “Drop the PDF deliverable; the dashboard is now the priority.”
- “That earlier assumption was wrong. This must also work offline.”
- “Continue after compaction, but do not repeat the completed migration.”

On the first important intent update, IntentRail creates local task state without blocking the work and shows one short notice. Later updates are reconciled quietly unless a conflict genuinely requires the user to choose.

### Explicit user controls

You can intervene at any time in natural language. Hosts that expose named Skill commands may use `$skill-name` or `/skill-name`, depending on the client.

| What you want | Example request | Skill |
| --- | --- | --- |
| Start or force intent tracking | “Use IntentRail to track this task.” | `intentrail` |
| Inspect the current objective and constraints | “Show the current IntentRail status.” | `intentrail-status` |
| Save a semantic recovery point | “Create an IntentRail checkpoint before we continue.” | `intentrail-checkpoint` |
| Continue after interruption or compaction | “Resume from the latest IntentRail checkpoint.” | `intentrail-resume` |
| Check the result against the latest request | “Verify this work against the current intent.” | `intentrail-verify` |
| Pause tracking | “Pause IntentRail for this task.” | `intentrail` |

The status view distinguishes confirmed requirements, temporary assumptions, stale decisions, unresolved conflicts, recent changes, acceptance criteria, and the next material action. It does not expose raw conversation logs.

## Supported hosts

| Host | Integration |
| --- | --- |
| OpenAI Codex | Skills, lifecycle Hooks, checkpointing, and material-action Gate |
| Claude Code | Skills, lifecycle Hooks, checkpointing, and material-action Gate |
| GitHub Copilot CLI | Agent Skills and repository/user Hook integration |
| Other Agent Skills clients | Semantic Skills; deterministic lifecycle interception depends on the host |

Native Hook trust and approval remain under the user's control. IntentRail reports verified installation as `Full-candidate` until the host accepts and runs the installed Hooks end to end.

## Local state and privacy

IntentRail stores task state under `.intentrail/` by default.

- State remains local; there is no required account or cloud service.
- IntentRail stores normalized intent and provenance references, not full conversation transcripts.
- Web pages, documents, tool output, code comments, and imported handoffs cannot silently confirm or override user intent.
- Cross-device or cross-agent handoff is explicit and sanitized before export.
- Uninstall removes only IntentRail-owned installation files and preserves task state and user files.

## Project status

IntentRail is an early-stage open-source project. Version 0.5.0 includes the canonical five-Skill suite, a standard-library state engine, atomic intent reconciliation, stale-route invalidation, semantic checkpoints, material-action gating, verification, and managed installation for multiple hosts.

The project currently claims mitigation and inspectable control, not a complete solution to lost-in-conversation behavior. Real-host evaluations and broader long-running task coverage remain ongoing work.

Licensed under the [MIT License](LICENSE).

See the [architecture](docs/architecture.md), [changelog](CHANGELOG.md), [contribution guide](CONTRIBUTING.md), and [security policy](SECURITY.md) for release and maintenance details.
