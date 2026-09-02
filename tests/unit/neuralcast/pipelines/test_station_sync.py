"""Focused unit tests for station sync helpers."""

from __future__ import annotations

import json

import pytest

from neuralcast.audio.download import DownloadNoResultsError
from neuralcast.metadata.storage import metadata_key
from neuralcast.models import Song
from neuralcast.pipelines import station_sync
from neuralcast.pipelines.station_sync import media as station_sync_media
from neuralcast.pipelines.station_sync import resolver as station_sync_resolver
from neuralcast.pipelines.station_sync import service as station_sync_service


def test_remove_new_releases_metadata_entries_removes_matching_keys(tmp_path) -> None:
    playlists_dir = tmp_path / "playlists"
    metadata_dir = tmp_path / "metadata"
    playlists_dir.mkdir()
    metadata_dir.mkdir()
    keep_key = metadata_key("Artist B", "Keep", "Album", "2026")
    remove_key = metadata_key("Artist A", "Remove", "Album", "2026")
    metadata_path = metadata_dir / "New Releases.metadata.json"
    metadata_path.write_text(
        json.dumps({"entries": {keep_key: {}, remove_key: {}}}),
        encoding="utf-8",
    )

    removed = station_sync.remove_new_releases_metadata_entries(
        playlists_dir,
        [Song(artist="Artist A", title="Remove", album="Album", year="2026")],
    )

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert removed == 1
    assert sorted(payload["entries"]) == [keep_key]


def test_station_sync_facade_reexports_owning_implementations() -> None:
    assert station_sync.DefaultMediaLibrary is station_sync_media.DefaultMediaLibrary
    assert station_sync.DefaultTrackResolver is station_sync_resolver.DefaultTrackResolver
    assert station_sync.StationSync is station_sync_service.StationSync


def test_default_media_library_apply_override_rejects_bad_inputs(tmp_path, capsys) -> None:
    library = station_sync.DefaultMediaLibrary()
    log = station_sync.PlaylistLog("Playlist")

    assert not library.apply_override(
        Song(artist="", title="Song", year="2026", override_url="https://youtu.be/x"),
        tmp_path / "song.mp3",
        "Playlist",
        dry_run=False,
        log=log,
    )
    assert not library.apply_override(
        Song(artist="Artist", title="Song", year="2026", override_url="https://example.com/x"),
        tmp_path / "song.mp3",
        "Playlist",
        dry_run=False,
        log=log,
    )
    assert not library.apply_override(
        Song(artist="Artist", title="Song", year="2026", override_url="https://youtu.be/x"),
        None,
        "Playlist",
        dry_run=False,
        log=log,
    )

    output = capsys.readouterr().out
    assert "missing artist/title" in output
    assert "unsupported URL" in output
    assert "could not determine target path" in output


def test_default_media_library_apply_override_dry_run_does_not_download(tmp_path, monkeypatch) -> None:
    library = station_sync.DefaultMediaLibrary()
    song_path = tmp_path / "Artist - Song.mp3"
    song = Song(
        artist="Artist",
        title="Song",
        year="2026",
        override_url="https://youtu.be/x",
    )
    monkeypatch.setattr(
        station_sync_media,
        "youtube_to_mp3",
        lambda *_args, **_kwargs: pytest.fail("download should not run in dry-run"),
    )

    changed = library.apply_override(
        song,
        song_path,
        "Playlist",
        dry_run=True,
        log=station_sync.PlaylistLog("Playlist"),
    )

    assert changed is False
    assert song.override_url == "https://youtu.be/x"


def test_default_media_library_apply_override_restores_backup_on_failure(tmp_path, monkeypatch) -> None:
    library = station_sync.DefaultMediaLibrary()
    song_path = tmp_path / "Artist - Song.mp3"
    song_path.write_bytes(b"original")
    song = Song(
        artist="Artist",
        title="Song",
        year="2026",
        override_url="https://youtu.be/x",
    )

    def failing_download(*_args, **_kwargs) -> None:
        song_path.write_bytes(b"partial")
        raise DownloadNoResultsError("not found")

    monkeypatch.setattr(station_sync_media, "youtube_to_mp3", failing_download)

    changed = library.apply_override(
        song,
        song_path,
        "Playlist",
        dry_run=False,
        log=station_sync.PlaylistLog("Playlist"),
    )

    assert changed is False
    assert song_path.read_bytes() == b"original"
    assert not song_path.with_suffix(".mp3.bak").exists()


