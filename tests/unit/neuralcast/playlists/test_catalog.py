"""Tests for the station playlist catalog Interface."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from neuralcast.metadata.storage import metadata_key
from neuralcast.models import Song
from neuralcast.playlists.catalog import (
    CatalogFormatError,
    CatalogPersistenceError,
    CatalogTrack,
    CatalogWritePolicy,
    StationPlaylistCatalog,
)


def test_load_requires_artist_and_title_columns(tmp_path) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    pd.DataFrame([{"Name": "Unknown"}]).to_csv(
        playlists_dir / "Broken.csv", index=False
    )

    with pytest.raises(CatalogFormatError, match="required column"):
        StationPlaylistCatalog(playlists_dir).load("Broken")


def test_save_preserves_unknown_columns_without_exposing_frame_to_caller(
    tmp_path,
) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    path = playlists_dir / "Metal.csv"
    pd.DataFrame(
        [
            {
                "Artist": "Ghost",
                "Title": "Rats",
                "Album": "",
                "Year": "2018",
                "Validated": False,
                "Mood": "dark",
            }
        ]
    ).to_csv(path, index=False)
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _message: None)
    snapshot = catalog.load("Metal")

    catalog.save(
        snapshot,
        [snapshot.songs[0].model_copy(update={"album": "Prequelle"})],
    )

    saved = pd.read_csv(path, dtype=str).fillna("")
    assert saved.iloc[0]["Album"] == "Prequelle"
    assert saved.iloc[0]["Mood"] == "dark"


def test_preview_append_does_not_write_and_new_persisted_row_is_unvalidated(
    tmp_path,
) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    path = playlists_dir / "Metal.csv"
    pd.DataFrame(columns=["Artist", "Title", "Album", "Year", "Validated"]).to_csv(
        path, index=False
    )
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _message: None)
    song = Song(
        artist="Ghost",
        title="Rats",
        album="Prequelle",
        year="2018",
        validated=True,
    )

    assert catalog.append("Metal", song, policy=CatalogWritePolicy.PREVIEW) is True
    assert pd.read_csv(path).empty
    assert catalog.append("Metal", song) is True

    saved = pd.read_csv(path)
    assert bool(saved.iloc[0]["Validated"]) is False


def test_replace_new_releases_writes_csv_and_matching_metadata(tmp_path) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    song = Song(
        artist="Ghost",
        title="Peacefield",
        album="Skeletá",
        year="2025",
        validated=False,
    )
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _message: None)

    catalog.replace_with_metadata(
        "New Releases",
        [CatalogTrack(song=song, metadata={"TrackID": "123"})],
    )

    metadata_path = tmp_path / "metadata" / "New Releases.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["entries"] == {
        metadata_key("Ghost", "Peacefield", "Skeletá", "2025"): {
            "TrackID": "123"
        }
    }


def test_save_new_releases_removal_updates_companion_metadata(tmp_path) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    remove = Song(artist="Artist A", title="Remove", album="One", year="2026")
    keep = Song(artist="Artist B", title="Keep", album="Two", year="2026")
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _message: None)
    catalog.replace_with_metadata(
        "New Releases",
        [
            CatalogTrack(song=remove, metadata={"TrackID": "remove"}),
            CatalogTrack(song=keep, metadata={"TrackID": "keep"}),
        ],
    )
    snapshot = catalog.load("New Releases")

    catalog.save(snapshot, [keep], removed_songs=[remove])

    entries = catalog.load_companion_entries()
    assert list(entries) == [metadata_key("Artist B", "Keep", "Two", "2026")]


def test_replace_cleans_staged_files_when_metadata_is_not_serializable(
    tmp_path,
) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    song = Song(artist="Ghost", title="Rats", album="Prequelle", year="2018")
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _message: None)

    with pytest.raises(CatalogPersistenceError):
        catalog.replace_with_metadata(
            "New Releases",
            [CatalogTrack(song=song, metadata={"invalid": object()})],
        )

    assert not (playlists_dir / "New Releases.csv.tmp").exists()
    assert not (
        tmp_path / "metadata" / "New Releases.metadata.json.tmp"
    ).exists()


def test_replace_rolls_back_csv_when_metadata_commit_fails(tmp_path, monkeypatch) -> None:
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _message: None)
    original_song = Song(
        artist="Ghost",
        title="Rats",
        album="Prequelle",
        year="2018",
    )
    replacement_song = Song(
        artist="Ghost",
        title="Spillways",
        album="Impera",
        year="2022",
    )
    catalog.replace_with_metadata(
        "New Releases",
        [CatalogTrack(song=original_song, metadata={"TrackID": "old"})],
    )

    csv_path = playlists_dir / "New Releases.csv"
    metadata_path = tmp_path / "metadata" / "New Releases.metadata.json"
    original_csv = csv_path.read_bytes()
    original_metadata = metadata_path.read_bytes()
    original_replace = type(csv_path).replace
    metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")

    def fail_metadata_commit(path, target):
        if path == metadata_tmp and target == metadata_path:
            raise OSError("simulated metadata rename failure")
        return original_replace(path, target)

    monkeypatch.setattr(type(csv_path), "replace", fail_metadata_commit)

    with pytest.raises(CatalogPersistenceError, match="paired playlist artifacts"):
        catalog.replace_with_metadata(
            "New Releases",
            [CatalogTrack(song=replacement_song, metadata={"TrackID": "new"})],
        )

    assert csv_path.read_bytes() == original_csv
    assert metadata_path.read_bytes() == original_metadata
    assert not (playlists_dir / ".New Releases.csv.catalog-backup").exists()
    assert not (
        tmp_path / "metadata" / ".New Releases.metadata.json.catalog-backup"
    ).exists()
