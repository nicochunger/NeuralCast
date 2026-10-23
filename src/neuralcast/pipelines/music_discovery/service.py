"""Resumable five-song auditions and owner-authorized feedback application."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import uuid
from typing import Callable
from zoneinfo import ZoneInfo

from neuralcast.config import DEFAULT_TIMEZONE_NAME, RUNTIME_ROOT, station_dir_from_slug
from neuralcast.models import Song
from neuralcast.playlists.catalog import StationPlaylistCatalog, playlist_song_key
from neuralcast.services.editorial_agent import EditorialStageError
from . import media
from .codex import MODEL, REASONING_EFFORT, run_agent
from .models import Audition, Batch, FeedbackDraft, FeedbackResult, Suggestions
from .storage import (
    Store,
    ConflictError,
    atomic_json,
    digest,
    exclusive_lock,
    file_hash,
    now_iso,
)


class DiscoveryService:
    def __init__(
        self,
        station: str,
        *,
        root: Path | None = None,
        station_dir: Path | None = None,
        maintenance_lock: Path | None = None,
        agent: Callable = run_agent,
        downloader: Callable = media.download,
    ) -> None:
        self.store = Store(station, root)
        self.station_dir = station_dir or station_dir_from_slug(station)
        self.maintenance_lock = maintenance_lock or Path(
            os.getenv(
                "NC_MAINTENANCE_LOCK_FILE",
                str(RUNTIME_ROOT / "catalog-maintenance.lock"),
            )
        )
        self.agent = agent
        self.downloader = downloader

    def catalog_context(self) -> dict:
        catalog = StationPlaylistCatalog(
            self.station_dir / "playlists", log=lambda _: None
        )
        playlists = {}
        identities = set()
        for path in sorted(catalog.playlists_dir.glob("*.csv")):
            snapshot = catalog.load(path)
            identities.update(
                playlist_song_key(song)
                for song in snapshot.songs + snapshot.deletion_requests
            )
            if path.stem == "New Releases":
                continue
            if path.is_symlink():
                raise ValueError("Discovery destinations must be local playlist CSVs")
            playlists[path.stem] = [song.model_dump() for song in snapshot.songs]
        if not playlists:
            raise ValueError("No permanent station playlists are available")
        return {
            "station": self.store.station,
            "playlist_definitions": (
                self.station_dir / "playlist_descriptions.md"
            ).read_text(),
            "playlists": playlists,
            "existing_identities": sorted(identities),
        }

    def _history(self, exclude: str | None = None) -> list[dict]:
        result = []
        for path in sorted((self.store.root / "batches").glob("*/batch.json")):
            batch = self.store.load_batch(path.parent.name)
            if batch.id != exclude:
                result.append(
                    {
                        "batch_id": batch.id,
                        "candidates": [c.model_dump() for c in batch.candidates],
                        "feedback": {
                            k: v.model_dump() for k, v in batch.feedback.items()
                        },
                    }
                )
        return result

    def _agent_result(self, batch: Batch, stage: str, context: dict, model):
        folder = self.store.batch_dir(batch.id) / stage
        result_path = folder / "accepted.json"
        context_hash = digest(context)
        if result_path.exists():
            saved = json.loads(result_path.read_text())
            if saved["context_hash"] != context_hash:
                raise ConflictError(
                    "Saved agent result belongs to a different snapshot"
                )
            return model.model_validate(saved["result"])
        attempt = folder / f"attempt-{uuid.uuid4().hex}"
        parsed = model.model_validate(self.agent(stage, context, attempt))
        # Application-specific validation happens before this result is persisted.
        return parsed

    def _save_agent_result(
        self, batch: Batch, stage: str, context: dict, result
    ) -> None:
        atomic_json(
            self.store.batch_dir(batch.id) / stage / "accepted.json",
            {
                "context_hash": digest(context),
                "model": MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "result": result.model_dump(),
            },
        )

    def generate(self, *, scheduled: bool = False) -> dict:
        with self.store.lock():
            state = self.store.state()
            batch = self.store.current()
            if batch and batch.status in {"review", "submitted"}:
                return {
                    "skipped": True,
                    "reason": "The current batch is awaiting review or processing",
                    "batch_id": batch.id,
                }
            week = datetime.now(ZoneInfo(DEFAULT_TIMEZONE_NAME)).strftime("%G-W%V")
            if (
                scheduled
                and state.last_scheduled_week == week
                and (not batch or batch.status == "processed")
            ):
                return {
                    "skipped": True,
                    "reason": "Suggestions already generated this week",
                }
            if batch is None or batch.status == "processed":
                batch = Batch(
                    id=f"batch-{uuid.uuid4().hex}",
                    station=self.store.station,
                    created_at=now_iso(),
                )
                context = {
                    **self.catalog_context(),
                    "preferences": state.preferences,
                    "previous_batches": self._history(),
                }
                atomic_json(
                    self.store.batch_dir(batch.id) / "discovery_context.json", context
                )
                self.store.save_batch(batch)
                state.current_batch_id = batch.id
                state.last_scheduled_week = week
                self.store.save_state(state)
            if batch.error is not None:
                batch.error = None
                self.store.save_batch(batch)
            try:
                context = json.loads(
                    (
                        self.store.batch_dir(batch.id) / "discovery_context.json"
                    ).read_text()
                )
                if not batch.candidates:
                    result = self._agent_result(
                        batch, "discovery", context, Suggestions
                    )
                    self._validate_suggestions(result, context)
                    self._save_agent_result(batch, "discovery", context, result)
                    batch.candidates = result.candidates
                    self.store.save_batch(batch)
                for candidate in batch.candidates:
                    if candidate.id in batch.auditions:
                        path = self.store.verified_audio(batch, candidate.id)
                        media.save_artwork(
                            path, self.store.artwork_path(batch, candidate.id)
                        )
                        continue
                    path = self.store.audio_path(batch, candidate.id)
                    self.downloader(candidate, path)
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or path.stat().st_size <= 0
                    ):
                        raise ValueError(
                            "Downloader did not produce an audition recording"
                        )
                    batch.auditions[candidate.id] = Audition(
                        sha256=file_hash(path), size=path.stat().st_size
                    )
                    self.store.save_batch(batch)
                    media.save_artwork(
                        path, self.store.artwork_path(batch, candidate.id)
                    )
                batch.status = "review"
                batch.error = None
                self.store.save_batch(batch)
                state.last_scheduled_week = week
                self.store.save_state(state)
                return {"skipped": False, "batch_id": batch.id}
            except Exception as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, EditorialStageError)
                    else f"Preparation failed ({type(exc).__name__})."
                )
                batch.error = f"{reason} Generate suggestions retries this batch."
                self.store.save_batch(batch)
                raise

    def _validate_suggestions(self, result: Suggestions, context: dict) -> None:
        identities = {tuple(key) for key in context["existing_identities"]}
        prior = {
            (c["artist"].strip().lower(), c["title"].strip().lower())
            for b in context["previous_batches"]
            for c in b["candidates"]
        }
        current = set()
        ids = set()
        artists = set()
        paths = set()
        sources = set()
        permanent_artists = {
            song["artist"].strip().lower()
            for songs in context["playlists"].values()
            for song in songs
        }
        for candidate in result.candidates:
            key = (candidate.artist.lower(), candidate.title.lower())
            target = media.target_path(Path("songs"), candidate, candidate.playlist)
            if (
                candidate.id in ids
                or key in identities | prior | current
                or candidate.playlist not in context["playlists"]
                or target in paths
                or candidate.artist.lower() in artists
                or candidate.source_url in sources
            ):
                raise ValueError(
                    "Repeated recording, artist, unsafe destination, or existing suggestion"
                )
            ids.add(candidate.id)
            current.add(key)
            artists.add(candidate.artist.lower())
            paths.add(target)
            sources.add(candidate.source_url)
        if len(artists - permanent_artists) < 3:
            raise ValueError(
                "At least three suggested artists must be new to the permanent catalog"
            )

    def save_feedback(self, draft: FeedbackDraft) -> Batch:
        with self.store.lock():
            batch = self.store.current()
            if (
                not batch
                or batch.id != draft.batch_id
                or batch.status != "review"
                or draft.revision != batch.revision
            ):
                raise ConflictError(
                    "This review changed or was submitted; reload before saving"
                )
            if set(draft.feedback) - {c.id for c in batch.candidates}:
                raise ValueError("Feedback contains an unknown suggestion")
            batch.feedback = draft.feedback
            batch.revision += 1
            self.store.save_batch(batch)
            return batch

    def submit(self, batch_id: str, revision: int) -> Batch:
        with self.store.lock():
            batch = self.store.current()
            if not batch or batch.id != batch_id:
                raise ConflictError("Unknown current batch")
            if batch.status in {"submitted", "processed"}:
                if revision != batch.revision:
                    raise ConflictError("Feedback revision changed")
                return batch
            if batch.status != "review" or revision != batch.revision:
                raise ConflictError(
                    "Feedback changed; save and reload before submitting"
                )
            if set(batch.feedback) != {c.id for c in batch.candidates}:
                raise ValueError(
                    "Choose Yes or No for all five suggestions before processing"
                )
            for candidate in batch.candidates:
                self.store.verified_audio(batch, candidate.id)
            batch.submission_hash = digest(
                {k: v.model_dump() for k, v in batch.feedback.items()}
            )
            batch.status = "submitted"
            batch.error = None
            atomic_json(
                self.store.batch_dir(batch.id) / "feedback_context.json",
                {
                    **self.catalog_context(),
                    "preferences": self.store.state().preferences,
                    "batch_id": batch.id,
                    "candidates": [c.model_dump() for c in batch.candidates],
                    "feedback": {k: v.model_dump() for k, v in batch.feedback.items()},
                },
            )
            self.store.save_batch(batch)
            return batch

    def process(self, batch_id: str) -> dict:
        with self.store.lock():
            batch = self.store.current()
            if (
                not batch
                or batch.id != batch_id
                or batch.status not in {"submitted", "processed"}
            ):
                raise ConflictError("Submit the current feedback before processing")
            if batch.status == "processed":
                return {"batch_id": batch.id, "already_processed": True}
            if (
                digest({k: v.model_dump() for k, v in batch.feedback.items()})
                != batch.submission_hash
            ):
                raise ConflictError("Frozen feedback was altered")
            if batch.error is not None:
                batch.error = None
                self.store.save_batch(batch)
            try:
                context = json.loads(
                    (
                        self.store.batch_dir(batch.id) / "feedback_context.json"
                    ).read_text()
                )
                if digest(context["feedback"]) != batch.submission_hash:
                    raise ConflictError(
                        "Feedback snapshot does not match the submitted review"
                    )
                if batch.editorial is None:
                    result = self._agent_result(
                        batch, "feedback", context, FeedbackResult
                    )
                    self._validate_feedback_result(batch, result, context)
                    self._save_agent_result(batch, "feedback", context, result)
                    batch.editorial = result
                    self.store.save_batch(batch)
                self._validate_feedback_result(batch, batch.editorial, context)
                with exclusive_lock(self.maintenance_lock):
                    self._apply_accepted(batch)
                state = self.store.state()
                if not any(item["batch_id"] == batch.id for item in state.preferences):
                    state.preferences.append(
                        {
                            "batch_id": batch.id,
                            "updates": [
                                p.model_dump()
                                for p in batch.editorial.preference_updates
                            ],
                            "feedback": {
                                k: v.model_dump() for k, v in batch.feedback.items()
                            },
                        }
                    )
                    self.store.save_state(state)
                # Remove only private audition copies, never station files. Receipts remain.
                for candidate in batch.candidates:
                    if candidate.id in batch.cleaned:
                        continue
                    path = self.store.audio_path(batch, candidate.id)
                    if path.exists() or path.is_symlink():
                        self.store.verified_audio(batch, candidate.id)
                        path.unlink()
                    batch.cleaned.append(candidate.id)
                    self.store.save_batch(batch)
                batch.status = "processed"
                batch.error = None
                self.store.save_batch(batch)
                return {"batch_id": batch.id, "accepted": len(batch.promoted)}
            except Exception as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, EditorialStageError)
                    else f"Feedback processing failed ({type(exc).__name__})."
                )
                batch.error = f"{reason} Retry processing to resume."
                self.store.save_batch(batch)
                raise

    def _validate_feedback_result(
        self, batch: Batch, result: FeedbackResult, context: dict
    ) -> None:
        accepted = {
            key for key, feedback in batch.feedback.items() if feedback.accepted
        }
        ids = [p.candidate_id for p in result.placements]
        if set(ids) != accepted or len(ids) != len(set(ids)):
            raise ValueError(
                "Agent placements must match exactly the owner-accepted songs"
            )
        for placement in result.placements:
            if placement.playlist not in context["playlists"]:
                raise ValueError("Agent proposed an unknown permanent playlist")
        for update in result.preference_updates:
            if set(update.candidate_ids) - set(batch.feedback):
                raise ValueError("Preference evidence references an unknown suggestion")
            if update.scope != "recording" and any(
                not batch.feedback[key].reasoning for key in update.candidate_ids
            ):
                raise ValueError(
                    "Broader preferences require explicit written feedback"
                )

    def _apply_accepted(self, batch: Batch) -> None:
        catalog = StationPlaylistCatalog(
            self.station_dir / "playlists", log=lambda _: None
        )
        candidates = {c.id: c for c in batch.candidates}
        # Preflight every destination before making any change.
        targets = set()
        for placement in batch.editorial.placements:
            candidate = candidates[placement.candidate_id]
            snapshot = catalog.load(placement.playlist)
            if snapshot.path.is_symlink() or snapshot.deletion_requests:
                raise ConflictError("Destination has deletion markers or is a symlink")
            target = media.target_path(
                self.station_dir / "songs", candidate, placement.playlist
            )
            if target in targets:
                raise ConflictError(
                    "Accepted recordings collide after filename sanitation"
                )
            targets.add(target)
            if candidate.id not in batch.promoted:
                self.store.verified_audio(batch, candidate.id)
                media.check_target(target, batch.auditions[candidate.id].sha256)
        for placement in batch.editorial.placements:
            candidate = candidates[placement.candidate_id]
            if candidate.id in batch.promoted:
                continue
            snapshot = catalog.load(placement.playlist)
            key = (candidate.artist.lower(), candidate.title.lower())
            source = self.store.verified_audio(batch, candidate.id)
            target = media.target_path(
                self.station_dir / "songs", candidate, placement.playlist
            )
            if not (self.station_dir / "songs").is_dir():
                raise ConflictError("Station media root is unavailable")
            # Install only the explicitly accepted file, never invoking station-wide sync.
            media.promote(source, target, batch.auditions[candidate.id].sha256)
            if key not in {playlist_song_key(song) for song in snapshot.songs}:
                catalog.save(
                    snapshot,
                    [
                        *snapshot.songs,
                        Song(
                            artist=candidate.artist,
                            title=candidate.title,
                            album=candidate.album,
                            year=candidate.year,
                            validated=False,
                        ),
                    ],
                )
            batch.promoted.append(candidate.id)
            self.store.save_batch(batch)

    def view(self) -> dict:
        batch = self.store.current()
        state = self.store.state()
        return {
            "station": self.store.station,
            "batch": batch.model_dump() if batch else None,
            "preferences": state.preferences,
        }
