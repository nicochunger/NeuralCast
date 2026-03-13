"""Tests for Deezer-backed sync validation and album lookup."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from neuralcast.metadata import album_lookup
from neuralcast.models import Song
from neuralcast.pipelines import playlist_sync
from neuralcast.services import validation


class DeezerSyncProviderTest(unittest.TestCase):
    def tearDown(self) -> None:
        validation.deezer_ok.cache_clear()
        validation.mb_ok.cache_clear()
        validation.itunes_ok.cache_clear()
        validation.verified.cache_clear()
        validation.deezer_album_ok.cache_clear()
        validation.mb_album_ok.cache_clear()
        validation.itunes_album_ok.cache_clear()
        validation.verified_album.cache_clear()
        album_lookup.album_candidates.cache_clear()

    def test_verified_uses_deezer_track_matches(self) -> None:
        track_hit = {
            "title": "Rats",
            "artist": {"name": "Ghost"},
            "album": {"title": "Prequelle"},
        }
        with patch.object(validation, "search_tracks", return_value=[track_hit]):
            self.assertTrue(validation.deezer_ok("Ghost", "Rats"))
            self.assertTrue(validation.verified("Ghost", "Rats"))

    def test_verified_album_verbose_reports_deezer_provider(self) -> None:
        track_hit = {
            "title": "Rats",
            "artist": {"name": "Ghost"},
            "album": {"title": "Prequelle"},
        }
        with (
            patch.object(validation, "search_tracks", return_value=[track_hit]),
            patch.object(validation, "mb_album_ok", return_value=False),
            patch.object(validation, "itunes_album_ok", return_value=False),
        ):
            details = validation.verified_album(
                "Ghost", "Rats", "Prequelle", verbose=True
            )

        self.assertEqual(
            details,
            {
                "deezer": True,
                "musicbrainz": False,
                "itunes": False,
                "any": True,
            },
        )

    def test_perform_song_validation_clears_unverified_album_but_keeps_song(self) -> None:
        song = Song(
            artist="Ghost",
            title="Rats",
            album="Wrong Album",
            year="2018",
            validated=False,
        )

        with (
            patch.object(validation, "verified", return_value=True),
            patch.object(validation, "verified_album", return_value=False),
        ):
            result = validation.perform_song_validation(song)

        assert result.song is not None
        self.assertTrue(result.song.validated)
        self.assertIsNone(result.song.album)
        self.assertEqual(result.album, "Wrong Album")
        self.assertTrue(result.album_cleared)

    def test_deezer_candidates_build_album_match(self) -> None:
        track_hit = {
            "id": 505508952,
            "title": "Rats",
            "rank": 578336,
            "artist": {"name": "Ghost"},
            "album": {"id": 64572462, "title": "Prequelle"},
        }
        album_hit = {
            "id": 64572462,
            "title": "Prequelle",
            "record_type": "album",
            "release_date": "2018-06-01",
            "artist": {"name": "Ghost"},
            "contributors": [{"name": "Ghost", "role": "Main"}],
        }
        with (
            patch.object(album_lookup, "search_tracks", return_value=[track_hit]),
            patch.object(album_lookup, "get_album", return_value=album_hit),
        ):
            matches = album_lookup._deezer_candidates("Ghost", "Rats", limit=10)

        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match.source, "deezer")
        self.assertEqual(match.album, "Prequelle")
        self.assertEqual(match.album_type, "album")
        self.assertEqual(match.track_id, "505508952")
        self.assertEqual(match.popularity, 578336)
        self.assertEqual(match.release_date, datetime(2018, 6, 1))
        self.assertGreaterEqual(match.confidence, 0.55)

    def test_guess_album_falls_back_to_itunes_after_deezer_and_musicbrainz(self) -> None:
        fallback = album_lookup.AlbumMatch(
            album="Prequelle",
            source="itunes",
            confidence=0.82,
            album_type="album",
            raw_album="Prequelle",
            release_date=datetime(2018, 6, 1),
            track_id="123",
            track_name="Rats",
            title_score=1.0,
            artist_score=1.0,
        )
        with (
            patch.object(album_lookup, "_deezer_candidates", return_value=[]),
            patch.object(album_lookup, "_musicbrainz_candidates", return_value=[]),
            patch.object(album_lookup, "_itunes_candidates", return_value=[fallback]),
            patch.object(album_lookup, "_spotify_candidates", return_value=[]),
        ):
            match = album_lookup.guess_album(
                "Ghost",
                "Rats",
                prefer_spotify=False,
                prefer_deezer=True,
                min_confidence=0.55,
                allow_fallback=True,
            )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.source, "itunes")
        self.assertEqual(match.album, "Prequelle")

    def test_playlist_sync_requests_deezer_first_album_backfill(self) -> None:
        song = Song(
            artist="Ghost",
            title="Rats",
            album="",
            year="2018",
            validated=False,
        )
        deezer_match = album_lookup.AlbumMatch(
            album="Prequelle",
            source="deezer",
            confidence=0.9,
            album_type="album",
            raw_album="Prequelle",
            release_date=datetime(2018, 6, 1),
            track_id="505508952",
            track_name="Rats",
            title_score=1.0,
            artist_score=1.0,
        )
        with (
            patch.object(playlist_sync, "verified_album", return_value=False),
            patch.object(playlist_sync, "guess_album", return_value=deezer_match) as guess_album_mock,
        ):
            updated_song, changed = playlist_sync._backfill_album_for_missing_song(song)

        self.assertTrue(changed)
        self.assertEqual(updated_song.album, "Prequelle")
        self.assertEqual(updated_song.year, "2018")
        self.assertFalse(updated_song.validated)
        self.assertEqual(
            guess_album_mock.call_args.kwargs,
            {
                "prefer_spotify": False,
                "prefer_deezer": True,
                "min_confidence": 0.55,
                "allow_fallback": True,
            },
        )

    def test_playlist_sync_clears_unverified_album_when_no_replacement_is_found(self) -> None:
        song = Song(
            artist="Ghost",
            title="Rats",
            album="Wrong Album",
            year="2018",
            validated=True,
        )

        with (
            patch.object(playlist_sync, "verified_album", return_value=False),
            patch.object(playlist_sync, "guess_album", return_value=None),
        ):
            updated_song, changed = playlist_sync._backfill_album_for_missing_song(song)

        self.assertTrue(changed)
        self.assertIsNone(updated_song.album)
        self.assertTrue(updated_song.validated)


if __name__ == "__main__":
    unittest.main()
