"""Unit tests for the Sonic Atlas & Station Vault read-only indexer."""

from __future__ import annotations

import json
import math
import os
import pathlib
import re

import pytest

from neuralcast.pipelines.sonic_atlas import (
    clear_atlas_cache,
    compute_constellation_layout,
    get_playlist_detail,
    get_station_atlas,
    parse_playlist_descriptions_text,
    parse_year_int,
    slugify,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


def _write_csv(path: pathlib.Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def _write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_atlas_cache()
    yield
    clear_atlas_cache()


def test_slugify_and_parse_year_int_edge_cases() -> None:
    assert slugify("Funk & Soul") == "funk-and-soul"
    assert slugify("João Gilberto") == "joao-gilberto"
    assert SLUG_RE.match(slugify("!!!"))
    assert slugify("!!!") == slugify("!!!")

    assert parse_year_int("1994") == 1994
    assert parse_year_int("1994.0") == 1994
    assert parse_year_int("1994-00-00") == 1994
    assert parse_year_int("1680") == 1680
    assert parse_year_int("Unknown") is None
    assert parse_year_int("Wiener Sängerknaben") is None
    assert parse_year_int("") is None
    assert parse_year_int(None) is None


def test_parse_playlist_descriptions_handles_formats() -> None:
    markdown = """# Station Definitions

Intro text.

## Placement and overlap

- General rule one.

## Playlist catalog

### Classic Rock

Foundational guitar-led rock centered on the 1970s. Prefer Classic Pop for synth-pop ballads.

### Aspen Vibes

Soft rock and melodic listening.

Overlap with other playlists is intentional here.

### Neo-Prog

Melodic progressive rock rooted in the 1980s.

Placement notes: Keep modern general prog in Progressive Rock.

### Deep House

Club-oriented house grooves.

#### Placement

Prefer Melodic EDM for sweeping builds.
"""
    parsed = parse_playlist_descriptions_text(markdown)
    assert set(parsed) == {"classic rock", "aspen vibes", "neo-prog", "deep house"}

    assert parsed["classic rock"].description == "Foundational guitar-led rock centered on the 1970s."
    assert parsed["classic rock"].placement_notes == "Prefer Classic Pop for synth-pop ballads."

    assert parsed["aspen vibes"].description == "Soft rock and melodic listening."
    assert parsed["aspen vibes"].placement_notes == "Overlap with other playlists is intentional here."

    assert parsed["neo-prog"].description == "Melodic progressive rock rooted in the 1980s."
    assert parsed["neo-prog"].placement_notes == "Keep modern general prog in Progressive Rock."

    assert parsed["deep house"].description == "Club-oriented house grooves."
    assert parsed["deep house"].placement_notes == "Prefer Melodic EDM for sweeping builds."


def test_constellation_layout_deterministic_and_non_overlapping() -> None:
    nodes = [f"playlist-{idx:02d}" for idx in range(18)]
    edges = [
        ("playlist-00", "playlist-01", 12.0),
        ("playlist-00", "playlist-02", 8.0),
        ("playlist-01", "playlist-02", 6.0),
        ("playlist-05", "playlist-06", 5.0),
    ]
    first = compute_constellation_layout("neuralcast", nodes, edges)
    second = compute_constellation_layout("neuralcast", list(reversed(nodes)), edges)
    assert first == second
    assert len(first) == len(nodes)

    for x, y in first.values():
        assert 0.08 <= x <= 0.92
        assert 0.08 <= y <= 0.92

    # No two nodes should stack on top of each other.
    coords = list(first.values())
    for idx_a in range(len(coords)):
        for idx_b in range(idx_a + 1, len(coords)):
            dist = math.hypot(
                coords[idx_a][0] - coords[idx_b][0],
                coords[idx_a][1] - coords[idx_b][1],
            )
            assert dist >= 0.05

    # Edge cases: 0, 1, 2 nodes.
    assert compute_constellation_layout("neuralcast", [], []) == {}
    assert compute_constellation_layout("neuralcast", ["solo"], []) == {
        "solo": (0.5, 0.5)
    }
    pair = compute_constellation_layout("neuralcast", ["b", "a"], [("a", "b", 1.0)])
    assert pair == {"a": (0.3, 0.5), "b": (0.7, 0.5)}


def test_indexer_del_youtube_bridges_decades_vault_episodes_and_companion(
    tmp_path: pathlib.Path,
) -> None:
    station_dir = tmp_path / "NeuralCast"
    catalogue_dir = tmp_path / "catalogue"

    # CSVs exercising [DEL], YouTube URL overrides, unknown years, and cross-playlist overlap.
    _write_csv(
        station_dir / "playlists" / "Classic Rock.csv",
        "Artist,Title,Year,Album,Validated",
        [
            "Queen,Bohemian Rhapsody,1975,A Night at the Opera,True",
            "[https://youtu.be/dQw4w9WgXcQ] Pink Floyd,Time,1973,The Dark Side of the Moon,True",
            "[https://www.youtube.com/watch?v=abc123] Led Zeppelin,Stairway to Heaven,1971,Led Zeppelin IV,True",
            "[DEL] Removed Artist,Removed Song,1980,Old Album,False",
            "Another Artist,[DEL] Deleted By Title,1982,Old Album,False",
            "Queen,Radio Ga Ga,1984,The Works,True",
            "Mystery Band,Untagged Cut,Unknown,,False",
        ],
    )
    _write_csv(
        station_dir / "playlists" / "Aspen Vibes.csv",
        "Artist,Title,Album,Year,Validated",
        [
            "Queen,Bohemian Rhapsody,A Night at the Opera,1975,True",
            "Queen,Radio Ga Ga,The Works,1984,True",
            "Toto,Africa,Toto IV,1982,True",
            "Phil Collins,In the Air Tonight,Face Value,1981,True",
        ],
    )
    _write_csv(
        station_dir / "playlists" / "Symphonic Classics.csv",
        "Artist,Title,Year,Album,Validated",
        [
            "Johann Sebastian Bach,Brandenburg Concerto No. 3,1721,Brandenburg Concertos,True",
        ],
    )

    (station_dir / "playlist_descriptions.md").write_text(
        """# NeuralCast Playlist Definitions

## Playlist catalog

### Classic Rock

Foundational guitar-led rock classics. Prefer Aspen Vibes for soft ballads.

### Aspen Vibes

Warm soft-ish listening across eras. Overlap with other playlists is intentional here.

### Symphonic Classics

Canonical orchestral and classical works.
""",
        encoding="utf-8",
    )

    # Published episode featuring Queen - Bohemian Rhapsody and Toto - Africa.
    _write_json(
        catalogue_dir / "neuralcast" / "ep-anthem" / "manifest.json",
        {
            "version": 1,
            "id": "ep-anthem",
            "station": "neuralcast",
            "channel": "neuralcast-es",
            "language": "es-AR",
            "title": "Anthems of the 70s & 80s",
            "episode_number": 3,
            "description": "Exploring timeless rock and pop craft.",
            "published_at": "2026-10-01T12:00:00+00:00",
            "duration": 1800.5,
            "chapters": [
                {"kind": "commentary", "id": "intro", "title": "Intro"},
                {
                    "kind": "music",
                    "id": "queen-bohemian",
                    "artist": "Queen",
                    "title": "Bohemian Rhapsody",
                    "album": "A Night at the Opera",
                    "year": "1975",
                },
                {
                    "kind": "music",
                    "id": "toto-africa",
                    "artist": "Toto",
                    "title": "Africa",
                    "album": "Toto IV",
                    "year": "1982",
                },
            ],
        },
    )

    # Host companion stories: 6 valid stories for Queen (should keep 5 newest),
    # plus a news item (ignored) and a corrupt channel companion file (tolerated).
    records = [
        {
            "id": f"rec-{idx}",
            "channel": "neuralcast-es",
            "archetype": "short_story",
            "expected_start": 1700000000.0 + idx * 100.0,
            "expected_end": 1700000060.0 + idx * 100.0,
            "expires_at": 1700001260.0 + idx * 100.0,
            "content": {
                "title": f"Queen Story #{idx}",
                "summary": f"Summary for story {idx}.",
                "key_points": [],
                "items": [],
                "subject": {
                    "artist": "Queen",
                    "title": "Bohemian Rhapsody",
                    "album": "A Night at the Opera",
                    "year": "1975",
                },
            },
            "transcript": "Spoken script.",
        }
        for idx in range(6)
    ]
    records.append(
        {
            "id": "news-1",
            "channel": "neuralcast-es",
            "archetype": "news",
            "expected_start": 1700009999.0,
            "expected_end": 1700010050.0,
            "expires_at": 1700011250.0,
            "content": {"title": "Daily News", "summary": "", "items": []},
            "transcript": "News.",
        }
    )
    _write_json(
        station_dir / "metadata" / "host_companion.json",
        {"version": 1, "records": records},
    )
    corrupt_companion = (
        station_dir / "metadata" / "host_channels" / "neuralcast-en" / "host_companion.json"
    )
    corrupt_companion.parent.mkdir(parents=True, exist_ok=True)
    corrupt_companion.write_text("{corrupt json!!!", encoding="utf-8")

    atlas = get_station_atlas(
        "neuralcast", station_dir=station_dir, catalogue_dir=catalogue_dir
    )

    assert atlas.station == "neuralcast"
    assert atlas.station_name == "NeuralCast"
    assert atlas.accent == "#1f8a78"

    # Stats: Classic Rock has 5 valid tracks (2 [DEL] excluded), Aspen Vibes 4, Symphonic 1 -> 10 entries, 8 unique.
    assert atlas.stats.playlist_count == 3
    assert atlas.stats.total_entries == 10
    assert atlas.stats.unique_tracks == 8
    assert atlas.stats.unique_artists == 7
    assert atlas.stats.bridge_count == 1
    assert atlas.stats.min_year == 1721
    assert atlas.stats.max_year == 1984
    assert atlas.stats.episode_count == 1

    # Rotation roles & editorial descriptions.
    playlists_by_slug = {p.slug: p for p in atlas.playlists}
    assert playlists_by_slug["classic-rock"].rotation_role == "themed"
    assert playlists_by_slug["aspen-vibes"].rotation_role == "reserved_window"
    assert playlists_by_slug["symphonic-classics"].rotation_role == "open_only"

    assert playlists_by_slug["classic-rock"].description == "Foundational guitar-led rock classics."
    assert (
        playlists_by_slug["classic-rock"].placement_notes
        == "Prefer Aspen Vibes for soft ballads."
    )

    # YouTube URLs stripped cleanly; [DEL] excluded.
    artist_names = {a.name for a in atlas.artists}
    assert "Pink Floyd" in artist_names
    assert "Led Zeppelin" in artist_names
    assert "Removed Artist" not in artist_names
    assert "Another Artist" not in artist_names
    assert not any("youtu" in name.lower() for name in artist_names)

    # Bridge between aspen-vibes (4 tracks) and classic-rock (5 tracks): 2 shared, union = 7 -> 2/7 = 0.2857.
    assert len(atlas.bridges) == 1
    bridge = atlas.bridges[0]
    assert bridge.source == "aspen-vibes"
    assert bridge.target == "classic-rock"
    assert bridge.shared_count == 2
    assert bridge.jaccard == round(2 / 7, 4)
    assert [t.title for t in bridge.sample_tracks] == ["Bohemian Rhapsody", "Radio Ga Ga"]

    # Decades aggregation: 1720s (1), 1970s (4 entries / 3 unique), 1980s (4 entries / 3 unique).
    decades_by_val = {d.decade: d for d in atlas.decades}
    assert list(decades_by_val) == [1720, 1970, 1980]
    assert decades_by_val[1970].label == "1970s"
    assert decades_by_val[1970].track_count == 4
    assert decades_by_val[1970].unique_track_count == 3
    assert decades_by_val[1980].track_count == 4
    assert decades_by_val[1980].unique_track_count == 3

    # Episodes cross-linking.
    assert len(atlas.episodes) == 1
    ep = atlas.episodes[0]
    assert ep.id == "ep-anthem"
    assert ep.episode_number == 3
    assert ep.playlist_slugs == ["aspen-vibes", "classic-rock"]
    assert ep.matched_artists == ["Queen", "Toto"]

    # Artist vault & host_companion stories (5 newest, descending).
    artists_by_slug = {a.slug: a for a in atlas.artists}
    queen = artists_by_slug["queen"]
    assert queen.track_count == 2
    assert queen.min_year == 1975
    assert queen.max_year == 1984
    assert queen.playlists == ["aspen-vibes", "classic-rock"]
    assert queen.episode_ids == ["ep-anthem"]
    assert len(queen.host_stories) == 5
    assert queen.host_stories[0].title == "Queen Story #5"
    assert queen.host_stories[-1].title == "Queen Story #1"
    assert queen.host_stories[0].track_title == "Bohemian Rhapsody"
    assert queen.host_stories[0].created_at is not None

    # Playlist detail verification.
    detail = get_playlist_detail(
        "neuralcast",
        "classic-rock",
        station_dir=station_dir,
        catalogue_dir=catalogue_dir,
    )
    assert detail.playlist.slug == "classic-rock"
    assert len(detail.tracks) == 5
    bohemian = next(t for t in detail.tracks if t.title == "Bohemian Rhapsody")
    assert bohemian.artist_slug == "queen"
    assert bohemian.decade == 1970
    assert bohemian.other_playlists == ["aspen-vibes"]
    assert bohemian.episode_ids == ["ep-anthem"]
    assert len(detail.bridges) == 1
    assert detail.bridges[0].playlist_slug == "aspen-vibes"
    assert len(detail.episodes) == 1
    assert [(t.artist, t.title) for t in detail.episodes[0].matched_tracks] == [
        ("Queen", "Bohemian Rhapsody")
    ]


def test_indexer_tolerates_missing_optional_files(tmp_path: pathlib.Path) -> None:
    station_dir = tmp_path / "NeuralForge"
    _write_csv(
        station_dir / "playlists" / "Power Metal.csv",
        "Artist,Title,Year,Album,Validated",
        ["Helloween,Eagle Fly Free,1988,Keeper of the Seven Keys Part II,True"],
    )
    atlas = get_station_atlas(
        "neuralforge",
        station_dir=station_dir,
        catalogue_dir=tmp_path / "missing-catalogue",
    )
    assert atlas.station == "neuralforge"
    assert atlas.accent == "#b55748"
    assert atlas.stats.playlist_count == 1
    assert atlas.stats.unique_tracks == 1
    assert atlas.episodes == []
    assert atlas.artists[0].host_stories == []


def test_mtime_cache_reuses_and_invalidates_on_source_change(
    tmp_path: pathlib.Path,
) -> None:
    station_dir = tmp_path / "NeuralForge"
    csv_path = station_dir / "playlists" / "Classic Metal.csv"
    _write_csv(
        csv_path,
        "Artist,Title,Year,Album,Validated",
        ["Iron Maiden,The Trooper,1983,Piece of Mind,True"],
    )

    first = get_station_atlas(
        "neuralforge", station_dir=station_dir, catalogue_dir=tmp_path / "cat"
    )
    second = get_station_atlas(
        "neuralforge", station_dir=station_dir, catalogue_dir=tmp_path / "cat"
    )
    # Exact cached object returned when files haven't changed.
    assert first is second

    # Mutate CSV with bumped mtime & new row -> cache automatically invalidates.
    _write_csv(
        csv_path,
        "Artist,Title,Year,Album,Validated",
        [
            "Iron Maiden,The Trooper,1983,Piece of Mind,True",
            "Black Sabbath,War Pigs,1970,Paranoid,True",
        ],
    )
    stat = csv_path.stat()
    os.utime(csv_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000))

    third = get_station_atlas(
        "neuralforge", station_dir=station_dir, catalogue_dir=tmp_path / "cat"
    )
    assert third is not first
    assert third.stats.unique_tracks == 2

    # Adding a new source file (playlist_descriptions.md) also invalidates cache.
    (station_dir / "playlist_descriptions.md").write_text(
        "## Playlist catalog\n\n### Classic Metal\n\nTraditional heavy metal.\n",
        encoding="utf-8",
    )
    fourth = get_station_atlas(
        "neuralforge", station_dir=station_dir, catalogue_dir=tmp_path / "cat"
    )
    assert fourth is not third
    assert fourth.playlists[0].description == "Traditional heavy metal."
