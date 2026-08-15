import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .helpers import PROJECT_ROOT, StoreCase
from intentrail_core.contracts import create_contract
from intentrail_core.errors import SensitiveContent
from intentrail_core.handoff import export_handoff, import_handoff, inspect_handoff
from intentrail_core.state import StateStore


class HandoffAndCliTests(StoreCase):
    def test_c1_export_inspect_and_merge_is_non_mutating(self):
        artifact = self.root / "src" / "result.txt"
        artifact.parent.mkdir()
        artifact.write_text("verified result", encoding="utf-8")
        contract = create_contract(
            self.store,
            {
                "objective": "Prepare a verified result",
                "evidence": [
                    {
                        "path": "src/result.txt",
                        "summary": "Result artifact",
                        "verification_status": "unverified",
                    }
                ],
            },
        )["contract"]
        contract_id = contract["contract_id"]
        output = self.root / "handoff.json"
        exported = export_handoff(self.store, contract_id, output, "c1", True)
        self.assertTrue(Path(exported["sidecar"]).exists())
        inspected = inspect_handoff(output)
        self.assertTrue(inspected["valid"])
        self.assertEqual(inspected["handoff"]["evidence"][0]["verification_status"], "verified")
        before = self.store.load_contract(contract_id)["version"]
        merged = import_handoff(self.store, output, merge=True)
        self.assertFalse(merged["written"])
        self.assertEqual(self.store.load_contract(contract_id)["version"], before)

    def test_new_contract_import_is_paused_inferred_and_untrusted(self):
        output = self.root / "handoff.json"
        export_handoff(self.store, self.contract_id, output, "c2", True)
        with tempfile.TemporaryDirectory() as destination:
            target = StateStore(destination)
            target.init()
            imported = import_handoff(target, output, new_contract=True)
            contract = imported["contract"]
            self.assertEqual(contract["status"], "paused")
            self.assertEqual(contract["objective"]["state"], "inferred")
            self.assertEqual(contract["objective"]["source"]["kind"], "external_untrusted_content")

    def test_secret_and_path_traversal_are_blocked(self):
        secret = create_contract(
            self.store,
            {
                "objective": "Use token sk-abcdefghijklmnopqrstuvwxyz123456",
                "source": {"kind": "user"},
                "source_ref": "secret-test",
            },
        )["contract"]
        with self.assertRaises(SensitiveContent):
            export_handoff(self.store, secret["contract_id"], self.root / "secret.json", "c2", True)

        unsafe = create_contract(
            self.store,
            {
                "objective": "Unsafe evidence",
                "evidence": [{"path": "../secret.txt", "summary": "bad", "verification_status": "unverified"}],
            },
        )["contract"]
        with self.assertRaises(SensitiveContent):
            export_handoff(self.store, unsafe["contract_id"], self.root / "unsafe.json", "c1", True)

    def test_cli_json_envelope_and_usage_failure(self):
        cli = PROJECT_ROOT / "skills" / "intentrail" / "scripts" / "intentrail.py"
        completed = subprocess.run(
            [sys.executable, str(cli), "version", "--json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        envelope = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["schema_version"], "2.0.0")

        failed = subprocess.run(
            [sys.executable, str(cli), "status", "--root", str(self.root / "missing"), "--json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        error = json.loads(failed.stdout)
        self.assertEqual(failed.returncode, 3)
        self.assertEqual(error["error"]["code"], "STATE_NOT_FOUND")

    def test_cli_progress_requires_an_explicit_concurrency_guard(self):
        cli = PROJECT_ROOT / "skills" / "intentrail" / "scripts" / "intentrail.py"
        payload = self.root / "progress.json"
        payload.write_text(
            json.dumps(
                {
                    "current_stage": "implementation",
                    "next_material_action": "Implement the reconciled route",
                    "expected_version": self.contract["version"],
                    "idempotency_key": "cli-progress-1",
                }
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, str(cli), "progress", "--input", str(payload), "--root", str(self.root), "--json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        envelope = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["contract"]["current_stage"], "implementation")

        payload.write_text(json.dumps({"current_stage": "verification"}), encoding="utf-8")
        rejected = subprocess.run(
            [sys.executable, str(cli), "progress", "--input", str(payload), "--root", str(self.root), "--json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        error = json.loads(rejected.stdout)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(error["error"]["code"], "USAGE_ERROR")
