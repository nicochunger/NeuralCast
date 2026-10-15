"""Atomic project receipts and explicit revision approvals."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from neuralcast.config import RUNTIME_ROOT, station_dir_from_slug
from .models import Identifier, Proposal
from pydantic import TypeAdapter


def identifier(value: str) -> str:
    return TypeAdapter(Identifier).validate_python(value)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Project:
    def __init__(
        self,
        station: str,
        episode_id: str,
        *,
        station_root: Path | None = None,
        runtime_root: Path | None = None,
    ):
        self.station = station
        self.id = identifier(episode_id)
        self.station_root = station_root or station_dir_from_slug(station)
        self.documents = self.station_root / "metadata" / "episodes" / self.id
        self.runtime = (runtime_root or RUNTIME_ROOT / "episodes") / station / self.id

    def proposal(self) -> Proposal:
        result = Proposal.model_validate(read_json(self.documents / "proposal.json"))
        if result.episode_id != self.id:
            raise ValueError("Proposal ID does not match project")
        return result

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.runtime.mkdir(parents=True, exist_ok=True)
        with (self.runtime / "production.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another episode operation is running") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def approve_proposal(self, expected_hash: str) -> None:
        actual = digest(self.proposal().model_dump())
        if actual != expected_hash:
            raise ValueError(
                "Proposal changed; review the current proposal before approving"
            )
        write_json(self.runtime / "proposal_approval.json", {"proposal_hash": actual})

    def approved_proposal(self) -> tuple[Proposal, str]:
        proposal = self.proposal()
        actual = digest(proposal.model_dump())
        approval = read_json(self.runtime / "proposal_approval.json")
        if approval.get("proposal_hash") != actual:
            raise ValueError("Current proposal has not been approved")
        return proposal, actual
