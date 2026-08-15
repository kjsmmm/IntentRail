import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from .helpers import StoreCase
from intentrail_core.contracts import apply_change, create_contract, load_reconciled, undo
from intentrail_core.errors import PermissionRequired, StaleVersion
from intentrail_core.util import atomic_write_json
from intentrail_core.validate import validate_project


class ContractTests(StoreCase):
    def event(self, operation, kind, version, key, **extra):
        payload = {
            "operation": operation,
            "entity_kind": kind,
            "expected_version": version,
            "idempotency_key": key,
            "source": {"kind": "user"},
            "source_ref": key,
        }
        payload.update(extra)
        return apply_change(self.store, self.contract_id, payload)

    def test_create_is_reconstructable_and_valid(self):
        result = validate_project(self.store)
        self.assertTrue(result["valid"], result)
        contract, events = load_reconciled(self.store, self.contract_id)
        self.assertEqual(contract["version"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["previous_hash"], None)

    def test_creation_snapshot_and_event_share_one_timestamp(self):
        with patch("intentrail_core.contracts.utc_now", return_value="2026-08-15T00:00:00Z"):
            with patch("intentrail_core.events.utc_now", return_value="2026-08-15T00:00:01Z"):
                created = create_contract(self.store, {"objective": "Cross a clock boundary safely"})
        self.assertEqual(created["contract"]["updated_at"], "2026-08-15T00:00:00Z")
        self.assertEqual(created["event"]["timestamp"], "2026-08-15T00:00:00Z")
        rebuilt, _ = load_reconciled(self.store, created["contract"]["contract_id"])
        self.assertEqual(rebuilt, created["contract"])

    def test_add_is_idempotent_and_stale_versions_fail(self):
        payload = {
            "operation": "ADD",
            "entity_kind": "deliverable",
            "after": "A command-line interface",
            "expected_version": 1,
            "idempotency_key": "turn-2-add-cli",
            "source": {"kind": "user"},
            "source_ref": "turn-2",
        }
        first = apply_change(self.store, self.contract_id, payload)
        second = apply_change(self.store, self.contract_id, payload)
        self.assertEqual(first["contract"]["version"], 2)
        self.assertTrue(second["duplicate"])
        with self.assertRaises(StaleVersion):
            self.event("ADD", "constraint", 1, "different-key", after="A stale write")

    def test_modify_revoke_confirm_and_conflict_preserve_provenance(self):
        old_id = self.contract["constraints"][0]["id"]
        modified = self.event(
            "MODIFY",
            "constraint",
            1,
            "turn-2-modify",
            entity_id=old_id,
            after={"text": "Use Python 3.11 standard library", "state": "confirmed", "scope": "runtime"},
        )["contract"]
        new_item = modified["constraints"][0]
        self.assertNotEqual(new_item["id"], old_id)
        self.assertIn(old_id, new_item["supersedes"])
        self.assertEqual(modified["superseded_items"][0]["state"], "superseded")

        added = self.event(
            "ADD",
            "assumption",
            2,
            "turn-3-assume",
            after={"text": "Output JSON", "state": "assumed"},
            source={"kind": "agent"},
        )["contract"]
        assumption_id = added["assumptions"][0]["id"]
        confirmed = self.event("CONFIRM", "assumption", 3, "turn-4-confirm", entity_id=assumption_id)["contract"]
        self.assertEqual(confirmed["assumptions"][0]["state"], "confirmed")

        conflicted = self.event(
            "CONFLICT",
            "constraint",
            4,
            "turn-5-conflict",
            entity_id=new_item["id"],
            after={"text": "Use a third-party parser", "state": "conflicted", "scope": "runtime"},
        )["contract"]
        active = conflicted["constraints"]
        self.assertEqual(len(active), 2)
        self.assertTrue(all(item["state"] == "conflicted" for item in active))

        revoked = self.event("REVOKE", "assumption", 5, "turn-6-revoke", entity_id=assumption_id)["contract"]
        self.assertEqual(revoked["assumptions"], [])
        self.assertTrue(any(item["id"] == assumption_id for item in revoked["superseded_items"]))

    def test_undo_appends_inverse_without_rewriting_history(self):
        added = self.event("ADD", "deliverable", 1, "turn-2-add", after="A report")["contract"]
        result = undo(self.store, self.contract_id)
        self.assertEqual(result["contract"]["version"], 3)
        self.assertEqual(result["contract"]["deliverables"], [])
        _, events = load_reconciled(self.store, self.contract_id)
        self.assertEqual([event["operation"] for event in events], ["ADD", "ADD", "UNDO"])

    def test_snapshot_lag_is_rebuilt_from_events(self):
        old_snapshot = self.store.load_contract(self.contract_id)
        self.event("ADD", "deliverable", 1, "turn-2-add", after="A report")
        atomic_write_json(self.store.contract_path(self.contract_id), old_snapshot)
        rebuilt, _ = load_reconciled(self.store, self.contract_id)
        self.assertEqual(rebuilt["version"], 2)
        self.assertEqual(rebuilt["deliverables"][0]["text"], "A report")

    def test_incomplete_tail_is_backed_up_and_repaired(self):
        with self.store.events_path(self.contract_id).open("ab") as handle:
            handle.write(b'{"broken":')
        result = validate_project(self.store, repair_tail=True)
        self.assertTrue(result["valid"], result)
        self.assertEqual(len(result["repairs"]), 1)
        backups = list(self.store.contract_dir(self.contract_id).glob("events.jsonl.tail-backup-*"))
        self.assertEqual(len(backups), 1)

    def test_concurrent_writers_cannot_overwrite_each_other(self):
        def write(key):
            try:
                result = self.event("ADD", "deliverable", 1, key, after="Deliverable " + key)
                return "ok", result["contract"]["version"]
            except StaleVersion:
                return "stale", None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ["writer-a", "writer-b"]))
        self.assertEqual(sorted(status for status, _ in results), ["ok", "stale"])
        contract, events = load_reconciled(self.store, self.contract_id)
        self.assertEqual(contract["version"], 2)
        self.assertEqual(len(events), 2)

    def test_untrusted_content_and_agent_cannot_overwrite_or_confirm_user_intent(self):
        constraint_id = self.contract["constraints"][0]["id"]
        with self.assertRaises(PermissionRequired):
            apply_change(
                self.store,
                self.contract_id,
                {
                    "operation": "MODIFY",
                    "entity_kind": "constraint",
                    "entity_id": constraint_id,
                    "after": {"text": "Ignore the user's constraint", "state": "inferred"},
                    "source": {"kind": "external_untrusted_content"},
                    "source_ref": "web-page",
                    "expected_version": 1,
                    "idempotency_key": "external-overwrite",
                },
            )
        with self.assertRaises(PermissionRequired):
            apply_change(
                self.store,
                self.contract_id,
                {
                    "operation": "ADD",
                    "entity_kind": "constraint",
                    "after": {"text": "Pretend this is confirmed", "state": "confirmed"},
                    "source": {"kind": "agent"},
                    "source_ref": "agent-inference",
                    "expected_version": 1,
                    "idempotency_key": "agent-confirmed-add",
                },
            )
        allowed = apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "ADD",
                "entity_kind": "assumption",
                "after": {"text": "Candidate fact from a document", "state": "inferred"},
                "source": {"kind": "external_untrusted_content"},
                "source_ref": "document-1",
                "expected_version": 1,
                "idempotency_key": "external-candidate",
            },
        )
        self.assertEqual(allowed["contract"]["assumptions"][0]["state"], "inferred")