def test_default_media_library_audit_existing_tags_refreshes_mismatches(tmp_path, monkeypatch) -> None:
    library = station_sync.DefaultMediaLibrary()
    song_path = tmp_path / "Artist - Song.mp3"
    song_path.write_bytes(b"mp3")
    tagged: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeEasyID3(dict):
        def __init__(self, _path: str) -> None:
            super().__init__(
                artist=["Wrong"],
                title=["Song"],
                date=["2020"],
                genre=["Old"],
                album=["Old Album"],
            )

    monkeypatch.setattr(station_sync_media, "EasyID3", FakeEasyID3)
    monkeypatch.setattr(
        station_sync_media,
        "tag_mp3",
        lambda *args, **kwargs: tagged.append((args, kwargs)),
    )

    refreshed = library.audit_existing_tags(
        [(Song(artist="Artist", title="Song", year="2026", album="Album"), song_path)],
        "Playlist",
        repair=True,
        log=station_sync.PlaylistLog("Playlist"),
    )

    assert refreshed == 1
    assert tagged == [
        (
            (str(song_path), "Artist", "Song", "2026", "Playlist", "Album"),
            {
                "log_prefix": "      ",
                "refresh_art": True,
                "apply_replaygain": False,
            },
        )
    ]


def test_default_media_library_genre_repair_skips_art_and_replaygain(
    tmp_path, monkeypatch
) -> None:
    library = station_sync.DefaultMediaLibrary()
    song_path = tmp_path / "Artist - Song.mp3"
    song_path.write_bytes(b"mp3")
    tagged: list[dict[str, object]] = []

    class FakeEasyID3(dict):
        def __init__(self, _path: str) -> None:
            super().__init__(
                artist=["Artist"],
                title=["Song"],
                date=["2026"],
                genre=["New Releases"],
                album=["Album"],
            )

    monkeypatch.setattr(station_sync_media, "EasyID3", FakeEasyID3)
    monkeypatch.setattr(
        station_sync_media,
        "tag_mp3",
        lambda *_args, **kwargs: tagged.append(kwargs),
    )

    mismatches = library.audit_existing_tags(
        [(Song(artist="Artist", title="Song", year="2026", album="Album"), song_path)],
        "Folk Rock",
        repair=True,
        log=station_sync.PlaylistLog("Folk Rock"),
    )

    assert mismatches == 1
    assert tagged == [
        {
            "log_prefix": "      ",
            "refresh_art": False,
            "apply_replaygain": False,
        }
    ]


def test_default_media_library_tag_audit_is_read_only_without_repair(
    tmp_path, monkeypatch
) -> None:
    library = station_sync.DefaultMediaLibrary()
    song_path = tmp_path / "Artist - Song.mp3"
    song_path.write_bytes(b"mp3")

    class FakeEasyID3(dict):
        def __init__(self, _path: str) -> None:
            super().__init__(artist=["Wrong"], title=["Song"])

    monkeypatch.setattr(station_sync_media, "EasyID3", FakeEasyID3)
    monkeypatch.setattr(
        station_sync_media,
        "tag_mp3",
        lambda *_args, **_kwargs: pytest.fail("read-only tag audit attempted repair"),
    )

    mismatches = library.audit_existing_tags(
        [(Song(artist="Artist", title="Song", year="2026"), song_path)],
        "Playlist",
        repair=False,
        log=station_sync.PlaylistLog("Playlist"),
    )

    assert mismatches == 1


def test_station_sync_helpers_classify_actions_and_write_duplicate_analysis(tmp_path) -> None:
    service = station_sync.StationSync(station_dir_resolver=lambda _slug: tmp_path)
    music_dir = tmp_path / "songs"
    music_dir.mkdir()
    existing = music_dir / "Artist - Existing.mp3"
    existing.write_bytes(b"mp3")
    songs = [
        Song(artist="Artist", title="Existing", year="2020"),
        Song(artist="Artist", title="Missing", year="2021"),
        Song(artist="Artist", title="Override", year="2022", override_url="https://youtu.be/x"),
    ]

    actions = service._build_playlist_actions(songs, music_dir)
    candidates = service._override_candidates(songs, music_dir)
    analysis_path = service._write_duplicate_analysis(
        tmp_path / "duplicate_analysis.log",
        {
            "A": [songs[0], songs[1]],
            "B": [Song(artist="Artist", title="Existing", year="2020")],
        },
    )

    assert [item.song.title for item in actions.existing_songs] == ["Existing"]
    assert [item.song.title for item in actions.missing_songs] == ["Missing"]
    assert actions.pending_overrides == 1
    assert candidates[0][1] == music_dir / "Artist - Override.mp3"
    assert "Songs appearing in multiple playlists" in analysis_path.read_text(encoding="utf-8")


def test_station_sync_run_handles_missing_playlist_dir(tmp_path) -> None:
    service = station_sync.StationSync(station_dir_resolver=lambda _slug: tmp_path / "Station")

    report = service.run(
        station_sync.SyncRequest(
            station_slug="missing",
            dry_run=True,
        )
    )

    assert report.playlist_reports == []
    assert report.duplicate_analysis_log == tmp_path / "Station" / "duplicate_analysis.log"
