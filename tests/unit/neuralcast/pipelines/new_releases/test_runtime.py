"""Unit tests for the New Releases runtime interface."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

from neuralcast.pipelines.new_releases.runtime import (
    NewReleasesRequest,
    NewReleasesRuntime,
    NewReleasesRuntimeDependencies,
)

legacy = importlib.import_module("neuralcast.pipelines.new_releases.main")


def _release(
    title: str,
    *,
    release_date: datetime,
    track_id: str,
    rank: int = 0,
) -> legacy.ArtistRelease:
    return legacy.ArtistRelease(
        artist="Ghost",
        title=title,
        album="Skeleta",
        year=release_date.year,
        release_date=release_date,
        track_id=track_id,
        rank=rank,
    )


def test_runtime_migrates_outdated_before_saving_final_playlist(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    playlists_dir = station_dir / "playlists"
    songs_dir = station_dir / "songs"
    playlists_dir.mkdir(parents=True)
    songs_dir.mkdir()
    old_release = _release(
        "Peacefield",
        release_date=datetime(2025, 1, 1, tzinfo=UTC),
        track_id="old",
    )
    new_release = _release(
        "Lachryma",
        release_date=datetime(2026, 5, 1, tzinfo=UTC),
        track_id="new",
    )
    calls: list[str] = []
    build_kwargs: dict[str, object] = {}

    def build_releases(*_args, **kwargs):
        build_kwargs.update(kwargs)
        return [new_release]

    def deps_factory(request: NewReleasesRequest) -> NewReleasesRuntimeDependencies:
        return NewReleasesRuntimeDependencies(
            station_paths=lambda _station: (station_dir, playlists_dir),
            load_station_artists=lambda _playlists: (
                ["Ghost"],
                {"Ghost": {"Rats"}},
                {"Ghost": {playlists_dir / "Gothic Metal.csv": {"Peacefield"}}},
            ),
            load_artist_id_cache=lambda _playlists: legacy.ArtistIDCache({}),
            load_existing_new_releases=lambda _playlists: [old_release],
            build_new_releases=build_releases,
            save_artist_id_cache=lambda _playlists, _cache: calls.append("cache"),
            move_outdated_releases=lambda *_args: calls.append("move"),
            save_new_releases=lambda _playlists, _releases: calls.append("save"),
            now=lambda: datetime(2026, 6, 1, tzinfo=UTC),
            set_debug_mode=lambda _enabled: None,
            log_debug=lambda _message: None,
            log_info=lambda _message: None,
        )

    result = NewReleasesRuntime(deps_factory).run(
        NewReleasesRequest(station="neuralforge", dry_run=True)
    )

    assert calls == ["cache", "move", "save"]
    assert result.final_releases == [new_release]
    assert result.outdated_existing == [old_release]
    assert build_kwargs["existing_artist_counts"] == {}


def test_runtime_keeps_collision_blocked_release_in_new_releases(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    playlists_dir = station_dir / "playlists"
    songs_dir = station_dir / "songs"
    playlists_dir.mkdir(parents=True)
    songs_dir.mkdir()
    old_release = _release(
        "Peacefield",
        release_date=datetime(2025, 1, 1, tzinfo=UTC),
        track_id="old",
    )
    saved: list[list[legacy.ArtistRelease]] = []

    def deps_factory(request: NewReleasesRequest) -> NewReleasesRuntimeDependencies:
        return NewReleasesRuntimeDependencies(
            station_paths=lambda _station: (station_dir, playlists_dir),
            load_station_artists=lambda _playlists: (
                ["Ghost"],
                {"Ghost": {"Rats"}},
                {"Ghost": {playlists_dir / "Gothic Metal.csv": {"Rats"}}},
            ),
            load_artist_id_cache=lambda _playlists: legacy.ArtistIDCache({}),
            load_existing_new_releases=lambda _playlists: [old_release],
            build_new_releases=lambda *_args, **_kwargs: [],
            save_artist_id_cache=lambda _playlists, _cache: None,
            move_outdated_releases=lambda *_args: [old_release],
            save_new_releases=lambda _playlists, releases: saved.append(releases),
            now=lambda: datetime(2026, 6, 1, tzinfo=UTC),
            set_debug_mode=lambda _enabled: None,
            log_debug=lambda _message: None,
            log_info=lambda _message: None,
        )

    result = NewReleasesRuntime(deps_factory).run(
        NewReleasesRequest(station="neuralforge")
    )

    assert result.final_releases == [old_release]
    assert saved == [[old_release]]


def test_runtime_dedupes_existing_and_new_releases_by_track_id(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    playlists_dir = station_dir / "playlists"
    playlists_dir.mkdir(parents=True)
    existing = _release(
        "Lachryma",
        release_date=datetime(2026, 5, 1, tzinfo=UTC),
        track_id="same",
        rank=1,
    )
    duplicate = _release(
        "Lachryma",
        release_date=datetime(2026, 5, 2, tzinfo=UTC),
        track_id="same",
        rank=10,
    )
    saved: list[list[legacy.ArtistRelease]] = []

    def deps_factory(request: NewReleasesRequest) -> NewReleasesRuntimeDependencies:
        return NewReleasesRuntimeDependencies(
            station_paths=lambda _station: (station_dir, playlists_dir),
            load_station_artists=lambda _playlists: (["Ghost"], {"Ghost": set()}, {}),
            load_artist_id_cache=lambda _playlists: legacy.ArtistIDCache({}),
            load_existing_new_releases=lambda _playlists: [existing],
            build_new_releases=lambda *_args, **_kwargs: [duplicate],
            save_artist_id_cache=lambda _playlists, _cache: None,
            move_outdated_releases=lambda *_args: None,
            save_new_releases=lambda _playlists, releases: saved.append(releases),
            now=lambda: datetime(2026, 6, 1, tzinfo=UTC),
            set_debug_mode=lambda _enabled: None,
            log_debug=lambda _message: None,
            log_info=lambda _message: None,
        )

    result = NewReleasesRuntime(deps_factory).run(
        NewReleasesRequest(station="neuralforge")
    )

    assert result.final_releases == [duplicate]
    assert saved == [[duplicate]]
