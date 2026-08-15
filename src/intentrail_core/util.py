"""Small deterministic utilities with no third-party dependencies."""

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .constants import SCHEMA_MAJOR
from .errors import MigrationRequired, UsageError


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if not isinstance(value, str):
        raise UsageError("Timestamp must be an RFC 3339 string.")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def new_id():
    return str(uuid.uuid4())


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_value(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UsageError("Invalid JSON file: {0}".format(path), details=str(exc))


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".{0}.".format(path.name), suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".{0}.".format(path.name), suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def append_jsonl(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_bytes(value) + b"\n"
    with path.open("ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def ensure_schema_compatible(document):
    version = document.get("schema_version") if isinstance(document, dict) else None
    if not isinstance(version, str):
        raise MigrationRequired("Document has no schema_version.")
    try:
        major = int(version.split(".", 1)[0])
    except (ValueError, IndexError):
        raise MigrationRequired("Invalid schema_version: {0}".format(version))
    if major != SCHEMA_MAJOR:
        raise MigrationRequired(
            "Unsupported schema major version: {0}".format(version),
            recovery_actions=["Run intentrail migrate with a compatible version."],
        )


_RELATIVE_PATH = re.compile(r"^(?![A-Za-z]:)(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\).+$")


def validate_relative_path(value):
    return isinstance(value, str) and bool(_RELATIVE_PATH.match(value))


def load_input(value):
    if value == "-":
        import sys

        raw = sys.stdin.read()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UsageError("stdin must contain exactly one JSON object.", details=str(exc))
    return read_json(value)


def project_relative(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise UsageError("Path is outside the project root: {0}".format(path))
