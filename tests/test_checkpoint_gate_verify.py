from concurrent.futures import ThreadPoolExecutor

from .helpers import StoreCase
from intentrail_core.checkpoint import create_checkpoint, resume_checkpoint
from intentrail_core.contracts import apply_change, load_reconciled
from intentrail_core.errors import GateBlocked, UsageError
from intentrail_core.gates import consume_ticket, handle_hook, issue_lease, issue_ticket
from intentrail_core.precedents import confirm_precedent, list_precedents, revoke_precedent
from intentrail_core.state import StateStore
from intentrail_core.util import new_id
from intentrail_core.verify import verify_result


class CheckpointGateVerifyTests(StoreCase):
    def test_checkpoint_resume_restores_semantics_not_files(self):
        checkpoint = create_checkpoint(self.store, self.contract_id, "milestone")["checkpoint"]
        current, _ = load_reconciled(self.store, self.contract_id)
        apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "MODIFY",
                "entity_kind": "objective",
                "entity_id": current["objective"]["id"],
                "after": {"text": "Build a different artifact", "state": "confirmed"},
                "source": {"kind": "user"},
                "source_ref": "correction",
                "expected_version": current["version"],
                "idempotency_key": "modify-after-checkpoint",
            },
        )
        resumed = resume_checkpoint(self.store, checkpoint["checkpoint_id"])
        self.assertEqual(resumed["contract"]["objective"], "Build the requested artifact")
        self.assertFalse(resumed["restored_business_files"])

    def test_gate_lease_ticket_and_one_shot_consumption(self):
        contract, _ = load_reconciled(self.store, self.contract_id)
        binding = new_id()
        lease = issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": contract["version"],
                "event_head_hash": contract["event_head_hash"],
                "binding_id": binding,
                "turn_or_prompt_id": "turn-3",
                "allowed_scopes": ["project-files"],
            },
        )
        ticket = issue_ticket(
            self.store,
            {
                "lease_id": lease["lease_id"],
                "binding_id": binding,
                "action_class": "destructive_local",
                "action_summary": "Replace generated file",
                "intent_refs": [self.contract["objective"]["id"]],
                "affected_scopes": ["project-files"],
                "targets": ["dist/result.json"],
            },
        )
        consumed = consume_ticket(self.store, ticket["ticket_id"], ["dist/result.json"])
        self.assertIsNotNone(consumed["consumed_at"])
        with self.assertRaises(GateBlocked):
            consume_ticket(self.store, ticket["ticket_id"], ["dist/result.json"])

    def test_ticket_is_one_shot_under_concurrent_consumers(self):
        contract, _ = load_reconciled(self.store, self.contract_id)
        binding = new_id()
        lease = issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": contract["version"],
                "event_head_hash": contract["event_head_hash"],
                "binding_id": binding,
                "turn_or_prompt_id": "turn-concurrent",
                "allowed_scopes": ["project-files"],
            },
        )
        ticket = issue_ticket(
            self.store,
            {
                "lease_id": lease["lease_id"],
                "binding_id": binding,
                "action_class": "release",
                "action_summary": "Release the verified package",
                "intent_refs": [self.contract["objective"]["id"]],
                "affected_scopes": ["release"],
                "targets": ["dist/package.whl"],
            },
        )

        def consume():
            try:
                consume_ticket(self.store, ticket["ticket_id"], ["dist/package.whl"])
                return "ok"
            except GateBlocked:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: consume(), range(2)))
        self.assertEqual(sorted(outcomes), ["blocked", "ok"])

    def test_contract_update_invalidates_lease_and_read_only_hook_needs_none(self):
        contract, _ = load_reconciled(self.store, self.contract_id)
        binding = new_id()
        lease = issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": contract["version"],
                "event_head_hash": contract["event_head_hash"],
                "binding_id": binding,
                "turn_or_prompt_id": "turn-3",
                "allowed_scopes": ["project-files"],
            },
        )
        self.assertTrue(handle_hook(self.store, "codex", "pre-tool-use", {"action_class": "read"})["allow"])
        observed = handle_hook(
            self.store,
            "codex",
            "session-start",
            {"context_id": "thread-123", "contract_id": self.contract_id},
        )
        self.assertEqual(observed["binding"]["contract_id"], self.contract_id)
        self.assertTrue((self.store.state_root / "bindings" / (observed["binding"]["binding_id"] + ".json")).exists())
        apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "ADD",
                "entity_kind": "constraint",
                "after": "Preserve compatibility",
                "source": {"kind": "user"},
                "source_ref": "turn-4",
                "expected_version": contract["version"],
                "idempotency_key": "turn-4-add",
            },
        )
        with self.assertRaises(GateBlocked):
            handle_hook(
                self.store,
                "codex",
                "pre-tool-use",
                {"action_class": "other_local_write", "lease_id": lease["lease_id"], "binding_id": binding, "scope": "project-files"},
            )

    def test_verify_requires_all_criteria_and_can_complete(self):
        criterion = self.contract["acceptance_criteria"][0]
        failed = verify_result(
            self.store,
            {"contract_id": self.contract_id, "expected_version": 1, "criteria": [], "idempotency_key": "verify-1"},
        )
        self.assertFalse(failed["passed"])
        current, _ = load_reconciled(self.store, self.contract_id)
        passed = verify_result(
            self.store,
            {
                "contract_id": self.contract_id,
                "expected_version": current["version"],
                "criteria": [{"criterion_id": criterion["id"], "status": "pass", "evidence": "unittest output"}],
                "idempotency_key": "verify-2",
                "complete": True,
            },
        )
        self.assertTrue(passed["passed"])
        self.assertEqual(passed["contract"]["status"], "completed")

    def test_paused_contract_hook_bypasses_intentrail_gate(self):
        contract, _ = load_reconciled(self.store, self.contract_id)
        apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "PAUSE",
                "entity_kind": "contract",
                "entity_id": self.contract_id,
                "source": {"kind": "user"},
                "source_ref": "pause-test",
                "expected_version": contract["version"],
                "idempotency_key": "pause-test",
            },
        )
        decision = handle_hook(
            self.store,
            "codex",
            "pre-tool-use",
            {"action_class": "destructive_local", "contract_id": self.contract_id, "targets": ["output.txt"]},
        )
        self.assertTrue(decision["allow"])
        self.assertEqual(decision["reason"], "intentrail-paused")

    def test_dormant_project_does_not_block_actions_or_create_contract(self):
        dormant_root = self.root / "dormant"
        dormant_root.mkdir()
        dormant = StateStore(dormant_root)
        dormant.init()
        decision = handle_hook(
            dormant,
            "codex",
            "pre-tool-use",
            {"action_class": "other_local_write", "scope": "project-files"},
        )
        self.assertTrue(decision["allow"])
        self.assertEqual(decision["reason"], "intentrail-dormant")
        self.assertEqual(dormant.load_index()["contracts"], [])

    def test_precedents_require_explicit_confirmation_and_revoke(self):
        with self.assertRaises(UsageError):
            confirm_precedent(self.store, {"text": "Prefer JSON"})
        item = confirm_precedent(self.store, {"text": "Prefer JSON", "confirmed_by_user": True})
        self.assertEqual(list_precedents(self.store)["items"][0]["status"], "active")
        revoked = revoke_precedent(self.store, item["id"])
        self.assertEqual(revoked["status"], "revoked")
