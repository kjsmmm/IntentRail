"""Cross-platform exclusive file locks for short state transactions."""

import json
import os
import time
from pathlib import Path

from .errors import StaleVersion
from .util import new_id, utc_now


class FileLock:
    def __init__(self, path, attempts=40, delay=0.05):
        self.path = Path(path)
        self.attempts = attempts
        self.delay = delay
        self.owner_id = new_id()
        self.acquired = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"owner_id": self.owner_id, "pid": os.getpid(), "created_at": utc_now()},
            sort_keys=True,
        ).encode("utf-8")
        for _ in range(self.attempts):
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, payload)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                time.sleep(self.delay)
        raise StaleVersion(
            "State is locked by another operation.",
            details={"lock": str(self.path)},
            recovery_actions=["Retry after the active operation finishes.", "Run validate if the owner is no longer active."],
        )

    def release(self):
        if not self.acquired:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        if current.get("owner_id") == self.owner_id:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback):
        self.release()
