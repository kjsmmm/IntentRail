import json
import io
from contextlib import redirect_stdout

from .helpers import StoreCase
from intentrail_core.checkpoint import create_checkpoint, list_checkpoints, resume_contract, show_checkpoint
from intentrail_core.contracts import apply_change, diff_versions, load_reconciled
from intentrail_core.errors import SensitiveContent
from intentrail_core.handoff import export_handoff, inspect_handoff
from intentrail_core.migrate import migrate
from intentrail_core.validate import validate_project
from intentrail_core.cli import main


class OperationCoverageTests(StoreCase):
    def apply(self, operation, version, key, **values):
        payload = {
            "operation": operation,
            "entity_kind": values.pop("entity_kind", "contract"),
            "entity_id": values.pop("entity_id", self.contract_id),
            "source": values.pop("source", {"kind": "user"}),
            "source_ref": key,
            "expected_version": version,
            "idempotency_key": key,
        }
        payload.update(values)
        return apply_change(self.store, self.contract_id, payload)

    def test_defer_resolve_diff_pause_resume_and_migration(self):
        deferred = self.apply(
            "DEFER",
            1,
            "defer-1",
            entity_kind="question",
            entity_id=None,
            after={"text": "Choose the deployment region", "state": "assumed"},
        )["contract"]
        question = deferred["questions"][0]
        self.assertIn("deferred", question["tags"])

        resolved = self.apply(
            "RESOLVE",
            2,
            "resolve-1",
            entity_kind="question",
            entity_id=question["id"],
            after={"text": "Deploy in eu-west-1", "state": "confirmed"},
        )["contract"]
        self.assertEqual(resolved["questions"][0]["state"], "confirmed")
        self.assertNotEqual(resolved["questions"][0]["id"], question["id"])

        paused = self.apply("PAUSE", 3, "pause-1")["contract"]
        self.assertEqual(paused["status"], "paused")
        resumed = resume_contract(self.store, self.contract_id)
        self.assertEqual(resumed["contract"]["status"], "active")

        changes = diff_versions(self.store, self.contract_id, 1, 4)
        self.assertEqual([item["operation"] for item in changes["changes"]], ["DEFER", "RESOLVE", "PAUSE"])
        migration = migrate(self.store, "2.0.0")
        self.assertFalse(migration["changed"])

    def test_checkpoint_list_show_and_middle_corruption_fail_closed(self):
        checkpoint = create_checkpoint(self.store, self.contract_id, "coverage")["checkpoint"]
        listed = list_checkpoints(self.store, self.contract_id)
        self.assertEqual(listed["checkpoints"][0]["checkpoint_id"], checkpoint["checkpoint_id"])
        shown = show_checkpoint(self.store, checkpoint["checkpoint_id"])
        self.assertEqual(shown["checkpoint"]["checkpoint_hash"], checkpoint["checkpoint_hash"])

        path = self.store.events_path(self.contract_id)
        lines = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["source_ref"] = "tampered-middle-event"
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = validate_project(self.store, repair_tail=True)
        self.assertFalse(result["valid"])
        self.assertEqual(result["repairs"], [])

    def test_handoff_tampering_is_rejected(self):
        output = self.root / "handoff-tamper.json"
        export_handoff(self.store, self.contract_id, output, "c2", True)
        package = json.loads(output.read_text(encoding="utf-8"))
        package["next_action"] = "Injected action"
        output.write_text(json.dumps(package), encoding="utf-8")
        with self.assertRaises(SensitiveContent):
            inspect_handoff(output)

    def test_interaction_mode_has_a_deterministic_cli_control(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["mode", "strict", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["data"]["interaction_mode"], "strict")
        self.assertEqual(self.store.load_config()["interaction_mode"], "strict")
