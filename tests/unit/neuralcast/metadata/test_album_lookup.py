"""Unit tests for album lookup scoring and provider selection."""

from __future__ import annotations

from datetime import datetime

from neuralcast.metadata import album_lookup


def _match(
    album: str,
    *,
    source: str = "deezer",
    confidence: float = 0.9,
    album_type: str = "album",
    release_date: datetime | None = None,
    flags: list[str] | None = None,
    artist_score: float = 1.0,
) -> album_lookup.AlbumMatch:
    return album_lookup.AlbumMatch(
        album=album,
        source=source,
        confidence=confidence,
        album_type=album_type,
        raw_album=album,
        release_date=release_date,
        track_id="1",
        track_name="Track",
        title_score=1.0,
        artist_score=artist_score,
        flags=flags or [],
    )


def test_title_and_album_cleaning_remove_common_noise() -> None:
    assert album_lookup._normalize_title("Rats (feat. Someone) - Remastered") == "rats"
    assert album_lookup._clean_album_name("Prequelle (Deluxe Edition) - 2018 Remaster") == "Prequelle"


def test_provider_order_prefers_deezer_when_requested() -> None:
    assert album_lookup._candidate_provider_order(prefer_spotify=True, prefer_deezer=True) == [
        "deezer",
        "musicbrainz",
        "itunes",
        "spotify",
    ]
    assert album_lookup._candidate_provider_order(prefer_spotify=True, prefer_deezer=False)[0] == "spotify"


def test_prefer_official_chooses_official_album_before_single() -> None:
    matches = [
        _match("Single", album_type="single", flags=["status:official"]),
        _match("Album", album_type="album", flags=["status:official"]),
    ]

    assert album_lookup._prefer_official(matches) == [matches[1]]


def test_prefer_earliest_studio_album_ignores_later_reissues() -> None:
    original = _match("Album", release_date=datetime(1980, 1, 1))
    later = _match("Album 2015 Remaster", release_date=datetime(2015, 1, 1))

    assert album_lookup._prefer_earliest_studio_album([later, original]) == [original]


def test_guess_album_returns_confident_non_live_match(monkeypatch) -> None:
    live = _match("Live Album", flags=["live_album"], confidence=0.95)
    studio = _match("Studio Album", release_date=datetime(1990, 1, 1), confidence=0.8)
    monkeypatch.setattr(album_lookup, "album_candidates", lambda *_args, **_kwargs: [live, studio])

    assert album_lookup.guess_album("Artist", "Title") == studio


def test_spotify_candidates_scores_and_filters_payload(monkeypatch) -> None:
    class FakeSpotify:
        def search(self, **_kwargs):
            return {
                "tracks": {
                    "items": [
                        {
                            "id": "bad",
                            "name": "Wrong Song",
                            "artists": [{"name": "Ghost"}],
                            "album": {"name": "Wrong", "album_type": "album"},
                        },
                        {
                            "id": "good",
                            "name": "Rats",
                            "popularity": 75,
                            "artists": [{"name": "Ghost"}],
                            "album": {
                                "name": "Prequelle (Deluxe Edition)",
                                "album_type": "album",
                                "release_date": "2018-06-01",
                                "release_date_precision": "day",
                                "artists": [{"name": "Ghost"}],
                            },
                        },
                    ]
                }
            }

    monkeypatch.setattr(album_lookup, "_get_spotify_client", lambda: FakeSpotify())

    matches = album_lookup._spotify_candidates("Ghost", "Rats")

    assert len(matches) == 1
    assert matches[0].source == "spotify"
    assert matches[0].album == "Prequelle"
    assert matches[0].track_id == "good"
    assert matches[0].release_date == datetime(2018, 6, 1)
    assert "reissue" in matches[0].flags


def test_deezer_candidates_handles_bad_rank_album_cache_and_artist_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        album_lookup,
        "search_tracks",
        lambda *_args, **_kwargs: [
            {
                "id": "skip",
                "title": "Wrong Song",
                "artist": {"name": "Ghost"},
                "album": {"id": "1", "title": "Wrong"},
            },
            {
                "id": "hit",
                "title": "Rats",
                "rank": "not-an-int",
                "artist": {"name": "Ghost Tribute Band"},
                "album": {"id": "2", "title": "Prequelle Live"},
            },
        ],
    )
    monkeypatch.setattr(
        album_lookup,
        "get_album",
        lambda album_id: {
            "id": album_id,
            "title": "Prequelle Live",
            "record_type": "single",
            "release_date": "2018-06",
            "artist": {"name": "Various Artists"},
        },
    )

    matches = album_lookup._deezer_candidates("Ghost", "Rats")

    assert len(matches) == 1
    assert matches[0].album == "Prequelle Live"
    assert matches[0].album_type == "single"
    assert matches[0].popularity is None
    assert "type:single" in matches[0].flags
    assert "live_album" in matches[0].flags
    assert "album_artist_mismatch" in matches[0].flags


def test_musicbrainz_candidates_parse_release_data_and_live_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        album_lookup.musicbrainzngs,
        "search_recordings",
        lambda **_kwargs: {
            "recording-list": [
                {"title": "Wrong", "release-list": [{"title": "Wrong"}]},
                {
                    "title": "Rats - Live",
                    "ext-score": "90",
                    "artist-credit": [{"artist": {"name": "Ghost"}}],
                    "release-list": [
                        {
                            "title": "Prequelle Live",
                            "date": "2018",
                            "release-group": {"primary-type": "Album"},
                        }
                    ],
                },
            ]
        },
    )

    matches = album_lookup._musicbrainz_candidates("Ghost", "Rats")

    assert len(matches) == 1
    assert matches[0].source == "musicbrainz"
    assert matches[0].release_date == datetime(2018, 1, 1)
    assert "live_track" in matches[0].flags
    assert "live_album" in matches[0].flags


def test_itunes_candidates_normalize_song_collection_and_dates(monkeypatch) -> None:
    monkeypatch.setattr(
        album_lookup,
        "_itunes_search",
        lambda *_args, **_kwargs: [
            {
                "trackId": 123,
                "trackName": "Rats",
                "artistName": "Ghost",
                "collectionName": "Prequelle - 2018 Remaster",
                "collectionType": "song",
                "releaseDate": "2018-06-01T00:00:00Z",
            }
        ],
    )

    matches = album_lookup._itunes_candidates("Ghost", "Rats")

    assert len(matches) == 1
    assert matches[0].source == "itunes"
    assert matches[0].album == "Prequelle"
    assert matches[0].album_type == "single"
    assert matches[0].release_date == datetime(2018, 6, 1)
    assert "type:single" in matches[0].flags
    assert "reissue" in matches[0].flags


def test_album_candidates_uses_provider_order_until_first_hit(monkeypatch) -> None:
    album_lookup.album_candidates.cache_clear()
    calls: list[str] = []

    def fake_provider(provider: str, *_args, **_kwargs):
        calls.append(provider)
        return [_match("Prequelle", source=provider)] if provider == "itunes" else []

    monkeypatch.setattr(album_lookup, "_provider_candidates", fake_provider)

    matches = album_lookup.album_candidates(
        "Ghost",
        "Rats",
        prefer_spotify=False,
        prefer_deezer=True,
    )

    assert calls == ["deezer", "musicbrainz", "itunes"]
    assert matches[0].source == "itunes"


def test_get_official_album_name_returns_match_album(monkeypatch) -> None:
    monkeypatch.setattr(album_lookup, "guess_album", lambda *_args, **_kwargs: _match("Prequelle"))

    assert album_lookup.get_official_album_name("Ghost", "Rats") == "Prequelle"
