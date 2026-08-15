"""Project discovery and canonical .intentrail filesystem layout."""

import os
import shutil
from pathlib import Path

from .constants import DEFAULT_CONFIG, SCHEMA_VERSION
from .errors import IntentConflict, StateNotFound, UsageError
from .locks import FileLock
from .util import atomic_write_json, ensure_schema_compatible, new_id, read_json, utc_now


class StateStore:
    def __init__(self, project_root):
        self.project_root = Path(project_root).resolve()
        self.state_root = self.project_root / ".intentrail"

    @classmethod
    def discover(cls, explicit_root=None, start=None, require_initialized=True):
        if explicit_root:
            root = Path(explicit_root).resolve()
            store = cls(root)
            if require_initialized and not store.state_root.is_dir():
                raise StateNotFound("IntentRail is not initialized at {0}".format(root))
            return store
        current = Path(start or os.getcwd()).resolve()
        for candidate in [current] + list(current.parents):
            if (candidate / ".intentrail").is_dir():
                return cls(candidate)
        if not require_initialized:
            for candidate in [current] + list(current.parents):
                if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
                    return cls(candidate)
            return cls(current)
        raise StateNotFound(
            "No .intentrail directory was found.",
            recovery_actions=["Run intentrail init in the project root."],
        )

    @property
    def index_path(self):
        return self.state_root / "index.json"

    @property
    def config_path(self):
        return self.state_root / "config.json"

    @property
    def precedents_path(self):
        return self.state_root / "precedents.json"

    def project_lock(self):
        return FileLock(self.state_root / ".project.lock")

    def contract_dir(self, contract_id):
        return self.state_root / "contracts" / contract_id

    def contract_path(self, contract_id):
        return self.contract_dir(contract_id) / "contract.json"

    def events_path(self, contract_id):
        return self.contract_dir(contract_id) / "events.jsonl"

    def contract_lock(self, contract_id):
        return FileLock(self.contract_dir(contract_id) / ".lock")

    def init(self, scope="repo"):
        if scope not in {"repo", "user"}:
            raise UsageError("scope must be repo or user")
        self.state_root.mkdir(parents=True, exist_ok=True)
        (self.state_root / "contracts").mkdir(exist_ok=True)
        (self.state_root / "bindings").mkdir(exist_ok=True)
        (self.state_root / "runtime").mkdir(exist_ok=True)
        created = []
        now = utc_now()
        with self.project_lock():
            if not self.index_path.exists():
                atomic_write_json(
                    self.index_path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "project_id": new_id(),
                        "created_at": now,
                        "updated_at": now,
                        "contracts": [],
                    },
                )
                created.append("index.json")
            index = self.load_index()
            if not self.config_path.exists():
                config = dict(DEFAULT_CONFIG)
                config["scope"] = scope
                atomic_write_json(self.config_path, config)
                created.append("config.json")
            if not self.precedents_path.exists():
                atomic_write_json(
                    self.precedents_path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "project_id": index["project_id"],
                        "updated_at": now,
                        "items": [],
                    },
                )
                created.append("precedents.json")
        exclude_changed = self._ensure_local_git_exclude()
        return {
            "project_root": str(self.project_root),
            "state_root": str(self.state_root),
            "created": created,
            "git_exclude_updated": exclude_changed,
        }

    def _ensure_local_git_exclude(self):
        git = self.project_root / ".git"
        if not git.is_dir():
            return False
        info = git / "info"
        exclude = info / "exclude"
        info.mkdir(parents=True, exist_ok=True)
        marker = "/.intentrail/ # intentrail-owned"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if marker in existing:
            return False
        with exclude.open("a", encoding="utf-8", newline="\n") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(marker + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def load_index(self):
        try:
            index = read_json(self.index_path)
        except FileNotFoundError:
            raise StateNotFound("IntentRail project index is missing.")
        ensure_schema_compatible(index)
        return index

    def save_index(self, index):
        index["updated_at"] = utc_now()
        atomic_write_json(self.index_path, index)

    def load_config(self):
        try:
            config = read_json(self.config_path)
        except FileNotFoundError:
            raise StateNotFound("IntentRail config is missing.")
        ensure_schema_compatible(config)
        return config

    def save_config(self, config):
        atomic_write_json(self.config_path, config)

    def update_config(self, changes):
        with self.project_lock():
            config = self.load_config()
            config.update(changes)
            self.save_config(config)
        return config

    def load_contract(self, contract_id):
        try:
            contract = read_json(self.contract_path(contract_id))
        except FileNotFoundError:
            raise StateNotFound("Contract not found: {0}".format(contract_id))
        ensure_schema_compatible(contract)
        return contract

    def list_contracts(self):
        return list(self.load_index().get("contracts", []))

    def resolve_contract_id(self, explicit=None):
        index = self.load_index()
        ids = {entry["contract_id"] for entry in index.get("contracts", [])}
        if explicit:
            if explicit not in ids:
                raise StateNotFound("Contract not found: {0}".format(explicit))
            return explicit
        selected = self.load_config().get("selected_contract_id")
        if selected in ids:
            return selected
        active = [entry["contract_id"] for entry in index.get("contracts", []) if entry.get("status") == "active"]
        if len(active) == 1:
            return active[0]
        if not active:
            raise StateNotFound("No active contract is available.")
        raise IntentConflict(
            "Multiple active contracts exist; select one explicitly.",
            details={"contract_ids": active},
            recovery_actions=["Run intentrail contract select CONTRACT_ID."],
        )

    def select_contract(self, contract_id):
        self.resolve_contract_id(contract_id)
        with self.project_lock():
            config = self.load_config()
            config["selected_contract_id"] = contract_id
            self.save_config(config)
        return {"selected_contract_id": contract_id}

    def upsert_contract_index(self, contract):
        with self.project_lock():
            index = self.load_index()
            entries = index.setdefault("contracts", [])
            summary = {
                "contract_id": contract["contract_id"],
                "title": contract.get("title") or _objective_text(contract) or "Untitled task",
                "status": contract["status"],
                "version": contract["version"],
                "updated_at": contract["updated_at"],
            }
            for position, entry in enumerate(entries):
                if entry.get("contract_id") == contract["contract_id"]:
                    preserved = dict(entry)
                    preserved.update(summary)
                    entries[position] = preserved
                    break
            else:
                entries.append(summary)
            self.save_index(index)

    def create_backup(self, paths, label):
        stamp = utc_now().replace(":", "").replace("-", "")
        backup_root = self.state_root / "backups" / "{0}-{1}".format(stamp, label)
        backup_root.mkdir(parents=True, exist_ok=False)
        copied = []
        for source in paths:
            source = Path(source)
            if not source.exists():
                continue
            target = backup_root / source.name
            if source.is_dir():
                shutil.copytree(str(source), str(target))
            else:
                shutil.copy2(str(source), str(target))
            copied.append(str(target))
        return {"backup_root": str(backup_root), "files": copied}


def _objective_text(contract):
    objective = contract.get("objective")
    return objective.get("text") if isinstance(objective, dict) else None
