"""Deep boundary for track validation and album metadata resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

from neuralcast.metadata.album_lookup import AlbumMatch
from neuralcast.models import Song, ValidationResult


class ResolutionMode(str, Enum):
    CHECK_AVAILABLE = "check_available"
    VALIDATE = "validate"
    BACKFILL_ALBUM = "backfill_album"
    PROMOTE_RELEASE_ALBUM = "promote_release_album"


@dataclass(frozen=True)
class TrackResolutionRequest:
    song: Song
    mode: ResolutionMode
    prefer_deezer: bool = True
    prefer_spotify: bool = False
    min_confidence: float = 0.55
    allow_fallback: bool = True


@dataclass(frozen=True)
class TrackResolution:
    song: Song | None
    available: bool
    validated: bool = False
    changed: bool = False
    album_cleared: bool = False
    album_changed: bool = False
    album_promoted: bool = False
    source: str | None = None
    confidence: float | None = None
    album_type: str | None = None
    track_id: str | None = None
    release_date: datetime | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def keep(self) -> bool:
        return self.song is not None

    def to_validation_result(self, original: Song) -> ValidationResult:
        album_value = (original.album or "").strip()
        return ValidationResult(
            song=self.song,
            album=album_value or None,
            album_cleared=self.album_cleared,
        )


class TrackValidationPort(Protocol):
    def track_exists(self, artist: str, title: str) -> bool:
        ...

    def album_matches(self, artist: str, title: str, album: str) -> bool:
        ...


class AlbumResolutionPort(Protocol):
    def guess_album(
        self,
        artist: str,
        title: str,
        *,
        prefer_spotify: bool,
        prefer_deezer: bool,
        min_confidence: float,
        allow_fallback: bool,
    ) -> AlbumMatch | None:
        ...


@dataclass(frozen=True)
class CallableTrackValidationPort:
    track_exists_func: Callable[[str, str], bool]
    album_matches_func: Callable[[str, str, str], bool]

    def track_exists(self, artist: str, title: str) -> bool:
        return self.track_exists_func(artist, title)

    def album_matches(self, artist: str, title: str, album: str) -> bool:
        return self.album_matches_func(artist, title, album)


@dataclass(frozen=True)
class CallableAlbumResolutionPort:
    guess_album_func: Callable[..., AlbumMatch | None]

    def guess_album(
        self,
        artist: str,
        title: str,
        *,
        prefer_spotify: bool,
        prefer_deezer: bool,
        min_confidence: float,
        allow_fallback: bool,
    ) -> AlbumMatch | None:
        return self.guess_album_func(
            artist,
            title,
            prefer_spotify=prefer_spotify,
            prefer_deezer=prefer_deezer,
            min_confidence=min_confidence,
            allow_fallback=allow_fallback,
        )


class TrackMetadataResolver:
    def __init__(
        self,
        *,
        validator: TrackValidationPort,
        album_resolver: AlbumResolutionPort,
    ) -> None:
        self._validator = validator
        self._album_resolver = album_resolver

    def resolve(self, request: TrackResolutionRequest) -> TrackResolution:
        if request.mode == ResolutionMode.CHECK_AVAILABLE:
            return self._check_available(request.song)
        if request.mode == ResolutionMode.VALIDATE:
            return self._validate(request.song)
        if request.mode == ResolutionMode.BACKFILL_ALBUM:
            return self._backfill_album(request)
        if request.mode == ResolutionMode.PROMOTE_RELEASE_ALBUM:
            return self._promote_release_album(request)
        raise ValueError(f"Unsupported resolution mode: {request.mode}")

    def resolve_song(self, song: Song, *, mode: ResolutionMode) -> TrackResolution:
        return self.resolve(TrackResolutionRequest(song=song, mode=mode))

    def _check_available(self, song: Song) -> TrackResolution:
        available = self._track_exists(song)
        return TrackResolution(
            song=song if available else None,
            available=available,
            validated=song.validated and available,
        )

    def _validate(self, song: Song) -> TrackResolution:
        if not self._track_exists(song):
            return TrackResolution(
                song=None,
                available=False,
                notes=("track_unavailable",),
            )

        album_value = (song.album or "").strip()
        if not album_value:
            validated_song = song.model_copy(update={"validated": True})
            return TrackResolution(
                song=validated_song,
                available=True,
                validated=True,
                changed=validated_song != song,
            )

        try:
            if self._validator.album_matches(song.artist, song.title, album_value):
                validated_song = song.model_copy(update={"validated": True})
                return TrackResolution(
                    song=validated_song,
                    available=True,
                    validated=True,
                    changed=validated_song != song,
                )
        except Exception as exc:  # noqa: BLE001
            return TrackResolution(
                song=song.model_copy(update={"validated": True, "album": None}),
                available=True,
                validated=True,
                changed=True,
                album_cleared=True,
                album_changed=True,
                notes=(f"album_validation_failed:{exc}",),
            )

        return TrackResolution(
            song=song.model_copy(update={"validated": True, "album": None}),
            available=True,
            validated=True,
            changed=True,
            album_cleared=True,
            album_changed=True,
            notes=("album_unconfirmed",),
        )

    def _backfill_album(self, request: TrackResolutionRequest) -> TrackResolution:
        song = request.song
        album_value = (song.album or "").strip()
        year_value = str(song.year).strip() if song.year is not None else ""
        missing_year = not year_value or year_value.casefold() == "unknown"
        album_needs_replacement = False
        notes: list[str] = []

        if album_value:
            try:
                if self._validator.album_matches(song.artist, song.title, album_value):
                    if not missing_year:
                        return TrackResolution(
                            song=song,
                            available=True,
                            validated=song.validated,
                        )
                else:
                    album_needs_replacement = True
                    notes.append("album_unconfirmed")
            except Exception as exc:  # noqa: BLE001
                album_needs_replacement = True
                notes.append(f"album_validation_failed:{exc}")

        match = self._guess_album(request, notes)
        if not match or not (match.album or "").strip():
            if album_needs_replacement:
                return TrackResolution(
                    song=song.model_copy(update={"album": None}),
                    available=True,
                    validated=song.validated,
                    changed=True,
                    album_cleared=True,
                    album_changed=True,
                    notes=tuple(notes + ["album_lookup_no_match"]),
                )
            return TrackResolution(
                song=song,
                available=True,
                validated=song.validated,
                notes=tuple(notes + ["album_lookup_no_match"]),
            )

        return self._apply_album_match(
            song,
            match,
            missing_year=missing_year,
            album_promoted=False,
            notes=tuple(notes),
        )

    def _promote_release_album(self, request: TrackResolutionRequest) -> TrackResolution:
        song = request.song
        current_album = (song.album or "").strip()
        match = self._guess_album(request, [])
        if (
            not match
            or not (match.album or "").strip()
            or (match.album_type or "").strip().casefold() != "album"
        ):
            return TrackResolution(
                song=song,
                available=True,
                validated=song.validated,
                notes=("album_promotion_no_studio_match",),
            )

        album_changed = current_album.casefold() != match.album.strip().casefold()
        type_promoted = (match.album_type or "").strip().casefold() == "album"
        if not album_changed and not type_promoted:
            return TrackResolution(
                song=song,
                available=True,
                validated=song.validated,
                source=match.source,
                confidence=match.confidence,
                album_type=match.album_type,
                track_id=match.track_id,
                release_date=match.release_date,
            )

        return self._apply_album_match(
            song,
            match,
            missing_year=bool(match.release_date),
            album_promoted=True,
            notes=(),
        )

    def _track_exists(self, song: Song) -> bool:
        try:
            return self._validator.track_exists(song.artist, song.title)
        except Exception:
            return False

    def _guess_album(
        self,
        request: TrackResolutionRequest,
        notes: list[str],
    ) -> AlbumMatch | None:
        try:
            return self._album_resolver.guess_album(
                request.song.artist,
                request.song.title,
                prefer_spotify=request.prefer_spotify,
                prefer_deezer=request.prefer_deezer,
                min_confidence=request.min_confidence,
                allow_fallback=request.allow_fallback,
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"album_lookup_failed:{exc}")
            return None

    @staticmethod
    def _apply_album_match(
        song: Song,
        match: AlbumMatch,
        *,
        missing_year: bool,
        album_promoted: bool,
        notes: tuple[str, ...],
    ) -> TrackResolution:
        updated_fields: dict[str, object] = {}
        new_album = (match.album or "").strip()
        if new_album and new_album.casefold() != (song.album or "").strip().casefold():
            updated_fields["album"] = new_album

        if missing_year and match.release_date:
            new_year = str(match.release_date.year)
            if new_year and new_year != str(song.year):
                updated_fields["year"] = new_year

        changed = bool(updated_fields)
        updated_song = song.model_copy(update=updated_fields) if changed else song
        return TrackResolution(
            song=updated_song,
            available=True,
            validated=updated_song.validated,
            changed=changed,
            album_changed="album" in updated_fields,
            album_promoted=album_promoted and bool(new_album),
            source=match.source,
            confidence=match.confidence,
            album_type=match.album_type,
            track_id=match.track_id,
            release_date=match.release_date,
            notes=notes,
        )


__all__ = [
    "AlbumResolutionPort",
    "CallableAlbumResolutionPort",
    "CallableTrackValidationPort",
    "ResolutionMode",
    "TrackMetadataResolver",
    "TrackResolution",
    "TrackResolutionRequest",
    "TrackValidationPort",
]
