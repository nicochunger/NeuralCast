"""Station locks and atomic audition documents outside live station media."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from pydantic import BaseModel

from neuralcast.config import RUNTIME_ROOT, station_dir_from_slug
from .models import Batch, State


class ConflictError(RuntimeError):
    """An operation conflicts with an active or changed review."""


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict | BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".discovery-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConflictError(
                "Another operation is running; try again shortly"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, station: str, root: Path | None = None) -> None:
        station_dir_from_slug(station)  # Validate before using station in a path.
        self.station = station
        self.root = (root or RUNTIME_ROOT / "music_discovery") / station

    def lock(self):
        return exclusive_lock(self.root / "station.lock")

    def state(self) -> State:
        path = self.root / "state.json"
        return State.model_validate_json(path.read_text()) if path.exists() else State()

    def save_state(self, state: State) -> None:
        atomic_json(self.root / "state.json", state)

    def batch_dir(self, batch_id: str) -> Path:
        from pydantic import TypeAdapter
        from .models import Identifier

        TypeAdapter(Identifier).validate_python(batch_id)
        return self.root / "batches" / batch_id

    def load_batch(self, batch_id: str) -> Batch:
        batch = Batch.model_validate_json(
            (self.batch_dir(batch_id) / "batch.json").read_text()
        )
        if batch.id != batch_id or batch.station != self.station:
            raise ValueError("Batch identity mismatch")
        return batch

    def current(self) -> Batch | None:
        batch_id = self.state().current_batch_id
        return self.load_batch(batch_id) if batch_id else None

    def save_batch(self, batch: Batch) -> None:
        atomic_json(self.batch_dir(batch.id) / "batch.json", batch)

    def audio_path(self, batch: Batch, candidate_id: str) -> Path:
        if candidate_id not in {candidate.id for candidate in batch.candidates}:
            raise ValueError("Unknown suggestion")
        return self.batch_dir(batch.id) / "audio" / f"{candidate_id}.mp3"

    def verified_audio(self, batch: Batch, candidate_id: str) -> Path:
        path = self.audio_path(batch, candidate_id)
        receipt = batch.auditions.get(candidate_id)
        if (
            receipt is None
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != receipt.size
            or file_hash(path) != receipt.sha256
        ):
            raise ValueError("Audition audio is missing or changed")
        return path

    def artwork_path(self, batch: Batch, candidate_id: str) -> Path:
        self.audio_path(batch, candidate_id)  # Validate membership before path use.
        return self.batch_dir(batch.id) / "artwork" / f"{candidate_id}.image"
