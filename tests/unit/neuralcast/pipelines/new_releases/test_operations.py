#!/usr/bin/env python3
"""Unit tests for the New Releases pipeline."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

new_releases = importlib.import_module("neuralcast.pipelines.new_releases.operations")
new_releases_runtime = importlib.import_module(
    "neuralcast.pipelines.new_releases.runtime"
)


class NewReleasesTest(unittest.TestCase):
    def setUp(self) -> None:
        new_releases._KNOWN_TRACK_MATCH_CACHE.clear()
        new_releases._ALBUM_GENRE_CACHE.clear()
        new_releases._MB_RECORDING_ARTIST_CACHE.clear()

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
            pd.DataFrame([{"Artist": "Skip New Releases", "Title": "Song"}]).to_csv(
                playlists_dir / "New Releases.csv", index=False
            )

            artists, artist_tracks, artist_map = new_releases.load_station_artists(
                playlists_dir
            )

            self.assertEqual(artists, ["Keep"])
            self.assertEqual(artist_tracks["Keep"], {"Song"})
            self.assertEqual(
                sorted(path.name for path in artist_map["Keep"]),
                ["Main.csv"],
            )

    def test_load_release_exclusions_normalizes_artist_and_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            playlists_dir = Path(tmpdir) / "playlists"
            metadata_dir = Path(tmpdir) / "metadata"
            playlists_dir.mkdir()
            metadata_dir.mkdir()
            (metadata_dir / "New Releases.exclusions.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {"Artist": "Black Rose", "Title": "White Cat"},
                            {"Artist": "", "Title": "Ignored"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            exclusions = new_releases.load_release_exclusions(playlists_dir)

            self.assertEqual(exclusions, {"blackrosewhitecat"})

    @patch.object(new_releases, "fetch_recent_releases")
    def test_build_new_releases_ranks_using_single_then_rank_then_date(
        self, fetch_recent_releases
    ) -> None:
        fetch_recent_releases.return_value = [
            new_releases.ArtistRelease(
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
            new_releases.ArtistRelease(
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

        releases = new_releases.build_new_releases(
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

    @patch.object(new_releases, "fetch_recent_releases")
    def test_build_new_releases_counts_existing_tracks_toward_per_artist_limit(
        self, fetch_recent_releases
    ) -> None:
        fetch_recent_releases.return_value = [
            new_releases.ArtistRelease(
                artist="Ghost",
                title="New Cut",
                album="Album",
                year=2026,
                release_date=datetime(2026, 5, 1, tzinfo=UTC),
                track_id="new",
            )
        ]

        releases = new_releases.build_new_releases(
            ["Ghost"],
            days=120,
            per_artist=3,
            known_tracks={"Ghost": {"Rats"}},
            existing_artist_counts={"ghost": 3},
        )

        self.assertEqual(releases, [])
        fetch_recent_releases.assert_not_called()

    @patch.object(new_releases, "fetch_recent_releases")
    def test_build_new_releases_skips_station_exclusions(
        self, fetch_recent_releases
    ) -> None:
        fetch_recent_releases.return_value = [
            new_releases.ArtistRelease(
                artist="Black Rose",
                title="White Cat",
                album="Electric Dreams",
                year=2026,
                release_date=datetime(2026, 6, 9, tzinfo=UTC),
                track_id="blocked",
            )
        ]

        releases = new_releases.build_new_releases(
            ["Black Rose"],
            days=120,
            known_tracks={"Black Rose": {"No Point Runnin'"}},
            excluded_keys={"blackrosewhitecat"},
        )

        self.assertEqual(releases, [])

    def test_fetch_recent_releases_skips_unreadable_tracks(self) -> None:
        with (
            patch.object(
                new_releases,
                "_resolve_artist",
                return_value={"id": "13208305", "name": "Atavistia"},
            ),
            patch.object(
                new_releases,
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
                new_releases,
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
            patch.object(
                new_releases,
                "_is_probable_old_catalog_release",
                return_value=False,
            ),
            patch.object(
                new_releases,
                "_known_artist_genre_ids",
                return_value=frozenset(),
            ),
        ):
            releases = new_releases.fetch_recent_releases(
                "Atavistia",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"Timeless Despair"},
            )

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].title, "Mystic Tavern")
        self.assertEqual(releases[0].track_id, "3785083802")

    def test_fetch_recent_releases_prefers_highest_ranked_album_track(self) -> None:
        with (
            patch.object(
                new_releases,
                "_resolve_artist",
                return_value={"id": "123", "name": "Ghost"},
            ),
            patch.object(
                new_releases,
                "_iter_recent_albums",
                return_value=[
                    (
                        datetime(2026, 5, 15, tzinfo=UTC),
                        {
                            "id": "456",
                            "title": "Skeleta",
                            "record_type": "album",
                            "genre_id": 464,
                        },
                    )
                ],
            ),
            patch.object(
                new_releases,
                "_album_tracks_by_artist",
                return_value=[
                    {
                        "id": "first",
                        "title": "Opening Track",
                        "readable": True,
                        "track_position": 1,
                        "disk_number": 1,
                        "rank": 100,
                    },
                    {
                        "id": "best",
                        "title": "Lead Single",
                        "readable": True,
                        "track_position": 2,
                        "disk_number": 1,
                        "rank": 900,
                    },
                ],
            ),
            patch.object(
                new_releases,
                "_known_artist_genre_ids",
                return_value=frozenset({464}),
            ),
            patch.object(
                new_releases,
                "_known_artist_genres_are_ambiguous",
                return_value=False,
            ),
            patch.object(
                new_releases,
                "_is_probable_old_catalog_release",
                return_value=False,
            ),
        ):
            releases = new_releases.fetch_recent_releases(
                "Ghost",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"Rats"},
            )

        self.assertEqual([release.title for release in releases], ["Lead Single"])

    def test_fetch_recent_releases_rejects_merged_artist_genre_mismatch(self) -> None:
        with (
            patch.object(
                new_releases,
                "_resolve_artist",
                return_value={"id": "357887", "name": "Parsifal"},
            ),
            patch.object(
                new_releases,
                "_known_artist_genre_ids",
                return_value=frozenset({85, 464}),
            ),
            patch.object(
                new_releases,
                "_known_artist_genres_are_ambiguous",
                return_value=False,
            ),
            patch.object(
                new_releases,
                "_iter_recent_albums",
                return_value=[
                    (
                        datetime(2026, 5, 12, tzinfo=UTC),
                        {
                            "id": "981531511",
                            "title": "MELLONTAS",
                            "record_type": "single",
                            "genre_id": 132,
                        },
                    )
                ],
            ),
            patch.object(
                new_releases,
                "_album_genre_ids",
                return_value=frozenset({132}),
            ),
            patch.object(new_releases, "_album_tracks_by_artist") as album_tracks,
        ):
            releases = new_releases.fetch_recent_releases(
                "Parsifal",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"Storming the Reaper"},
            )

        self.assertEqual(releases, [])
        album_tracks.assert_not_called()

    def test_fetch_recent_releases_rejects_same_genre_musicbrainz_identity_mismatch(
        self,
    ) -> None:
        with (
            patch.object(
                new_releases,
                "_resolve_artist",
                return_value={"id": "424248", "name": "Black Rose"},
            ),
            patch.object(
                new_releases,
                "_known_artist_genre_ids",
                return_value=frozenset({152}),
            ),
            patch.object(
                new_releases,
                "_known_artist_genres_are_ambiguous",
                return_value=True,
            ),
            patch.object(
                new_releases,
                "_known_musicbrainz_artist_ids",
                return_value=frozenset({"british-band"}),
            ),
            patch.object(
                new_releases,
                "_musicbrainz_recording_artist_ids",
                return_value=frozenset({"swedish-band"}),
            ),
            patch.object(
                new_releases,
                "_iter_recent_albums",
                return_value=[
                    (
                        datetime(2026, 7, 20, tzinfo=UTC),
                        {
                            "id": "1018928021",
                            "title": "Divine Sign (single)",
                            "record_type": "single",
                            "genre_id": 152,
                        },
                    )
                ],
            ),
            patch.object(
                new_releases,
                "_album_tracks_by_artist",
                return_value=[
                    {
                        "id": "4126501871",
                        "title": "Divine Sign",
                        "readable": True,
                        "track_position": 1,
                        "disk_number": 1,
                        "rank": 100000,
                    }
                ],
            ),
            patch.object(
                new_releases,
                "_is_probable_old_catalog_release",
                return_value=False,
            ) as old_catalog_check,
        ):
            releases = new_releases.fetch_recent_releases(
                "Black Rose",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"No Point Runnin'", "Sucker For Your Love"},
            )

        self.assertEqual(releases, [])
        old_catalog_check.assert_not_called()

    def test_fetch_recent_releases_skips_tracks_already_in_station_catalog(self) -> None:
        with (
            patch.object(
                new_releases,
                "_resolve_artist",
                return_value={"id": "123", "name": "Motörhead"},
            ),
            patch.object(
                new_releases,
                "_known_artist_genre_ids",
                return_value=frozenset(),
            ),
            patch.object(
                new_releases,
                "_iter_recent_albums",
                return_value=[
                    (
                        datetime(2026, 5, 12, tzinfo=UTC),
                        {
                            "id": "456",
                            "title": "Neat Neat Neat",
                            "record_type": "single",
                        },
                    )
                ],
            ),
            patch.object(
                new_releases,
                "_album_tracks_by_artist",
                return_value=[
                    {
                        "id": "789",
                        "title": "Neat Neat Neat",
                        "readable": True,
                        "track_position": 1,
                        "disk_number": 1,
                        "rank": 100000,
                    }
                ],
            ),
        ):
            releases = new_releases.fetch_recent_releases(
                "Motörhead",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"Neat Neat Neat"},
            )

        self.assertEqual(releases, [])

    def test_is_alt_or_reissue_rejects_archival_and_alternate_versions(self) -> None:
        cases = [
            ("Mama Kin (2024 Mix)", ""),
            ("Eire (Outtake)", ""),
            ("Natural Disaster (Instrumental)", ""),
            ("Kiss The Sky (Unreleased Track)", ""),
            ("Stay Clean (Sound check)", ""),
            ("Don't Tell Me You Love Me (2026)", ""),
            ("Going Under", "Play Dirty (Bonus Track Edition)"),
        ]
        for title, album in cases:
            with self.subTest(title=title, album=album):
                self.assertTrue(new_releases._is_alt_or_reissue(title, album))

    def test_is_probable_old_catalog_release_uses_musicbrainz_dates(self) -> None:
        new_releases._MB_RELEASE_CACHE.clear()
        with patch.object(
            new_releases,
            "_musicbrainz_earliest_release_date",
            side_effect=[
                datetime(1996, 9, 1, tzinfo=UTC),
                None,
            ],
        ):
            result = new_releases._is_probable_old_catalog_release(
                "Aerosmith",
                "Institutional Man",
                "Angry Machines",
                datetime(2026, 2, 1, tzinfo=UTC),
            )

        self.assertTrue(result)

    def test_musicbrainz_earliest_release_date_ignores_other_artists(self) -> None:
        new_releases._MB_RELEASE_CACHE.clear()
        with patch.object(
            new_releases.musicbrainzngs,
            "search_releases",
            return_value={
                "release-list": [
                    {
                        "title": "Old Gods Awaken",
                        "date": "2020-10-30",
                        "artist-credit-phrase": "Black Tar Superstar",
                    },
                    {
                        "title": "Old Gods Awaken",
                        "date": "2026-05-15",
                        "artist-credit-phrase": "Atavistia",
                    },
                ]
            },
        ):
            result = new_releases._musicbrainz_earliest_release_date(
                "Atavistia", "Old Gods Awaken", entity="release"
            )

        self.assertEqual(result, datetime(2026, 5, 15, tzinfo=UTC))

    def test_fetch_recent_releases_skips_musicbrainz_old_catalog_release(self) -> None:
        with (
            patch.object(
                new_releases,
                "_resolve_artist",
                return_value={"id": "123", "name": "Aerosmith"},
            ),
            patch.object(
                new_releases,
                "_iter_recent_albums",
                return_value=[
                    (
                        datetime(2026, 2, 1, tzinfo=UTC),
                        {
                            "id": "456",
                            "title": "Mama Kin (2024 Mix)",
                            "record_type": "single",
                        },
                    )
                ],
            ),
            patch.object(
                new_releases,
                "_album_tracks_by_artist",
                return_value=[
                    {
                        "id": "789",
                        "title": "Mama Kin (2024 Mix)",
                        "readable": True,
                        "track_position": 1,
                        "disk_number": 1,
                        "rank": 80000,
                    }
                ],
            ),
            patch.object(
                new_releases,
                "_is_probable_old_catalog_release",
                return_value=True,
            ),
            patch.object(
                new_releases,
                "_known_artist_genre_ids",
                return_value=frozenset(),
            ),
        ):
            releases = new_releases.fetch_recent_releases(
                "Aerosmith",
                cutoff=datetime(2026, 1, 1, tzinfo=UTC),
                known_titles={"Dream On"},
            )

        self.assertEqual(releases, [])

    def test_resolve_artist_revalidates_cached_artist_with_known_track(self) -> None:
        cache = new_releases.ArtistIDCache(entries={}, dirty=False)
        cache.set("Ghost", "8506054")

        with (
            patch.object(
                new_releases,
                "_fetch_artist_by_id",
                return_value={"id": "8506054", "name": "Ghost"},
            ) as fetch_artist,
            patch.object(new_releases, "_exact_artist_matches", return_value=[]),
            patch.object(
                new_releases, "_artist_has_known_track", return_value=True
            ) as has_track,
            patch.object(new_releases, "_best_artist_match") as best_match,
            patch.object(new_releases, "_search_artist_using_known_tracks") as search_known,
        ):
            artist = new_releases._resolve_artist(
                "Ghost", {"Rats", "Square Hammer"}, cache
            )

        self.assertEqual(artist, {"id": "8506054", "name": "Ghost"})
        fetch_artist.assert_called_once_with("8506054")
        has_track.assert_called_once_with(
            "8506054", "Ghost", {"Rats", "Square Hammer"}
        )
        best_match.assert_not_called()
        search_known.assert_not_called()

    def test_resolve_artist_keeps_name_matching_cache_when_verification_is_down(
        self,
    ) -> None:
        cache = new_releases.ArtistIDCache(entries={"ghost": "8506054"})

        with (
            patch.object(
                new_releases,
                "_fetch_artist_by_id",
                return_value={"id": "8506054", "name": "Ghost"},
            ),
            patch.object(
                new_releases, "_artist_has_known_track", return_value=None
            ),
            patch.object(new_releases, "_best_artist_match") as best_match,
        ):
            artist = new_releases._resolve_artist("Ghost", {"Rats"}, cache)

        self.assertEqual(artist, {"id": "8506054", "name": "Ghost"})
        self.assertEqual(cache.get("Ghost"), "8506054")
        self.assertFalse(cache.dirty)
        best_match.assert_not_called()

    def test_artist_has_known_track_requires_candidate_artist_id(self) -> None:
        with patch.object(
            new_releases,
            "_deezer_get",
            return_value={
                "data": [
                    {
                        "title": "On and On",
                        "artist": {"id": "wrong", "name": "Raven"},
                    }
                ]
            },
        ):
            result = new_releases._artist_has_known_track(
                "59914", "Raven", {"On and On"}
            )

        self.assertFalse(result)

    def test_known_artist_genres_use_best_supported_known_album(self) -> None:
        with (
            patch.object(
                new_releases,
                "_find_known_artist_tracks",
                return_value=[
                    {"album": {"id": "wrong"}},
                    {"album": {"id": "correct"}},
                    {"album": {"id": "correct"}},
                ],
            ),
            patch.object(
                new_releases,
                "_album_genre_ids",
                side_effect=lambda album_id: (
                    frozenset({152}) if album_id == "correct" else frozenset({116})
                ),
            ),
        ):
            genre_ids = new_releases._known_artist_genre_ids(
                "424248",
                "Black Rose",
                {"I Don't Believe It", "No Point Runnin'", "Sucker For Your Love"},
            )

        self.assertEqual(genre_ids, frozenset({152}))

    def test_best_artist_match_uses_known_track_id_for_duplicate_names(self) -> None:
        exact_matches = [
            {"id": "wrong", "name": "Raven"},
            {"id": "59914", "name": "Raven"},
        ]

        def has_known_track(
            artist_id: str, artist_name: str, known_titles: set[str]
        ) -> bool:
            return artist_id == "59914"

        with (
            patch.object(
                new_releases, "_exact_artist_matches", return_value=exact_matches
            ),
            patch.object(
                new_releases,
                "_artist_has_known_track",
                side_effect=has_known_track,
            ),
        ):
            artist = new_releases._best_artist_match("Raven", {"On and On"})

        self.assertEqual(artist, {"id": "59914", "name": "Raven"})

    def test_best_artist_match_skips_when_known_tracks_do_not_match(self) -> None:
        with (
            patch.object(
                new_releases,
                "_exact_artist_matches",
                return_value=[{"id": "wrong", "name": "Legend"}],
            ),
            patch.object(new_releases, "_artist_has_known_track", return_value=False),
        ):
            artist = new_releases._best_artist_match(
                "Legend", {"Death in the Nursery"}
            )

        self.assertIsNone(artist)

    def test_exact_artist_matches_are_accent_insensitive(self) -> None:
        with patch.object(
            new_releases,
            "_deezer_get",
            return_value={
                "data": [
                    {"id": "108315", "name": "Týr"},
                    {"id": "78901512", "name": "TYR"},
                    {"id": "other", "name": "Tyra"},
                ]
            },
        ):
            matches = new_releases._exact_artist_matches("Tyr")

        self.assertEqual(
            matches,
            [
                {"id": "108315", "name": "Týr"},
                {"id": "78901512", "name": "TYR"},
            ],
        )

    def test_resolve_artist_revalidates_ambiguous_cached_artist(self) -> None:
        cache = new_releases.ArtistIDCache(entries={"raven": "wrong"}, dirty=False)

        with (
            patch.object(
                new_releases,
                "_fetch_artist_by_id",
                return_value={"id": "wrong", "name": "Raven"},
            ),
            patch.object(
                new_releases,
                "_exact_artist_matches",
                return_value=[
                    {"id": "wrong", "name": "Raven"},
                    {"id": "59914", "name": "Raven"},
                ],
            ),
            patch.object(new_releases, "_artist_has_known_track", return_value=False),
            patch.object(
                new_releases,
                "_best_artist_match",
                return_value={"id": "59914", "name": "Raven"},
            ) as best_match,
            patch.object(
                new_releases, "_search_artist_using_known_tracks"
            ) as search_known,
        ):
            artist = new_releases._resolve_artist("Raven", {"On and On"}, cache)

        self.assertEqual(artist, {"id": "59914", "name": "Raven"})
        self.assertEqual(cache.get("Raven"), "59914")
        best_match.assert_called_once_with("Raven", {"On and On"})
        search_known.assert_not_called()

    def test_resolve_artist_accepts_cached_alias_when_known_tracks_match(self) -> None:
        cache = new_releases.ArtistIDCache(entries={"rhapsody": "12061"}, dirty=False)

        with (
            patch.object(
                new_releases,
                "_fetch_artist_by_id",
                return_value={"id": "12061", "name": "Rhapsody of Fire"},
            ),
            patch.object(new_releases, "_artist_has_known_track", return_value=True),
            patch.object(
                new_releases,
                "_exact_artist_matches",
                return_value=[
                    {"id": "20", "name": "Rhapsody"},
                    {"id": "290802041", "name": "Rhapsody"},
                ],
            ),
            patch.object(new_releases, "_best_artist_match") as best_match,
            patch.object(
                new_releases, "_search_artist_using_known_tracks"
            ) as search_known,
        ):
            artist = new_releases._resolve_artist(
                "Rhapsody", {"Emerald Sword"}, cache
            )

        self.assertEqual(artist, {"id": "12061", "name": "Rhapsody of Fire"})
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
                new_releases.SESSION,
                "get",
                side_effect=[
                    self._fake_response(quota_payload),
                    self._fake_response(success_payload),
                ],
            ) as session_get,
            patch.object(new_releases.time, "sleep") as sleep_mock,
        ):
            payload = new_releases._deezer_get("/search/artist", params={"q": "Ghost"})

        self.assertEqual(payload, success_payload)
        self.assertEqual(session_get.call_count, 2)
        self.assertTrue(sleep_mock.called)

    def test_resolve_destination_playlist_prefers_exact_title_match(self) -> None:
        release = new_releases.ArtistRelease(
            artist="Ghost",
            title="Peacefield",
            album="Skeleta",
            year=2025,
            release_date=datetime(2025, 4, 25, tzinfo=UTC),
            track_id="123",
        )
        path_a = Path("/tmp/A.csv")
        path_b = Path("/tmp/B.csv")
        artist_playlist_map = {
            "Ghost": {
                path_a: {"Rats"},
                path_b: {"Peacefield"},
            }
        }

        destination = new_releases._resolve_destination_playlist(
            release, artist_playlist_map
        )

        self.assertEqual(destination, path_b)

    def test_move_outdated_releases_appends_to_genre_playlist_and_moves_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir)
            playlists_dir = station_dir / "playlists"
            audio_root = station_dir / "songs"
            source_dir = audio_root / "New Releases"
            destination_dir = audio_root / "Gothic Metal"
            playlists_dir.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            destination_dir.mkdir(parents=True)

            destination_csv = playlists_dir / "Gothic Metal.csv"
            pd.DataFrame(
                [
                    {
                        "Artist": "Ghost",
                        "Title": "Rats",
                        "Album": "Prequelle",
                        "Year": "2018",
                        "Validated": True,
                    }
                ]
            ).to_csv(destination_csv, index=False)

            audio_path = source_dir / "Ghost - Peacefield.mp3"
            audio_path.write_bytes(b"fake mp3")

            release = new_releases.ArtistRelease(
                artist="Ghost",
                title="Peacefield",
                album="Skeleta",
                year=2025,
                release_date=datetime(2025, 1, 1, tzinfo=UTC),
                track_id="123",
                album_type="album",
                is_single=False,
                validated=False,
            )
            artist_playlist_map = {
                "Ghost": {
                    destination_csv: {"Peacefield", "Rats"},
                }
            }

            with patch.object(new_releases, "_promote_release_album", return_value=True), patch.object(
                new_releases, "tag_mp3"
            ) as tag_mp3:
                new_releases.move_outdated_releases(
                    [release],
                    artist_playlist_map,
                    audio_root,
                    "New Releases",
                    dry_run=False,
                )

            moved_audio = destination_dir / "Ghost - Peacefield.mp3"
            self.assertFalse(audio_path.exists())
            self.assertTrue(moved_audio.exists())
            tag_mp3.assert_called_once_with(
                str(moved_audio),
                "Ghost",
                "Peacefield",
                "2025",
                "Gothic Metal",
                "Skeleta",
                log_prefix="      ",
                refresh_art=True,
                apply_replaygain=False,
            )

            df = pd.read_csv(destination_csv, dtype=str).fillna("")
            self.assertEqual(list(df["Title"]), ["Rats", "Peacefield"])

    def test_move_outdated_release_updates_destination_genre_without_album_refresh(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir)
            playlists_dir = station_dir / "playlists"
            source_dir = station_dir / "songs" / "New Releases"
            destination_dir = station_dir / "songs" / "Folk Rock"
            playlists_dir.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            destination_dir.mkdir(parents=True)
            destination_csv = playlists_dir / "Folk Rock.csv"
            pd.DataFrame(columns=["Artist", "Title", "Album", "Year", "Validated"]).to_csv(
                destination_csv, index=False
            )
            source = source_dir / "Artist - Song.mp3"
            source.write_bytes(b"fake mp3")
            release = new_releases.ArtistRelease(
                artist="Artist",
                title="Song",
                album="Album",
                year=2026,
                release_date=datetime(2026, 1, 1, tzinfo=UTC),
                track_id="1",
                album_type="album",
            )

            with patch.object(
                new_releases, "_promote_release_album", return_value=False
            ), patch.object(new_releases, "tag_mp3") as tag_mp3:
                blocked = new_releases.move_outdated_releases(
                    [release],
                    {"Artist": {destination_csv: {"Older Song"}}},
                    station_dir / "songs",
                    "New Releases",
                    dry_run=False,
                )

            destination = destination_dir / "Artist - Song.mp3"
            self.assertEqual(blocked, [])
            self.assertTrue(destination.exists())
            tag_mp3.assert_called_once_with(
                str(destination),
                "Artist",
                "Song",
                "2026",
                "Folk Rock",
                "Album",
                log_prefix="      ",
                refresh_art=False,
                apply_replaygain=False,
            )

    def test_move_outdated_release_retains_source_on_case_insensitive_collision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir)
            playlists_dir = station_dir / "playlists"
            source_dir = station_dir / "songs" / "New Releases"
            destination_dir = station_dir / "songs" / "Celtic Metal"
            playlists_dir.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            destination_dir.mkdir(parents=True)
            destination_csv = playlists_dir / "Celtic Metal.csv"
            pd.DataFrame(columns=["Artist", "Title", "Album", "Year", "Validated"]).to_csv(
                destination_csv, index=False
            )
            source = source_dir / (
                'Lyriel - The Skye Boat Song (from "Outlander") (Case Conflict 7).mp3'
            )
            source.write_bytes(b"new recording")
            existing = destination_dir / 'Lyriel - The Skye Boat Song (From "Outlander").mp3'
            existing.write_bytes(b"existing recording")
            release = new_releases.ArtistRelease(
                artist="Lyriel",
                title='The Skye Boat Song (From "Outlander")',
                album='The Skye Boat Song (From "Outlander")',
                year=2026,
                release_date=datetime(2026, 1, 1, tzinfo=UTC),
                track_id="1",
                album_type="single",
                is_single=True,
            )

            with patch.object(
                new_releases, "_promote_release_album", return_value=False
            ), patch.object(
                new_releases,
                "EasyID3",
                return_value={
                    "artist": ["Lyriel"],
                    "title": ['The Skye Boat Song (From "Outlander")'],
                },
            ), patch.object(new_releases, "tag_mp3") as tag_mp3:
                blocked = new_releases.move_outdated_releases(
                    [release],
                    {"Lyriel": {destination_csv: {"Older Song"}}},
                    station_dir / "songs",
                    "New Releases",
                    dry_run=False,
                )

            self.assertEqual(blocked, [release])
            self.assertTrue(source.exists())
            self.assertEqual(existing.read_bytes(), b"existing recording")
            tag_mp3.assert_not_called()
            self.assertTrue(pd.read_csv(destination_csv).empty)

    def test_promote_release_album_rechecks_track_titled_album(self) -> None:
        release = new_releases.ArtistRelease(
            artist="Ghost",
            title="Peacefield",
            album="Peacefield",
            year=2025,
            release_date=datetime(2025, 1, 1, tzinfo=UTC),
            track_id="123",
            album_type="album",
            is_single=False,
        )
        resolution = SimpleNamespace(
            notes=(),
            album_promoted=True,
            song=new_releases.Song(
                artist="Ghost",
                title="Peacefield",
                album="Skeleta",
                year="2025",
            ),
            album_type="album",
            release_date=None,
            track_id=None,
        )

        with patch.object(new_releases, "TrackMetadataResolver") as resolver_type:
            resolver_type.return_value.resolve.return_value = resolution

            promoted = new_releases._promote_release_album(release)

        self.assertTrue(promoted)
        resolver_type.return_value.resolve.assert_called_once()
        self.assertEqual(release.album, "Skeleta")
        self.assertFalse(release.is_single)

    def test_runtime_moves_outdated_releases_before_saving_new_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir) / "Station"
            playlists_dir = station_dir / "playlists"
            songs_dir = station_dir / "songs"
            playlists_dir.mkdir(parents=True)
            songs_dir.mkdir()

            old_release = new_releases.ArtistRelease(
                artist="Ghost",
                title="Peacefield",
                album="Skeleta",
                year=2025,
                release_date=datetime(2025, 1, 1, tzinfo=UTC),
                track_id="123",
            )
            artist_playlist_map = {
                "Ghost": {playlists_dir / "Gothic Metal.csv": {"Peacefield"}}
            }
            cache = new_releases.ArtistIDCache(entries={}, dirty=False)

            with (
                patch.object(
                    new_releases_runtime,
                    "default_station_paths",
                    return_value=(station_dir, playlists_dir),
                ),
                patch.object(
                    new_releases,
                    "load_station_artists",
                    return_value=(["Ghost"], {"Ghost": {"Peacefield"}}, artist_playlist_map),
                ),
                patch.object(
                    new_releases,
                    "load_artist_id_cache",
                    return_value=cache,
                ),
                patch.object(
                    new_releases,
                    "load_existing_new_releases",
                    return_value=[old_release],
                ),
                patch.object(new_releases, "build_new_releases", return_value=[]),
                patch.object(new_releases, "save_artist_id_cache"),
                patch.object(new_releases, "move_outdated_releases") as move_outdated,
                patch.object(new_releases, "save_new_releases"),
            ):
                new_releases_runtime.NewReleasesRuntime().run(
                    new_releases_runtime.NewReleasesRequest(
                        station="neuralforge",
                        dry_run=True,
                    )
                )

            move_outdated.assert_called_once_with(
                [old_release],
                artist_playlist_map,
                songs_dir,
                "New Releases",
                dry_run=True,
            )

    def test_save_new_releases_writes_isolated_files_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir)
            playlists_dir = station_dir / "playlists"
            playlists_dir.mkdir(parents=True)
            release = new_releases.ArtistRelease(
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

            new_releases.save_new_releases(playlists_dir, [release], dry_run=False)

            csv_path = playlists_dir / "New Releases.csv"
            metadata_path = station_dir / "metadata" / "New Releases.metadata.json"
            legacy_csv_path = playlists_dir / "New Releases Deezer.csv"
            self.assertTrue(csv_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertFalse(legacy_csv_path.exists())

            df = pd.read_csv(csv_path, dtype=str).fillna("")
            self.assertEqual(
                list(df.columns),
                ["Artist", "Title", "Album", "Year", "Validated"],
            )
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
            cache = new_releases.ArtistIDCache(entries={}, dirty=False)
            cache.set("Ghost", "8506054")

            new_releases.save_artist_id_cache(playlists_dir, cache, dry_run=True)

            cache_path = Path(tmpdir) / "metadata" / "ArtistIDs.json"
            self.assertFalse(cache_path.exists())


if __name__ == "__main__":
    unittest.main()
