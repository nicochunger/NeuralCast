"""Unit tests for host story asset helpers."""

from __future__ import annotations

import csv
import datetime as dt
import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from neuralcast.pipelines.host_orchestrator import assets


def test_load_station_track_metadata_merges_csv_and_metadata_cache(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    playlist_dir = station_dir / "playlists"
    metadata_dir = station_dir / "metadata"
    playlist_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    with (playlist_dir / "New Releases.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["Artist", "Title", "Album", "Year"])
        writer.writeheader()
        writer.writerow(
            {"Artist": "Ghost", "Title": "Rats", "Album": "Prequelle", "Year": "2018"}
        )
    (metadata_dir / "New Releases.metadata.json").write_text(
        json.dumps({"entries": {"ghost|rats|prequelle|2018": {"Popularity": 100}}}),
        encoding="utf-8",
    )

    metadata = assets.load_station_track_metadata(station_dir)

    item = metadata["ghost|rats"]
    assert item.album == "Prequelle"
    assert item.year == "2018"
    assert item.genre == "New Releases"
    assert item.notes == "popularity=100"


def test_load_station_track_metadata_tolerates_bad_sources_and_uses_direct_entries(
    tmp_path,
) -> None:
    station_dir = tmp_path / "Station"
    playlists = station_dir / "playlists"
    metadata_dir = station_dir / "metadata"
    playlists.mkdir(parents=True)
    metadata_dir.mkdir()
    (playlists / "Broken.csv").write_bytes(b"\xff\xfe")
    (metadata_dir / "New Releases.metadata.json").write_text(
        json.dumps(
            {
                "Artist|Title|Album|2026": {
                    "AlbumType": "single",
                    "ReleaseDate": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )

    metadata = assets.load_station_track_metadata(station_dir)

    assert metadata["artist|title"].album == "Album"
    assert (
        metadata["artist|title"].notes == "album_type=single, release_date=2026-01-01"
    )


def test_replaygain_and_local_cleanup_handle_missing_tool_and_expired_files(
    tmp_path, monkeypatch
) -> None:
    audio = tmp_path / "story.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(
        assets.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    assets.apply_replaygain(audio)

    output_dir = tmp_path / "snippets"
    old_dir = output_dir / "neuralforge" / "old"
    old_dir.mkdir(parents=True)
    old_file = old_dir / "story.mp3"
    old_file.write_bytes(b"old")
    fresh_file = output_dir / "neuralforge" / "fresh" / "story.txt"
    fresh_file.parent.mkdir(parents=True)
    fresh_file.write_text("fresh", encoding="utf-8")
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).timestamp()
    os.utime(old_file, (old_ts, old_ts))
    monkeypatch.setattr(assets, "STORY_OUTPUT_DIR", output_dir)

    assets.cleanup_local_stories("neuralforge", keep_days=3)

    assert not old_file.exists()
    assert fresh_file.exists()
    assert not old_dir.exists()


@pytest.fixture
def station_media(tmp_path, monkeypatch):
    station_dir = tmp_path / "NeuralForge"
    station_dir.mkdir()
    media = tmp_path / "live_media"
    media.mkdir()
    (station_dir / "songs").symlink_to(media, target_is_directory=True)

    def resolve_station(slug):
        assert slug == "neuralforge"
        return station_dir

    monkeypatch.setattr(assets, "station_dir_from_slug", resolve_station)
    return media


def test_remote_cleanup_deletes_only_expired_matching_files(station_media) -> None:
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    deleted: list[tuple[str, int]] = []
    client = SimpleNamespace(
        list_media_files=lambda _station: [
            {"path": "AI Stories/old.mp3", "mtime": now - 10_000_000, "id": 1},
            {"path": "AI Stories/fresh.mp3", "mtime": now, "id": 2},
            {"path": "Music/old.mp3", "mtime": now - 10_000_000, "id": 3},
            {"path": "AI Stories/no-id.mp3", "mtime": now - 10_000_000},
        ],
        delete_media_file=lambda station, media_id: deleted.append((station, media_id)),
    )

    assets.cleanup_remote_stories(
        client, "neuralforge", keep_days=3, media_owner_station="neuralforge"
    )

    assert deleted == [("neuralforge", 1)]


def test_prune_keeps_nonempty_folders_parents_and_other_channels(station_media):
    prefix = "AI Stories/neuralforge/fr-CH"
    base = station_media / prefix
    empty = base / "2026-08-01"
    empty.mkdir(parents=True)
    occupied = base / "2026-08-02"
    occupied.mkdir()
    audio = occupied / "story.mp3"
    audio.write_bytes(b"recording")
    nested = base / "2026-08-03" / "empty-subfolder"
    nested.mkdir(parents=True)
    invalid = base / "2026-02-30"
    invalid.mkdir()
    other = station_media / "AI Stories/neuralforge/es-AR/2026-08-01"
    other.mkdir(parents=True)
    lookalike = station_media / f"{prefix}-other/2026-08-01"
    lookalike.mkdir(parents=True)
    legacy = station_media / "AI Stories/2026-08-01"
    legacy.mkdir()

    assert assets.prune_empty_story_date_folders("neuralforge", prefix) == 1

    assert not empty.exists()
    assert base.is_dir()
    assert audio.read_bytes() == b"recording"
    assert all(p.is_dir() for p in (nested, invalid, other, lookalike, legacy))


def test_remote_cleanup_prunes_after_deletion_using_shared_media_owner(station_media):
    prefix = "AI Stories/neuralforge/fr-CH"
    base = station_media / prefix
    old = base / "2026-08-01/story.mp3"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    already_empty = base / "2026-08-02"
    already_empty.mkdir()
    now = dt.datetime.now(dt.timezone.utc).timestamp()

    def delete(station, media_id):
        assert station == "neuralforge_fr"
        assert media_id == 1
        old.unlink()

    client = SimpleNamespace(
        list_media_files=lambda station: [
            {"path": f"{prefix}/2026-08-01/story.mp3", "mtime": now - 1e7, "id": 1}
        ],
        delete_media_file=delete,
    )
    assets.cleanup_remote_stories(
        client, "neuralforge_fr", 7, prefix, media_owner_station="neuralforge"
    )

    assert list(base.iterdir()) == []


def test_remote_cleanup_preserves_recent_files_and_failed_deletions(station_media):
    prefix = "AI Stories/neuralforge/fr-CH"
    base = station_media / prefix
    failed = base / "2026-08-01/story.mp3"
    failed.parent.mkdir(parents=True)
    failed.write_bytes(b"failed")
    fresh = base / "2026-08-02/story.mp3"
    fresh.parent.mkdir()
    fresh.write_bytes(b"fresh")
    already_empty = base / "2026-08-03"
    already_empty.mkdir()
    now = dt.datetime.now(dt.timezone.utc).timestamp()

    def delete(station, media_id):
        assert media_id == 1
        raise RuntimeError("API deletion failed")

    client = SimpleNamespace(
        list_media_files=lambda station: [
            {"path": f"{prefix}/2026-08-01/story.mp3", "mtime": now - 1e7, "id": 1},
            {"path": f"{prefix}/2026-08-02/story.mp3", "mtime": now, "id": 2},
        ],
        delete_media_file=delete,
    )
    assets.cleanup_remote_stories(
        client, "neuralforge_fr", 7, prefix, media_owner_station="neuralforge"
    )

    assert failed.read_bytes() == b"failed"
    assert fresh.read_bytes() == b"fresh"
    assert not already_empty.exists()


@pytest.mark.parametrize("keep_days", [0, -1, 7])
def test_remote_cleanup_skips_folders_when_disabled_or_inventory_fails(
    station_media, keep_days
):
    empty = station_media / "AI Stories/2026-08-01"
    empty.mkdir(parents=True)

    def list_files(station):
        assert keep_days > 0
        raise RuntimeError("API unavailable")

    assets.cleanup_remote_stories(
        SimpleNamespace(list_media_files=list_files),
        "neuralforge",
        keep_days,
        media_owner_station="neuralforge",
    )

    assert empty.is_dir()


def test_prune_does_not_follow_symlinked_prefixes_or_date_folders(
    station_media, tmp_path
):
    outside = tmp_path / "outside/2026-08-01"
    outside.mkdir(parents=True)
    stories = station_media / "AI Stories"
    stories.mkdir()
    (stories / "linked-channel").symlink_to(outside.parent, target_is_directory=True)
    linked_date = stories / "2026-08-01"
    linked_date.symlink_to(outside, target_is_directory=True)

    assert (
        assets.prune_empty_story_date_folders(
            "neuralforge", "AI Stories/linked-channel"
        )
        == 0
    )
    assert assets.prune_empty_story_date_folders("neuralforge", "AI Stories") == 0
    assert outside.is_dir()
    assert linked_date.is_symlink()


@pytest.mark.parametrize(
    "prefix",
    ["", ".", "../Music", "/AI Stories", "AI Stories/../Music", "AI Stories\\Music"],
)
def test_prune_rejects_unsafe_prefixes(station_media, prefix):
    with pytest.raises(ValueError, match="canonical relative path"):
        assets.prune_empty_story_date_folders("neuralforge", prefix)


def test_prune_logs_permission_failure_and_continues(
    station_media, monkeypatch, caplog
):
    base = station_media / "AI Stories"
    blocked = base / "2026-08-01"
    removable = base / "2026-08-02"
    blocked.mkdir(parents=True)
    removable.mkdir()
    original_rmdir = Path.rmdir

    def rmdir(path):
        if path.resolve() == blocked.resolve():
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", rmdir)

    assert assets.prune_empty_story_date_folders("neuralforge", "AI Stories") == 1
    assert blocked.is_dir()
    assert not removable.exists()
    assert "Unable to remove empty story folder" in caplog.text


def test_prune_preserves_file_created_during_cleanup(station_media, monkeypatch):
    directory = station_media / "AI Stories/2026-08-01"
    directory.mkdir(parents=True)
    audio = directory / "concurrent-upload.mp3"
    original_rmdir = Path.rmdir

    def rmdir(path):
        audio.write_bytes(b"upload")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", rmdir)

    assert assets.prune_empty_story_date_folders("neuralforge", "AI Stories") == 0
    assert audio.read_bytes() == b"upload"
