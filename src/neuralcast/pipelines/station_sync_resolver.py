"""Track validation and album-backfill implementation for station sync."""

from __future__ import annotations

from typing import Callable

from neuralcast.metadata.album_lookup import guess_album
from neuralcast.metadata.track_resolution import (
    CallableAlbumResolutionPort,
    CallableTrackValidationPort,
    ResolutionMode,
    TrackMetadataResolver,
    TrackResolutionRequest,
)
from neuralcast.models import Song, ValidationResult
from neuralcast.services.validation import verified, verified_album


class DefaultTrackResolver:
    def __init__(self, resolver: TrackMetadataResolver | None = None) -> None:
        self._resolver = resolver or TrackMetadataResolver(
            validator=CallableTrackValidationPort(
                track_exists_func=verified,
                album_matches_func=verified_album,
            ),
            album_resolver=CallableAlbumResolutionPort(guess_album_func=guess_album),
        )

    def is_available(self, song: Song) -> bool:
        return self._resolver.resolve(
            TrackResolutionRequest(song=song, mode=ResolutionMode.CHECK_AVAILABLE)
        ).available

    def validate_song(self, song: Song) -> ValidationResult:
        resolution = self._resolver.resolve(
            TrackResolutionRequest(song=song, mode=ResolutionMode.VALIDATE)
        )
        return resolution.to_validation_result(song)

    def backfill_album(
        self,
        song: Song,
        *,
        log: Callable[[str], None] = print,
    ) -> tuple[Song, bool]:
        resolution = self._resolver.resolve(
            TrackResolutionRequest(
                song=song,
                mode=ResolutionMode.BACKFILL_ALBUM,
                prefer_spotify=False,
                prefer_deezer=True,
                min_confidence=0.55,
                allow_fallback=True,
            )
        )
        for note in resolution.notes:
            if note == "album_unconfirmed":
                log(
                    "⚠️ album could not be confirmed for "
                    f"{song.artist} - {song.title}: {song.album}"
                )
            elif note == "album_lookup_no_match":
                log(f"⚠️ album lookup found no match for {song.artist} - {song.title}")
            elif note.startswith("album_validation_failed:"):
                detail = note.removeprefix("album_validation_failed:")
                log(f"⚠️ album check failed for {song.artist} - {song.title}: {detail}")
            elif note.startswith("album_lookup_failed:"):
                detail = note.removeprefix("album_lookup_failed:")
                log(f"⚠️ album lookup failed for {song.artist} - {song.title}: {detail}")
        if resolution.album_cleared and song.album:
            log(
                f"🧹 album metadata cleared: "
                f"{song.artist} - {song.title} ({song.album})"
            )
        if resolution.album_changed and resolution.song and resolution.song.album:
            source = resolution.source or "unknown"
            confidence = (
                resolution.confidence if resolution.confidence is not None else 0.0
            )
            log(
                f"📝 album updated: {song.artist} - {song.title} -> {resolution.song.album} "
                f"(source {source}, confidence {confidence:.2f})"
            )
        if (
            resolution.song
            and resolution.song.year != song.year
            and resolution.release_date is not None
        ):
            log(
                f"📝 year updated: "
                f"{song.artist} - {song.title} -> {resolution.song.year}"
            )
        return resolution.song or song, resolution.changed


__all__ = ["DefaultTrackResolver"]
