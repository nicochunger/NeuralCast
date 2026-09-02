"""Offline tests for New Releases storage boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

from neuralcast.models import Song
from neuralcast.playlists.catalog import CatalogTrack
from neuralcast.pipelines.new_releases import storage
from neuralcast.pipelines.new_releases.models import ArtistIDCache, ArtistRelease


def _release(title: str, release_date: datetime) -> ArtistRelease:
    return ArtistRelease("Ghost", title, release_date.year, "Skeleta", release_date, title)


def test_artist_cache_load_save_and_dry_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(storage, "load_station_json_dict", lambda *_args, **_kwargs: ({"ghost": "1", "bad": 2}, tmp_path))
    cache = storage.load_artist_id_cache(tmp_path)
    assert cache.entries == {"ghost": "1"}
    cache.set("Ghost", "2")
    written: dict[str, object] = {}
    monkeypatch.setattr(storage, "save_station_json_dict", lambda *_args: written.setdefault("entries", _args[-1]) or tmp_path / "cache.json")

    storage.save_artist_id_cache(tmp_path, cache, dry_run=True)
    assert not written
    storage.save_artist_id_cache(tmp_path, cache, dry_run=False)
    assert written["entries"] == {"ghost": "2"}


def test_load_existing_releases_and_partition_with_metadata(monkeypatch, tmp_path) -> None:
    tracks = [CatalogTrack(
        Song(
            artist="Ghost",
            title="Lachryma",
            album="Skeleta",
            year="2025",
            validated=False,
        ),
        {"ReleaseDate": "2025-04-25T00:00:00+00:00", "TrackID": "123", "Rank": "77", "IsSingle": "yes", "AlbumType": "single", "Validated": "true"},
    )]

    class FakeCatalog:
        def __init__(self, *_args, **_kwargs):
            pass
        def load_tracks_with_metadata(self, *_args, **_kwargs):
            return tracks

    path = tmp_path / "New Releases.csv"
    path.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(storage, "StationPlaylistCatalog", FakeCatalog)
    releases = storage.load_existing_new_releases(tmp_path)

    assert releases[0].rank == 77
    assert releases[0].is_single is True
    assert releases[0].validated is True
    valid, expired = storage.partition_releases_by_cutoff(releases + [_release("Old", datetime(2020, 1, 1, tzinfo=UTC))], datetime(2025, 1, 1, tzinfo=UTC))
    assert [item.title for item in valid] == ["Lachryma"]
    assert [item.title for item in expired] == ["Old"]


def test_release_exclusions_invalid_json_and_save_new_releases_dry_run(tmp_path) -> None:
    playlists = tmp_path / "playlists"
    metadata = tmp_path / "metadata"
    playlists.mkdir()
    metadata.mkdir()
    (metadata / "New Releases.exclusions.json").write_text("bad", encoding="utf-8")
    assert storage.load_release_exclusions(playlists) == set()

    storage.save_new_releases(playlists, [_release("New", datetime(2026, 1, 1, tzinfo=UTC))], dry_run=True)
    assert not (playlists / "New Releases.csv").exists()
