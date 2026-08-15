# Activation probes

Keep IntentRail dormant when the work is a simple question, an unambiguous one-step action, casual chat, a low-risk read-only check, or an unrelated temporary detour.

Activate when at least one durable benefit exists:

- The task has multiple stages or will span turns.
- The user adds, replaces, revokes, or corrects a requirement.
- Requirements conflict or depend on a later decision.
- A material action could follow stale intent.
- Context compaction, handoff, or task switching is likely.
- Completion must be checked against several criteria.

Loading the Skill is not activation. Persist only when the first material intent item must survive across turns.
