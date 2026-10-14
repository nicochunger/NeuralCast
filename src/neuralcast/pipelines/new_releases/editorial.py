"""Conservative release retention policy and durable editorial decisions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from neuralcast.metadata.constants import NEW_RELEASES_METADATA_FILENAME
from neuralcast.playlists.catalog import StationPlaylistCatalog

from .codex import MODEL, REASONING_EFFORT, review_with_codex
from .logging import log_info
from .models import ArtistRelease

DECISIONS_FILENAME = "New Releases.editorial.json"
POLICY_VERSION = 1


class EditorialCatalogChanged(RuntimeError):
    """Abort the updater rather than overwrite catalog edits made during research."""


def track_key(release: ArtistRelease) -> str:
    """Match catalog identity, preserving meaningful punctuation and accents."""
    return json.dumps(
        [release.artist.strip().casefold(), release.title.strip().casefold()],
        ensure_ascii=False,
    )


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_decisions(payload: dict, keys: set[str], destinations: set[str]) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"decisions"}:
        raise ValueError("Invalid editorial document")
    rows = payload["decisions"]
    if not isinstance(rows, list):
        raise ValueError("Decisions must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "track_key",
            "action",
            "destination",
            "reason",
            "sources",
        }:
            raise ValueError("Invalid editorial decision fields")
        key = row["track_key"]
        if not isinstance(key, str) or key not in keys or key in result:
            raise ValueError("Unknown or repeated editorial track identity")
        action, destination = row["action"], row["destination"]
        if action not in ("keep", "discard"):
            raise ValueError("Invalid editorial action")
        if action == "keep" and (
            not isinstance(destination, str) or destination not in destinations
        ):
            raise ValueError("Unknown editorial destination")
        if action == "discard" and destination is not None:
            raise ValueError("Discard cannot have a destination")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("Editorial reason is required")
        sources = row["sources"]
        if not isinstance(sources, list) or any(
            not isinstance(url, str)
            or urlparse(url).scheme not in ("http", "https")
            or not urlparse(url).netloc
            for url in sources
        ):
            raise ValueError("Invalid editorial source URLs")
        result[key] = row
    if set(result) != keys:
        raise ValueError("Editorial decisions do not cover the complete batch")
    if sum(row["action"] == "discard" for row in rows) > len(keys) // 5:
        raise ValueError("Editorial discard limit exceeded")
    return result


def load_journal(playlists_dir: Path) -> dict:
    path = playlists_dir.parent / "metadata" / DECISIONS_FILENAME
    if not path.exists():
        return {"version": POLICY_VERSION, "batches": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != POLICY_VERSION
        or not isinstance(payload.get("batches"), list)
    ):
        raise ValueError("Invalid editorial journal; refusing to replace it")
    return payload


def saved_decisions(journal: dict) -> dict:
    decisions = {}
    for batch in journal["batches"]:
        validated = validate_decisions(
            batch["output"], set(batch["candidate_keys"]), set(batch["destinations"])
        )
        if decisions.keys() & validated.keys():
            raise ValueError("Repeated identities in editorial journal")
        decisions.update(validated)
    return decisions


def discarded_identities(playlists_dir: Path) -> set[str]:
    """Feed persistent editorial exclusions back into discovery."""
    return {
        key
        for key, row in saved_decisions(load_journal(playlists_dir)).items()
        if row["action"] == "discard"
    }


def snapshot(playlists_dir: Path) -> tuple[str, dict, set[str]]:
    descriptions = playlists_dir.parent / "playlist_descriptions.md"
    paths = sorted(playlists_dir.glob("*.csv")) + [descriptions]
    metadata = playlists_dir.parent / "metadata" / NEW_RELEASES_METADATA_FILENAME
    if metadata.exists():
        paths.append(metadata)
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    catalog = StationPlaylistCatalog(playlists_dir)
    permanent = {}
    for path in sorted(playlists_dir.glob("*.csv")):
        if path.stem.casefold() == "new releases":
            continue
        permanent[path.stem] = [
            {"artist": song.artist, "title": song.title, "album": song.album}
            for song in catalog.load(path).songs
        ]
    return (
        digest.hexdigest(),
        {
            "station": playlists_dir.parent.name,
            "playlist_descriptions": descriptions.read_text(encoding="utf-8"),
            "permanent_catalog": permanent,
        },
        set(permanent),
    )


def review_releases(
    playlists_dir: Path,
    releases: list[ArtistRelease],
    *,
    dry_run: bool,
    reviewer: Callable[[dict], dict] = review_with_codex,
) -> dict:
    if not releases:
        return {}
    journal = load_journal(playlists_dir)
    existing = saved_decisions(journal)
    keys = {track_key(release) for release in releases}
    if len(keys) != len(releases):
        raise ValueError("Duplicate identities in aging release batch")
    fingerprint, context, destinations = snapshot(playlists_dir)
    pending = [release for release in releases if track_key(release) not in existing]
    if pending:
        context["candidates"] = [
            {
                "track_key": track_key(release),
                "artist": release.artist,
                "title": release.title,
                "album": release.album,
                "release_date": release.release_date.isoformat(),
            }
            for release in pending
        ]
        context["max_discards"] = len(pending) // 5
        context["previous_decisions"] = list(existing.values())
        log_info(
            f"Editorial review: {len(pending)} tracks, at most {len(pending) // 5} discards"
        )
        try:
            output = reviewer(context)
        finally:
            try:
                current_fingerprint = snapshot(playlists_dir)[0]
            except (OSError, ValueError) as exc:
                raise EditorialCatalogChanged(
                    "Cannot recheck catalog after research; aborting update"
                ) from exc
            if current_fingerprint != fingerprint:
                raise EditorialCatalogChanged(
                    "Catalog changed during editorial review; retry next run"
                )
        pending_keys = {track_key(release) for release in pending}
        decisions = validate_decisions(output, pending_keys, destinations)
        journal["batches"].append(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "model": MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "input_hash": fingerprint,
                "candidate_keys": sorted(pending_keys),
                "destinations": sorted(destinations),
                "output": output,
            }
        )
        if not dry_run:
            atomic_json(playlists_dir.parent / "metadata" / DECISIONS_FILENAME, journal)
        existing.update(decisions)
    chosen = {key: existing[key] for key in keys}
    for row in chosen.values():
        if row["action"] == "keep" and row["destination"] not in destinations:
            raise ValueError("Saved editorial destination no longer exists")
        log_info(
            f"Editorial {row['action']}: {row['track_key']} → {row['destination']}: {row['reason']}"
        )
    return chosen
