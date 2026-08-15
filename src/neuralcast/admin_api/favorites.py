"""Atomic, disk-backed favorites storage for the authenticated admin API."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from neuralcast.config import RUNTIME_ROOT

ADMIN_FAVORITES_PATH = RUNTIME_ROOT / "admin_http" / "favorites.json"
ADMIN_FAVORITES_LOCK_PATH = RUNTIME_ROOT / "admin_http" / "favorites.lock"


class FavoriteStore:
    """Read and write the single admin user's favorite tracks safely."""

    def __init__(
        self,
        path: Path = ADMIN_FAVORITES_PATH,
        lock_path: Path = ADMIN_FAVORITES_LOCK_PATH,
    ) -> None:
        self.path = path
        self.lock_path = lock_path

    def read(self) -> tuple[list[dict[str, Any]], bool]:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self._locked(fcntl.LOCK_SH):
            if not self.path.exists():
                return [], False
            raw = json.loads(self.path.read_text(encoding="utf-8"))

        if not isinstance(raw, list):
            raise ValueError("Favorites file must contain a JSON array.")

        return [entry for entry in raw if isinstance(entry, dict)], True

    def write(self, favorites: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self._locked(fcntl.LOCK_EX):
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                text=True,
            )
            try:
                with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                    json.dump(favorites, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

    def _locked(self, operation: int):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), operation)
        return _LockedFile(handle)


class _LockedFile:
    def __init__(self, handle) -> None:
        self.handle = handle

    def __enter__(self):
        return self.handle

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
