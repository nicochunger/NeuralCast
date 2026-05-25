"""Unit tests for shared data models."""

from __future__ import annotations

from neuralcast.models import Playlist, Song, ValidationResult


def test_song_defaults_optional_fields() -> None:
    song = Song(artist="Ghost", title="Rats", year="2018")

    assert song.album is None
    assert song.validated is False
    assert song.override_url is None


def test_playlist_wraps_songs_and_validation_result_accepts_empty_song() -> None:
    song = Song(artist="Ghost", title="Rats", year="2018")

    assert Playlist(songs=[song]).songs == [song]
    assert ValidationResult(song=None).song is None
