import json
import unittest
import warnings
from pathlib import Path

from .helpers import PROJECT_ROOT, StoreCase
from intentrail_core.bindings import bind_context
from intentrail_core.checkpoint import create_checkpoint
from intentrail_core.gates import issue_lease, issue_ticket
from intentrail_core.handoff import export_handoff
from intentrail_core.util import new_id, read_json

try:
    import jsonschema
except ImportError:
    jsonschema = None

try:
    from referencing import Registry, Resource
except ImportError:
    Registry = None
    Resource = None


@unittest.skipIf(jsonschema is None, "jsonschema is an optional test dependency")
class SchemaTests(StoreCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema_root = PROJECT_ROOT / "schemas"
        cls.schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in cls.schema_root.glob("*.json")
        }
        cls.registry = None
        if Registry is not None:
            cls.registry = Registry().with_resources(
                (document["$id"], Resource.from_contents(document))
                for document in cls.schemas.values()
            )

    def validate_as(self, document, schema_name):
        schema = self.schemas[schema_name]
        if self.registry is not None:
            validator = jsonschema.Draft202012Validator(schema, registry=self.registry, format_checker=jsonschema.FormatChecker())
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                resolver = jsonschema.RefResolver(base_uri=self.schema_root.as_uri() + "/", referrer=schema)
            validator = jsonschema.Draft202012Validator(schema, resolver=resolver, format_checker=jsonschema.FormatChecker())
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
        self.assertEqual(errors, [], [error.message for error in errors])

    def test_generated_state_documents_match_frozen_schemas(self):
        self.validate_as(read_json(self.store.index_path), "project-index.schema.json")
        self.validate_as(read_json(self.store.config_path), "config.schema.json")
        self.validate_as(read_json(self.store.precedents_path), "precedent.schema.json")
        self.validate_as(self.store.load_contract(self.contract_id), "contract.schema.json")
        first_event = json.loads(self.store.events_path(self.contract_id).read_text(encoding="utf-8").splitlines()[0])
        self.validate_as(first_event, "event.schema.json")
        self.validate_as(
            {
                "base_version": 1,
                "idempotency_key": "schema-reconcile",
                "source": {"kind": "user"},
                "source_ref": "turn-schema",
                "changes": [{"operation": "ADD", "entity_kind": "constraint", "after": "Keep compatibility"}],
            },
            "reconciliation.schema.json",
        )

        binding = bind_context(self.store, "codex", {"context_id": "schema-thread", "contract_id": self.contract_id})
        self.validate_as(binding, "binding.schema.json")

        checkpoint = create_checkpoint(self.store, self.contract_id, "schema-test")["checkpoint"]
        self.validate_as(checkpoint, "checkpoint.schema.json")
        checkpoint_index = read_json(self.store.contract_dir(self.contract_id) / "checkpoints" / "index.json")
        self.validate_as(checkpoint_index, "checkpoint-index.schema.json")

        current = self.store.load_contract(self.contract_id)
        binding_id = new_id()
        lease = issue_lease(
            self.store,
            {
                "decision": "PASS",
                "contract_id": self.contract_id,
                "contract_version": current["version"],
                "event_head_hash": current["event_head_hash"],
                "binding_id": binding_id,
                "turn_or_prompt_id": "schema-turn",
                "allowed_scopes": ["project-files"],
            },
        )
        self.validate_as(lease, "gate-lease.schema.json")
        ticket = issue_ticket(
            self.store,
            {
                "lease_id": lease["lease_id"],
                "binding_id": binding_id,
                "action_class": "release",
                "action_summary": "Publish package",
                "intent_refs": [self.contract["objective"]["id"]],
                "affected_scopes": ["release"],
                "targets": ["dist/intentrail.whl"],
            },
        )
        self.validate_as(ticket, "action-ticket.schema.json")

        output = self.root / "schema-handoff.json"
        package = export_handoff(self.store, self.contract_id, output, "c2", True)["handoff"]
        self.validate_as(package, "handoff.schema.json")

        self.validate_as(
            {
                "schema_version": "1.0.0",
                "owner": "intentrail-owned",
                "product_version": "0.5.0",
                "scope": "repo",
                "root": str(self.root),
                "hosts": ["codex"],
                "cli_path": str(self.root / "bin" / "intentrail"),
                "runtime_backend": "managed-cli",
                "installed": [],
                "configs": [],
                "last_backup": None,
                "updated_at": "2026-08-15T00:00:00Z",
            },
            "install-manifest.schema.json",
        )

        envelope = {
            "schema_version": "2.0.0",
            "ok": True,
            "command": "version",
            "exit_code": 0,
            "message": "IntentRail version",
            "data": {"product_version": "0.2.0"},
            "warnings": [],
            "error": None,
        }
        self.validate_as(envelope, "cli-envelope.schema.json")
