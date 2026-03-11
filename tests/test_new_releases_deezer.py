#!/usr/bin/env python3
"""Unit tests for the standalone Deezer New Releases pipeline."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

deezer_new_releases = importlib.import_module(
    "neuralcast.pipelines.new_releases_deezer.main"
)


class DeezerNewReleasesTest(unittest.TestCase):
    @staticmethod
    def _fake_response(payload: dict, status_code: int = 200):
        class _Response:
            def __init__(self, payload: dict, status_code: int) -> None:
                self._payload = payload
                self.status_code = status_code
                self.headers: dict[str, str] = {}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

            def json(self) -> dict:
                return self._payload

        return _Response(payload, status_code)

    def test_load_station_artists_excludes_new_release_playlists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            playlists_dir = Path(tmpdir)
            pd.DataFrame([{"Artist": "Keep", "Title": "Song"}]).to_csv(
                playlists_dir / "Main.csv", index=False
            )
            pd.DataFrame([{"Artist": "Skip Spotify", "Title": "Song"}]).to_csv(
                playlists_dir / "New Releases.csv", index=False
            )
            pd.DataFrame([{"Artist": "Skip Deezer", "Title": "Song"}]).to_csv(
                playlists_dir / "New Releases Deezer.csv", index=False
            )

            artists, artist_tracks, artist_map = deezer_new_releases.load_station_artists(
                playlists_dir
            )

            self.assertEqual(artists, ["Keep"])
            self.assertEqual(artist_tracks["Keep"], {"Song"})
            self.assertEqual(
                sorted(path.name for path in artist_map["Keep"]),
                ["Main.csv"],
            )

    @patch.object(deezer_new_releases, "fetch_recent_releases")
    def test_build_new_releases_ranks_using_single_then_rank_then_date(
        self, fetch_recent_releases
    ) -> None:
        fetch_recent_releases.return_value = [
            deezer_new_releases.ArtistRelease(
                artist="Ghost",
                title="Album Cut",
                album="Album",
                year=2025,
                release_date=datetime(2025, 4, 25, tzinfo=UTC),
                track_id="1",
                rank=900,
                is_single=False,
                album_type="album",
            ),
            deezer_new_releases.ArtistRelease(
                artist="Ghost",
                title="Single Cut",
                album="Single",
                year=2025,
                release_date=datetime(2025, 4, 20, tzinfo=UTC),
                track_id="2",
                rank=100,
                is_single=True,
                album_type="single",
            ),
        ]

        releases = deezer_new_releases.build_new_releases(
            ["Ghost"],
            days=120,
            per_artist=1,
            min_rank=0,
            prefer_singles=True,
            known_tracks={"Ghost": {"Rats"}},
            cutoff=datetime(2025, 1, 1, tzinfo=UTC),
        )

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].title, "Single Cut")

    def test_fetch_recent_releases_skips_unreadable_tracks(self) -> None:
        with (
            patch.object(
                deezer_new_releases,
                "_resolve_artist",
                return_value={"id": "13208305", "name": "Atavistia"},
            ),
            patch.object(
                deezer_new_releases,
                "_iter_recent_albums",
                return_value=[
                    (
                        datetime(2026, 5, 15, tzinfo=UTC),
                        {
                            "id": "901156102",
                            "title": "Old Gods Awaken",
                            "record_type": "album",
                        },
                    )
                ],
            ),
            patch.object(
                deezer_new_releases,
                "_album_tracks_by_artist",
                return_value=[
                    {
                        "id": "3785083792",
                        "title": "Raise all Thy Horns",
                        "readable": False,
                        "track_position": 1,
                        "disk_number": 1,
                        "rank": 100000,
                    },
                    {
                        "id": "3785083802",
                        "title": "Mystic Tavern",
                        "readable": True,
                        "track_position": 2,
                        "disk_number": 1,
                        "rank": 46502,
                    },
                ],
            ),
        ):
            releases = deezer_new_releases.fetch_recent_releases(
                "Atavistia",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"Timeless Despair"},
            )

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].title, "Mystic Tavern")
        self.assertEqual(releases[0].track_id, "3785083802")

    def test_resolve_artist_uses_cached_artist_without_track_verification(self) -> None:
        cache = deezer_new_releases.ArtistIDCache(entries={}, dirty=False)
        cache.set("Ghost", "8506054")

        with (
            patch.object(
                deezer_new_releases,
                "_fetch_artist_by_id",
                return_value={"id": "8506054", "name": "Ghost"},
            ) as fetch_artist,
            patch.object(deezer_new_releases, "_artist_has_known_track") as has_track,
            patch.object(deezer_new_releases, "_best_artist_match") as best_match,
            patch.object(
                deezer_new_releases, "_search_artist_using_known_tracks"
            ) as search_known,
        ):
            artist = deezer_new_releases._resolve_artist(
                "Ghost", {"Rats", "Square Hammer"}, cache
            )

        self.assertEqual(artist, {"id": "8506054", "name": "Ghost"})
        fetch_artist.assert_called_once_with("8506054")
        has_track.assert_not_called()
        best_match.assert_not_called()
        search_known.assert_not_called()

    def test_deezer_get_retries_after_quota_error_code_four(self) -> None:
        quota_payload = {
            "error": {
                "type": "Exception",
                "message": "Quota limit exceeded",
                "code": 4,
            }
        }
        success_payload = {"data": [{"id": 1}]}
        with (
            patch.object(
                deezer_new_releases.SESSION,
                "get",
                side_effect=[
                    self._fake_response(quota_payload),
                    self._fake_response(success_payload),
                ],
            ) as session_get,
            patch.object(deezer_new_releases.time, "sleep") as sleep_mock,
        ):
            payload = deezer_new_releases._deezer_get(
                "/search/artist", params={"q": "Ghost"}
            )

        self.assertEqual(payload, success_payload)
        self.assertEqual(session_get.call_count, 2)
        self.assertTrue(sleep_mock.called)

    def test_save_new_releases_writes_isolated_files_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir)
            playlists_dir = station_dir / "playlists"
            playlists_dir.mkdir(parents=True)
            release = deezer_new_releases.ArtistRelease(
                artist="Ghost",
                title="Peacefield",
                album="Skeletá",
                year=2025,
                release_date=datetime(2025, 4, 25, tzinfo=UTC),
                track_id="123",
                rank=555,
                is_single=False,
                album_type="album",
                validated=False,
            )

            deezer_new_releases.save_new_releases(
                playlists_dir, [release], dry_run=False
            )

            csv_path = playlists_dir / "New Releases Deezer.csv"
            metadata_path = station_dir / "metadata" / "New Releases Deezer.metadata.json"
            spotify_csv_path = playlists_dir / "New Releases.csv"
            self.assertTrue(csv_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertFalse(spotify_csv_path.exists())

            df = pd.read_csv(csv_path, dtype=str).fillna("")
            self.assertEqual(list(df.columns), ["Artist", "Title", "Album", "Year", "Validated"])
            self.assertEqual(df.iloc[0]["Validated"], "False")

            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            entries = payload["entries"]
            self.assertEqual(len(entries), 1)
            entry = next(iter(entries.values()))
            self.assertEqual(entry["TrackID"], "123")
            self.assertEqual(entry["Rank"], 555)

    def test_save_artist_id_cache_skips_writes_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            playlists_dir = Path(tmpdir) / "playlists"
            playlists_dir.mkdir(parents=True)
            cache = deezer_new_releases.ArtistIDCache(entries={}, dirty=False)
            cache.set("Ghost", "8506054")

            deezer_new_releases.save_artist_id_cache(
                playlists_dir, cache, dry_run=True
            )

            cache_path = Path(tmpdir) / "metadata" / "DeezerArtistIDs.json"
            self.assertFalse(cache_path.exists())


if __name__ == "__main__":
    unittest.main()
