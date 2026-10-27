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
from .models import EpisodeNumber, Identifier, Proposal
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
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, value: str) -> None:
    """Atomically replace a review document or receipt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
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
        self.runtime = (runtime_root or RUNTIME_ROOT / "episodes") / station / self.id

    @property
    def documents(self) -> Path:
        """Resolve numbered folders without changing the stable episode slug."""
        root = self.station_root / "metadata" / "episodes"
        legacy = root / self.id
        matches = [legacy] if legacy.exists() else []
        matches.extend(
            path
            for path in root.glob(f"*-{self.id}")
            if path.is_dir() and path.name[: -len(self.id) - 1].isdigit()
        )
        if len(matches) > 1:
            raise ValueError(f"Multiple document folders for episode {self.id}")
        return matches[0] if matches else legacy

    @property
    def episode_number(self) -> int | None:
        directory = self.documents
        path = directory / "proposal.json"
        if not path.exists():
            if directory.name != self.id:
                raise ValueError("Numbered episode folder needs proposal.json")
            return None
        proposal = self.proposal()
        number = proposal.episode_number
        if number is None:
            if directory.name != self.id:
                raise ValueError(
                    "Numbered episode folder needs episode_number in proposal.json"
                )
            return None
        if directory.name != self.id and directory.name != (f"{number:03d}-{self.id}"):
            raise ValueError("Episode folder does not match its number")
        return number

    def set_episode_number(self, number: int) -> Path:
        """Caller holds project lock; assign a station-local publication number."""
        number = TypeAdapter(EpisodeNumber).validate_python(number)
        current = self.documents
        existing = self.episode_number
        if existing is not None and existing != number:
            raise ValueError("Episode already has a different number")
        root = current.parent
        for path in root.glob("*/proposal.json"):
            if path.parent == current:
                continue
            other = Proposal.model_validate(read_json(path))
            if other.episode_number == number:
                raise ValueError("Episode number is already assigned in this station")
        for path in root.glob("*-*"):
            prefix = path.name.split("-", 1)[0]
            if path != current and path.is_dir() and prefix.isdigit():
                if int(prefix) == number:
                    raise ValueError(
                        "Episode number is already assigned in this station"
                    )
        destination = root / f"{number:03d}-{self.id}"
        if destination != current and destination.exists():
            raise ValueError("Numbered episode folder already exists")
        proposal_path = current / "proposal.json"
        proposal = read_json(proposal_path)
        write_json(proposal_path, {**proposal, "episode_number": number})
        if current != destination:
            current.rename(destination)
        latest_path = self.runtime / "editorial_latest.json"
        if latest_path.exists():
            latest = read_json(latest_path)
            latest["manuscript"] = str(destination / "manuscript.md")
            write_json(latest_path, latest)
        return destination

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
        concept_hash = self.concept_hash()
        if concept_hash != expected_hash:
            raise ValueError(
                "Proposal changed; review the current proposal before approving"
            )
        write_json(
            self.runtime / "proposal_approval.json",
            {"proposal_hash": actual, "concept_hash": concept_hash},
        )

    def editorial_brief(self):
        from .editorial_models import EditorialBrief

        embedded = self.proposal().editorial_brief
        legacy_path = self.documents / "brief.json"
        legacy = (
            EditorialBrief.model_validate(read_json(legacy_path))
            if legacy_path.exists()
            else None
        )
        if embedded is None:
            if legacy is None:
                raise ValueError(
                    "Editorial workflow requires editorial_brief in proposal.json"
                )
            brief = legacy
        else:
            brief = EditorialBrief.model_validate(embedded)
            if legacy is not None and legacy != brief:
                raise ValueError("Embedded and legacy editorial briefs disagree")
        if brief.proposal_hash != digest(self.proposal().model_dump()):
            raise ValueError("Editorial brief references a different proposal")
        return brief

    @property
    def has_editorial_brief(self) -> bool:
        return (
            self.proposal().editorial_brief is not None
            or (self.documents / "brief.json").exists()
        )

    def concept_hash(self) -> str:
        proposal_hash = digest(self.proposal().model_dump())
        if not self.has_editorial_brief:
            return proposal_hash  # Preserve legacy proposal-only projects.
        return digest(
            {
                "proposal_hash": proposal_hash,
                "brief": self.editorial_brief().model_dump(),
            }
        )

    def approved_proposal(self) -> tuple[Proposal, str]:
        proposal = self.proposal()
        actual = digest(proposal.model_dump())
        approval = read_json(self.runtime / "proposal_approval.json")
        if approval.get("proposal_hash") != actual:
            raise ValueError("Current proposal has not been approved")
        if approval.get("concept_hash", actual) != self.concept_hash():
            raise ValueError("Current concept/song brief has not been approved")
        return proposal, actual
