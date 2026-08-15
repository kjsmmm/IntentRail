import json

from .helpers import StoreCase
from intentrail_core.contracts import apply_change, load_reconciled
from intentrail_core.errors import GateBlocked, UsageError
from intentrail_core.gates import issue_lease, issue_ticket
from intentrail_core.migrate import migrate
from intentrail_core.reconcile import apply_reconciliation
from intentrail_core.util import atomic_write_json, sha256_value
from intentrail_core.validate import validate_project


class ReconciliationV2Tests(StoreCase):
    def test_batch_is_atomic_grouped_and_updates_projection(self):
        result = apply_reconciliation(
            self.store,
            {
                "base_version": 1,
                "idempotency_key": "turn-2-batch",
                "source": {"kind": "user"},
                "source_ref": "turn-2",
                "changes": [
                    {"operation": "ADD", "entity_kind": "deliverable", "after": "A CLI"},
                    {"operation": "ADD", "entity_kind": "constraint", "after": {"text": "No third-party runtime dependencies", "scope": "runtime"}},
                ],
            },
        )
        self.assertEqual(result["contract"]["version"], 3)
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(all(event["reconciliation_id"] == result["reconciliation_id"] for event in result["events"]))
        self.assertEqual([event["reconciliation_index"] for event in result["events"]], [1, 2])
        duplicate = apply_reconciliation(
            self.store,
            {
                "base_version": 1,
                "idempotency_key": "turn-2-batch",
                "source": {"kind": "user"},
                "source_ref": "turn-2",
                "changes": [{"operation": "ADD", "entity_kind": "deliverable", "after": "ignored retry"}],
            },
        )
        self.assertTrue(duplicate["duplicate"])

    def test_invalid_batch_writes_nothing(self):
        before, events_before = load_reconciled(self.store, self.contract_id)
        with self.assertRaises(UsageError):
            apply_reconciliation(
                self.store,
                {
                    "base_version": 1,
                    "idempotency_key": "invalid-batch",
                    "source": {"kind": "user"},
                    "source_ref": "turn-invalid",
                    "changes": [
                        {"operation": "ADD", "entity_kind": "deliverable", "after": "Would be partial"},
                        {"operation": "MODIFY", "entity_kind": "constraint", "target_id": "missing", "after": "Invalid"},
                    ],
                },
            )
        after, events_after = load_reconciled(self.store, self.contract_id)
        self.assertEqual(after, before)
        self.assertEqual(events_after, events_before)

    def test_explicit_dependency_becomes_stale_and_unlinked_scope_needs_review(self):
        constraint = self.contract["constraints"][0]
        explicit = apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "ADD",
                "entity_kind": "decision",
                "after": {"text": "Use the system parser", "scope": "runtime", "depends_on": [constraint["id"]]},
                "source": {"kind": "agent"},
                "source_ref": "decision-1",
                "expected_version": 1,
                "idempotency_key": "decision-1",
            },
        )["contract"]
        apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "ADD",
                "entity_kind": "decision",
                "after": {"text": "Use a runtime cache", "scope": "runtime"},
                "source": {"kind": "agent"},
                "source_ref": "decision-2",
                "expected_version": explicit["version"],
                "idempotency_key": "decision-2",
            },
        )
        result = apply_reconciliation(
            self.store,
            {
                "base_version": 3,
                "idempotency_key": "turn-change-runtime",
                "source": {"kind": "user"},
                "source_ref": "turn-4",
                "changes": [
                    {"operation": "MODIFY", "entity_kind": "constraint", "target_id": constraint["id"], "after": {"text": "Allow one vetted parser", "scope": "runtime"}}
                ],
            },
        )
        lifecycles = {item["text"]: item["lifecycle"] for item in result["contract"]["decisions"]}
        self.assertEqual(lifecycles["Use the system parser"], "stale")
        self.assertEqual(lifecycles["Use a runtime cache"], "needs_review")
        self.assertEqual(len(result["impact"]["stale"]), 1)
        self.assertEqual(len(result["impact"]["needs_review"]), 1)

    def test_high_risk_action_basis_rejects_superseded_intent(self):
        old_objective = self.contract["objective"]["id"]
        changed = apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "MODIFY",
                "entity_kind": "objective",
                "entity_id": old_objective,
                "after": "Build the revised artifact",
                "source": {"kind": "user"},
                "source_ref": "turn-2",
                "expected_version": 1,
                "idempotency_key": "objective-replace",
            },
        )["contract"]
        lease = issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": changed["version"],
                "event_head_hash": changed["event_head_hash"],
                "binding_id": "2b36bc31-e3f0-4578-ad69-a2ef9e330033",
                "turn_or_prompt_id": "turn-2",
                "allowed_scopes": ["external-systems"],
            },
        )
        with self.assertRaises(GateBlocked):
            issue_ticket(
                self.store,
                {
                    "lease_id": lease["lease_id"],
                    "binding_id": lease["binding_id"],
                    "action_class": "release",
                    "action_summary": "Release based on the old objective",
                    "intent_refs": [old_objective],
                    "affected_scopes": ["release"],
                    "targets": ["dist/package.zip"],
                },
            )

    def test_material_correction_clears_cursor_and_progress_replaces_it(self):
        progressed = apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "PROGRESS",
                "entity_kind": "contract",
                "entity_id": self.contract_id,
                "after": {"current_stage": "implementation", "next_material_action": "Implement the standard-library route"},
                "source": {"kind": "agent"},
                "source_ref": "plan-1",
                "expected_version": 1,
                "idempotency_key": "progress-1",
            },
        )["contract"]
        constraint = progressed["constraints"][0]
        changed = apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "MODIFY",
                "entity_kind": "constraint",
                "entity_id": constraint["id"],
                "after": {"text": "Allow one vetted dependency", "scope": constraint["scope"]},
                "source": {"kind": "user"},
                "source_ref": "turn-3",
                "expected_version": progressed["version"],
                "idempotency_key": "constraint-change-clears-cursor",
            },
        )["contract"]
        self.assertIsNone(changed["next_material_action"])
        replacement = apply_change(
            self.store,
            self.contract_id,
            {
                "operation": "PROGRESS",
                "entity_kind": "contract",
                "entity_id": self.contract_id,
                "after": {"next_material_action": "Evaluate the vetted dependency"},
                "source": {"kind": "agent"},
                "source_ref": "plan-2",
                "expected_version": changed["version"],
                "idempotency_key": "progress-2",
            },
        )["contract"]
        self.assertEqual(replacement["next_material_action"], "Evaluate the vetted dependency")

    def test_route_changing_lease_can_carry_valid_action_basis(self):
        contract, _ = load_reconciled(self.store, self.contract_id)
        lease = issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": contract["version"],
                "event_head_hash": contract["event_head_hash"],
                "binding_id": "a79948f2-0835-4fa7-8e98-908f7918983a",
                "turn_or_prompt_id": "turn-route",
                "allowed_scopes": ["project-files"],
                "action_summary": "Implement the route supported by the active objective",
                "intent_refs": [contract["objective"]["id"]],
                "affected_scopes": ["implementation"],
            },
        )
        self.assertEqual(lease["intent_refs"], [contract["objective"]["id"]])

    def test_v1_state_migrates_with_backup_and_rebuilt_hash_chain(self):
        _downgrade_fixture_to_v1(self.store)
        result = migrate(self.store, "2.0.0")
        self.assertTrue(result["changed"])
        self.assertTrue(result["backup"]["files"])
        self.assertTrue(validate_project(self.store)["valid"])
        contract, events = load_reconciled(self.store, self.contract_id)
        self.assertEqual(contract["schema_version"], "2.0.0")
        self.assertEqual(contract["objective"]["certainty"], "confirmed")
        self.assertIn("reconciliation_id", events[0])


def _downgrade_fixture_to_v1(store):
    contract_id = store.resolve_contract_id()
    for path in [store.index_path, store.config_path, store.precedents_path, store.contract_path(contract_id)]:
        document = json.loads(path.read_text(encoding="utf-8"))
        _strip_v2(document)
        document["schema_version"] = "1.0.0"
        atomic_write_json(path, document)
    events_path = store.events_path(contract_id)
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    previous = None
    for event in events:
        _strip_v2(event)
        event["schema_version"] = "1.0.0"
        event.pop("reconciliation_id", None)
        event.pop("reconciliation_index", None)
        event.pop("reconciliation_size", None)
        event["previous_hash"] = previous
        event.pop("event_hash", None)
        event["event_hash"] = sha256_value(event)
        previous = event["event_hash"]
    events_path.write_text("".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events), encoding="utf-8")


def _strip_v2(value):
    if isinstance(value, dict):
        value.pop("certainty", None)
        value.pop("lifecycle", None)
        value.pop("depends_on", None)
        value.pop("invalidated_by", None)
        for child in value.values():
            _strip_v2(child)
    elif isinstance(value, list):
        for child in value:
            _strip_v2(child)
