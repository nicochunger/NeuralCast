"""Offline unit tests for Deezer release-discovery filtering."""

from __future__ import annotations

from datetime import UTC, datetime

from neuralcast.pipelines.new_releases import discovery


def test_parse_release_date_and_album_exclusion_rules() -> None:
    assert discovery.parse_release_date("2026-09-02") == datetime(2026, 9, 2, tzinfo=UTC)
    assert discovery.parse_release_date("2026-09") == datetime(2026, 9, 1, tzinfo=UTC)
    assert discovery.parse_release_date("2026") == datetime(2026, 1, 1, tzinfo=UTC)
    assert discovery.parse_release_date("2026-99-99") is None
    assert discovery._is_alt_or_reissue("Song (Live)", "Album") is True
    assert discovery._is_alt_or_reissue("Song", "Album") is False


def test_fetch_recent_releases_filters_and_selects_best_eligible_track(monkeypatch) -> None:
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(discovery, "_resolve_artist", lambda *_args: {"id": "artist-1"})
    monkeypatch.setattr(discovery, "_known_artist_genre_ids", lambda *_args: frozenset())
    monkeypatch.setattr(discovery, "_iter_recent_albums", lambda *_args: [
        (datetime(2026, 4, 1, tzinfo=UTC), {"id": "album-1", "title": "Fresh", "record_type": "album"}),
        (datetime(2026, 3, 1, tzinfo=UTC), {"id": "album-2", "title": "Deluxe Edition", "record_type": "album"}),
    ])
    monkeypatch.setattr(discovery, "_album_matches_known_genres", lambda *_args: True)
    monkeypatch.setattr(discovery, "_is_probable_old_catalog_release", lambda *_args: False)
    monkeypatch.setattr(discovery, "_album_tracks_by_artist", lambda album_id, *_args: [
        {"id": "old", "title": "Known", "readable": True, "rank": 99},
        {"id": "unreadable", "title": "Unavailable", "readable": False, "rank": 90},
        {"id": "low", "title": "New Song", "readable": True, "rank": 1, "disk_number": 1, "track_position": 2},
        {"id": "best", "title": "Best Song", "readable": "yes", "rank": 10, "disk_number": 1, "track_position": 1},
    ] if album_id == "album-1" else [])

    releases = discovery.fetch_recent_releases("Ghost", cutoff, {"Known"})

    assert [(item.title, item.track_id, item.album_type) for item in releases] == [
        ("Best Song", "best", "album")
    ]


def test_deezer_pagination_and_genre_compatibility(monkeypatch) -> None:
    payloads = iter([
        {"data": [{"id": 1}], "next": "https://next.test/page"},
        {"data": [{"id": 2}]},
    ])
    monkeypatch.setattr(discovery, "_deezer_get", lambda *_args, **_kwargs: next(payloads))

    assert discovery._paginate_deezer("/artist/1/albums") == [{"id": 1}, {"id": 2}]
    assert discovery._genre_sets_are_compatible(frozenset({85}), frozenset({87}))
    assert not discovery._genre_sets_are_compatible(frozenset({85}), frozenset({106}))
