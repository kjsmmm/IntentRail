from .helpers import StoreCase
from intentrail_core.bindings import current_turn
from intentrail_core.contracts import load_reconciled
from intentrail_core.gates import issue_lease, issue_ticket
from intentrail_core.host_adapter import classify_tool, extract_targets, process_host_hook


class HostAdapterTests(StoreCase):
    def _bind_and_prompt(self, host="codex", turn="turn-1"):
        process_host_hook(
            host,
            {"hook_event_name": "SessionStart", "session_id": "session-1", "cwd": str(self.root), "source": "startup"},
            explicit_root=str(self.root),
        )
        output = process_host_hook(
            host,
            {"hook_event_name": "UserPromptSubmit", "session_id": "session-1", "turn_id": turn, "prompt": "continue", "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        bindings = list((self.store.state_root / "bindings").glob("*.json"))
        self.assertEqual(len(bindings), 1)
        import json

        binding = json.loads(bindings[0].read_text(encoding="utf-8"))
        self.assertIn("additionalContext", str(output))
        self.assertEqual(current_turn(self.store, binding["binding_id"])["turn_or_prompt_id"], turn)
        return binding

    def _lease(self, binding, turn):
        contract, _ = load_reconciled(self.store, self.contract_id)
        return issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": contract["version"],
                "event_head_hash": contract["event_head_hash"],
                "binding_id": binding["binding_id"],
                "turn_or_prompt_id": turn,
                "allowed_scopes": ["project-files", "external-systems"],
            },
        )

    def test_write_requires_current_turn_lease_and_read_is_silent(self):
        binding = self._bind_and_prompt(turn="turn-1")
        self._lease(binding, "turn-1")
        allowed = process_host_hook(
            "codex",
            {"hook_event_name": "PreToolUse", "session_id": "session-1", "turn_id": "turn-1", "tool_name": "apply_patch", "tool_input": {"path": "app.py"}, "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        self.assertEqual(allowed, {})
        process_host_hook(
            "codex",
            {"hook_event_name": "UserPromptSubmit", "session_id": "session-1", "turn_id": "turn-2", "prompt": "change direction", "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        blocked = process_host_hook(
            "codex",
            {"hook_event_name": "PreToolUse", "session_id": "session-1", "turn_id": "turn-2", "tool_name": "apply_patch", "tool_input": {"path": "app.py"}, "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
        read = process_host_hook(
            "codex",
            {"hook_event_name": "PreToolUse", "session_id": "session-1", "tool_name": "Read", "tool_input": {"path": "app.py"}, "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        self.assertEqual(read, {})

    def test_high_risk_ticket_is_found_by_exact_mechanical_target(self):
        binding = self._bind_and_prompt(host="copilot-cli", turn="turn-risk")
        lease = self._lease(binding, "turn-risk")
        tool_input = {"command": "git push origin main"}
        action_class, scope = classify_tool("Bash", tool_input)
        self.assertEqual((action_class, scope), ("external_write", "external-systems"))
        targets = extract_targets(tool_input)
        ticket = issue_ticket(
            self.store,
            {
                "lease_id": lease["lease_id"],
                "binding_id": binding["binding_id"],
                "action_class": action_class,
                "action_summary": "Publish the requested release",
                "intent_refs": [self.contract["objective"]["id"]],
                "affected_scopes": ["external-systems"],
                "targets": targets,
            },
        )
        allowed = process_host_hook(
            "copilot-cli",
            {"hook_event_name": "PreToolUse", "session_id": "session-1", "tool_name": "Bash", "tool_input": tool_input, "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        self.assertEqual(allowed, {})
        import json

        stored = json.loads((self.store.state_root / "runtime" / binding["binding_id"] / "tickets" / (ticket["ticket_id"] + ".json")).read_text(encoding="utf-8"))
        self.assertIsNotNone(stored["consumed_at"])

    def test_precompact_checkpoints_and_dormant_hook_never_initializes(self):
        before = self.contract["version"]
        process_host_hook(
            "claude-code",
            {"hook_event_name": "PreCompact", "session_id": "s", "cwd": str(self.root)},
            explicit_root=str(self.root),
        )
        current, _ = load_reconciled(self.store, self.contract_id)
        self.assertGreater(current["version"], before)
        dormant = self.root / "dormant-host"
        dormant.mkdir()
        result = process_host_hook(
            "codex",
            {"hook_event_name": "PreToolUse", "session_id": "none", "tool_name": "apply_patch", "tool_input": {}, "cwd": str(dormant)},
            explicit_root=str(dormant),
        )
        self.assertEqual(result, {})
        self.assertFalse((dormant / ".intentrail").exists())

    def test_secret_like_command_targets_are_redacted(self):
        targets = extract_targets({"command": "curl -H 'Authorization: Bearer super-secret-token-value' https://example.test"})
        rendered = " ".join(targets)
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("super-secret-token-value", rendered)
