"""Boundary tests for track metadata resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from neuralcast.metadata.album_lookup import AlbumMatch
from neuralcast.metadata.track_resolution import (
    AlbumResolutionPort,
    ResolutionMode,
    TrackMetadataResolver,
    TrackResolutionRequest,
    TrackValidationPort,
)
from neuralcast.models import Song


@dataclass
class FakeValidationPort(TrackValidationPort):
    track_available: bool = True
    album_available: bool = True

    def track_exists(self, artist: str, title: str) -> bool:
        return self.track_available

    def album_matches(self, artist: str, title: str, album: str) -> bool:
        return self.album_available


@dataclass
class FakeAlbumPort(AlbumResolutionPort):
    match: AlbumMatch | None = None

    def guess_album(self, *_args, **_kwargs) -> AlbumMatch | None:
        return self.match


def _resolver(
    *,
    track_available: bool = True,
    album_available: bool = True,
    match: AlbumMatch | None = None,
) -> TrackMetadataResolver:
    return TrackMetadataResolver(
        validator=FakeValidationPort(
            track_available=track_available,
            album_available=album_available,
        ),
        album_resolver=FakeAlbumPort(match=match),
    )


def _match(
    album: str,
    *,
    album_type: str = "album",
    release_date: datetime | None = None,
    track_id: str = "track-1",
) -> AlbumMatch:
    return AlbumMatch(
        album=album,
        source="deezer",
        confidence=0.9,
        album_type=album_type,
        raw_album=album,
        release_date=release_date,
        track_id=track_id,
        track_name="Rats",
        title_score=1.0,
        artist_score=1.0,
    )


def test_validate_rejects_unavailable_track() -> None:
    song = Song(artist="Ghost", title="Rats", album="Prequelle", year="2018")

    result = _resolver(track_available=False).resolve(
        TrackResolutionRequest(song=song, mode=ResolutionMode.VALIDATE)
    )

    assert result.song is None
    assert result.available is False
    assert result.notes == ("track_unavailable",)


def test_validate_clears_unconfirmed_album_but_keeps_track() -> None:
    song = Song(artist="Ghost", title="Rats", album="Wrong", year="2018")

    result = _resolver(album_available=False).resolve(
        TrackResolutionRequest(song=song, mode=ResolutionMode.VALIDATE)
    )

    assert result.song is not None
    assert result.song.validated is True
    assert result.song.album is None
    assert result.album_cleared is True
    assert result.album_changed is True


def test_backfill_album_adds_album_and_missing_year_from_match() -> None:
    song = Song(artist="Ghost", title="Rats", album="", year="Unknown")
    match = _match("Prequelle", release_date=datetime(2018, 6, 1))

    result = _resolver(match=match).resolve(
        TrackResolutionRequest(song=song, mode=ResolutionMode.BACKFILL_ALBUM)
    )

    assert result.song is not None
    assert result.song.album == "Prequelle"
    assert result.song.year == "2018"
    assert result.album_changed is True
    assert result.source == "deezer"
    assert result.confidence == 0.9


def test_backfill_album_clears_unconfirmed_album_when_no_match_exists() -> None:
    song = Song(
        artist="Ghost",
        title="Rats",
        album="Wrong",
        year="2018",
        validated=True,
    )

    result = _resolver(album_available=False, match=None).resolve(
        TrackResolutionRequest(song=song, mode=ResolutionMode.BACKFILL_ALBUM)
    )

    assert result.song is not None
    assert result.song.album is None
    assert result.song.validated is True
    assert result.album_cleared is True


def test_promote_release_album_requires_album_match_and_carries_provider_evidence() -> None:
    song = Song(
        artist="Ghost",
        title="Rats",
        album="Rats",
        year="2018",
        validated=False,
    )
    match = _match("Prequelle", release_date=datetime(2018, 6, 1), track_id="505508952")

    result = _resolver(match=match).resolve(
        TrackResolutionRequest(song=song, mode=ResolutionMode.PROMOTE_RELEASE_ALBUM)
    )

    assert result.song is not None
    assert result.song.album == "Prequelle"
    assert result.album_promoted is True
    assert result.album_type == "album"
    assert result.track_id == "505508952"
    assert result.release_date == datetime(2018, 6, 1)


def test_promote_release_album_rejects_single_match() -> None:
    song = Song(artist="Ghost", title="Rats", album="Rats", year="2018")
    match = _match("Rats", album_type="single")

    result = _resolver(match=match).resolve(
        TrackResolutionRequest(song=song, mode=ResolutionMode.PROMOTE_RELEASE_ALBUM)
    )

    assert result.song == song
    assert result.album_promoted is False
    assert result.notes == ("album_promotion_no_studio_match",)
