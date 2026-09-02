#!/usr/bin/env python3
"""Boundary tests for the station sync service."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from neuralcast.metadata.storage import metadata_key
from neuralcast.models import Song, ValidationResult
from neuralcast.pipelines.station_sync import (
    MediaLibrary,
    PlaylistLog,
    StationSync,
    SyncRequest,
    TrackResolver,
)
from neuralcast.playlists import library as playlist_library


class FakeResolver(TrackResolver):
    def __init__(
        self,
        *,
        available: dict[tuple[str, str], bool] | None = None,
        validations: dict[tuple[str, str], ValidationResult] | None = None,
    ) -> None:
        self.available = available or {}
        self.validations = validations or {}

    def is_available(self, song: Song) -> bool:
        return self.available.get((song.artist, song.title), True)

    def validate_song(self, song: Song) -> ValidationResult:
        return self.validations.get(
            (song.artist, song.title),
            ValidationResult(song=song.model_copy(update={"validated": True})),
        )

    def backfill_album(
        self,
        song: Song,
        *,
        log=print,
    ) -> tuple[Song, bool]:
        return song, False


class FakeMediaLibrary(MediaLibrary):
    def __init__(self) -> None:
        self.downloads: list[Path] = []

    def apply_override(
        self,
        song: Song,
        song_path: Path | None,
        playlist_name: str,
        *,
        dry_run: bool,
        log: PlaylistLog,
    ) -> bool:
        return False

    def audit_existing_tags(
        self,
        existing_songs: list[tuple[Song, Path]],
        playlist_name: str,
        *,
        repair: bool,
        log: PlaylistLog,
    ) -> int:
        return 0

    def download_song(
        self,
        song: Song,
        song_path: Path,
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> None:
        song_path.parent.mkdir(parents=True, exist_ok=True)
        song_path.write_bytes(b"fake mp3")
        self.downloads.append(song_path)

    def delete_file(
        self,
        song_path: Path,
        *,
        log: PlaylistLog,
    ) -> None:
        if song_path.exists():
            song_path.unlink()


class StationSyncBoundaryTest(unittest.TestCase):
    def test_dry_run_reports_invalid_new_release_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir) / "Station"
            playlists_dir = station_dir / "playlists"
            metadata_dir = station_dir / "metadata"
            songs_dir = station_dir / "songs"
            playlists_dir.mkdir(parents=True)
            metadata_dir.mkdir()
            songs_dir.mkdir()

            playlist_file = playlists_dir / "New Releases.csv"
            pd.DataFrame(
                [
                    {
                        "Artist": "Artist A",
                        "Title": "Remove Me",
                        "Album": "Album A",
                        "Year": "2026",
                        "Validated": False,
                    },
                    {
                        "Artist": "Artist B",
                        "Title": "Keep Me",
                        "Album": "Album B",
                        "Year": "2026",
                        "Validated": True,
                    },
                ]
            ).to_csv(playlist_file, index=False)

            metadata_entries = {
                metadata_key("Artist A", "Remove Me", "Album A", "2026"): {"provider": "test"},
                metadata_key("Artist B", "Keep Me", "Album B", "2026"): {"provider": "test"},
            }
            metadata_file = metadata_dir / "New Releases.metadata.json"
            metadata_file.write_text(
                json.dumps({"entries": metadata_entries}, indent=2) + "\n",
                encoding="utf-8",
            )

            resolver = FakeResolver(available={("Artist A", "Remove Me"): False})
            service = StationSync(
                resolver=resolver,
                media_library=FakeMediaLibrary(),
                station_dir_resolver=lambda _slug: station_dir,
            )

            report = service.run(
                SyncRequest(
                    station_slug="test-station",
                    dry_run=True,
                )
            )

            self.assertEqual(len(report.playlist_reports), 1)
            playlist_report = report.playlist_reports[0]
            self.assertEqual(playlist_report.name, "New Releases")
            self.assertEqual(playlist_report.removed_count, 1)
            self.assertEqual(playlist_report.final_song_count, 1)

            persisted_df = pd.read_csv(playlist_file, dtype=str).fillna("")
            self.assertEqual(list(persisted_df["Title"]), ["Remove Me", "Keep Me"])

            persisted_metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(persisted_metadata["entries"].keys()),
                sorted(metadata_entries),
            )
            self.assertFalse(report.duplicate_analysis_log.exists())

    def test_run_downloads_validated_missing_song_via_media_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir) / "Station"
            playlists_dir = station_dir / "playlists"
            songs_dir = station_dir / "songs"
            playlists_dir.mkdir(parents=True)
            songs_dir.mkdir()

            playlist_file = playlists_dir / "Synthwave.csv"
            pd.DataFrame(
                [
                    {
                        "Artist": "Timecop1983",
                        "Title": "On the Run",
                        "Album": "",
                        "Year": "2016",
                        "Validated": False,
                    }
                ]
            ).to_csv(playlist_file, index=False)

            validated_song = Song(
                artist="Timecop1983",
                title="On the Run",
                album=None,
                year="2016",
                validated=True,
            )
            resolver = FakeResolver(
                validations={
                    ("Timecop1983", "On the Run"): ValidationResult(song=validated_song)
                }
            )
            media_library = FakeMediaLibrary()
            service = StationSync(
                resolver=resolver,
                media_library=media_library,
                station_dir_resolver=lambda _slug: station_dir,
            )

            report = service.run(
                SyncRequest(
                    station_slug="test-station",
                    dry_run=False,
                )
            )

            playlist_report = report.playlist_reports[0]
            self.assertEqual(playlist_report.downloaded_count, 1)
            self.assertEqual(playlist_report.failed_count, 0)
            self.assertEqual(len(media_library.downloads), 1)
            self.assertTrue(media_library.downloads[0].exists())


if __name__ == "__main__":
    unittest.main()


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | None, int]]:
    snapshot: dict[str, tuple[str, bytes | None, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_dir():
            snapshot[relative] = ("dir", None, path.stat().st_mtime_ns)
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
    return snapshot


def test_dry_run_leaves_station_tree_unchanged(tmp_path, monkeypatch) -> None:
    station_dir = tmp_path / "Station"
    playlists_dir = station_dir / "playlists"
    music_dir = station_dir / "songs" / "Music"
    playlists_dir.mkdir(parents=True)
    music_dir.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "Artist": "[DEL] Delete Artist",
                "Title": "Old Song",
                "Album": "Old",
                "Year": "2020",
                "Validated": False,
            },
            {
                "Artist": "Keep Artist",
                "Title": "Keep Song",
                "Album": "Keep",
                "Year": "2021",
                "Validated": True,
            },
            {
                "Artist": "Invalid Artist",
                "Title": "Invalid Song",
                "Album": "Invalid",
                "Year": "2022",
                "Validated": False,
            },
        ]
    ).to_csv(playlists_dir / "Music.csv", index=False)
    pd.DataFrame(columns=["Artist", "Title", "Album", "Year", "Validated"]).to_csv(
        playlists_dir / "Empty.csv", index=False
    )

    for name in (
        "Delete Artist - Old Song.mp3",
        "Keep Artist - Keep Song.mp3",
        "Invalid Artist - Invalid Song.mp3",
        "unexpected-name.mp3",
    ):
        (music_dir / name).write_bytes(name.encode())
    (station_dir / "duplicate_analysis.log").write_text(
        "original report\n", encoding="utf-8"
    )

    class FakeEasyID3(dict):
        def __init__(self, path: str) -> None:
            name = Path(path).stem
            if name == "unexpected-name":
                artist, title = "Loose Artist", "Loose Song"
            else:
                artist, title = name.split(" - ", 1)
            super().__init__(artist=[artist], title=[title], date=["2020"])

    monkeypatch.setattr(playlist_library, "EasyID3", FakeEasyID3)
    resolver = FakeResolver(
        validations={
            ("Invalid Artist", "Invalid Song"): ValidationResult(song=None),
            ("Loose Artist", "Loose Song"): ValidationResult(
                song=Song(
                    artist="Loose Artist",
                    title="Loose Song",
                    year="2020",
                    validated=True,
                )
            ),
        }
    )
    service = StationSync(
        resolver=resolver,
        media_library=FakeMediaLibrary(),
        station_dir_resolver=lambda _slug: station_dir,
    )
    before = _tree_snapshot(station_dir)

    report = service.run(SyncRequest(station_slug="test", dry_run=True))

    assert _tree_snapshot(station_dir) == before
    music_report = next(
        item for item in report.playlist_reports if item.name == "Music"
    )
    assert music_report.removed_count == 1
    assert music_report.added_from_files == 1
    assert music_report.planned_download_count == 0


def test_dry_and_apply_build_matching_core_plans(tmp_path) -> None:
    class AuditingMediaLibrary(FakeMediaLibrary):
        def __init__(self) -> None:
            super().__init__()
            self.repair_modes: list[bool] = []

        def audit_existing_tags(
            self,
            existing_songs: list[tuple[Song, Path]],
            playlist_name: str,
            *,
            repair: bool,
            log: PlaylistLog,
        ) -> int:
            self.repair_modes.append(repair)
            return len(existing_songs)

    def build_station(root: Path) -> Path:
        playlists = root / "playlists"
        music = root / "songs" / "Music"
        playlists.mkdir(parents=True)
        music.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "Artist": "Artist",
                    "Title": "Existing",
                    "Album": "Album",
                    "Year": "2020",
                    "Validated": True,
                },
                {
                    "Artist": "Artist",
                    "Title": "Missing",
                    "Album": "Album",
                    "Year": "2021",
                    "Validated": True,
                },
            ]
        ).to_csv(playlists / "Music.csv", index=False)
        (music / "Artist - Existing.mp3").write_bytes(b"mp3")
        return root

    dry_station = build_station(tmp_path / "dry")
    apply_station = build_station(tmp_path / "apply")
    dry_media = AuditingMediaLibrary()
    apply_media = AuditingMediaLibrary()
    dry_report = StationSync(
        resolver=FakeResolver(),
        media_library=dry_media,
        station_dir_resolver=lambda _slug: dry_station,
    ).run(SyncRequest(station_slug="test", dry_run=True))
    apply_report = StationSync(
        resolver=FakeResolver(),
        media_library=apply_media,
        station_dir_resolver=lambda _slug: apply_station,
    ).run(SyncRequest(station_slug="test", dry_run=False))

    dry_playlist = dry_report.playlist_reports[0]
    apply_playlist = apply_report.playlist_reports[0]
    assert (
        dry_playlist.planned_download_count
        == apply_playlist.planned_download_count
        == 1
    )
    assert dry_playlist.removed_count == apply_playlist.removed_count == 0
    assert dry_playlist.tag_repair_count == apply_playlist.tag_repair_count == 1
    assert dry_media.repair_modes == [False]
    assert apply_media.repair_modes == [True]
