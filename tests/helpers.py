import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from intentrail_core.contracts import create_contract
from intentrail_core.state import StateStore


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = StateStore(self.root)
        self.store.init()
        created = create_contract(
            self.store,
            {
                "objective": "Build the requested artifact",
                "constraints": ["Use the standard library"],
                "acceptance_criteria": ["The result passes its tests"],
                "source": {"kind": "user"},
                "source_ref": "test-user-turn-1",
            },
        )
        self.contract = created["contract"]
        self.contract_id = self.contract["contract_id"]

    def tearDown(self):
        self.temporary.cleanup()
