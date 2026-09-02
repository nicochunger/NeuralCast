"""Unit tests for playlist catalog and MP3 library behavior."""

from __future__ import annotations

from neuralcast.models import Song
from neuralcast.playlists import library
from neuralcast.playlists.catalog import deduplicate_and_sort_songs, normalize_year_value


def test_normalize_year_value_handles_common_csv_shapes() -> None:
    assert normalize_year_value("1980.0") == "1980"
    assert normalize_year_value("2026-00-00") == "2026"
    assert normalize_year_value("unknown") == "Unknown"
    assert normalize_year_value("") is None


def test_deduplicate_and_sort_songs_removes_case_insensitive_duplicates() -> None:
    songs = [
        Song(artist="zeta", title="B", year="2000"),
        Song(artist="Alpha", title="A", year="2000"),
        Song(artist=" alpha ", title=" a ", year="2001"),
    ]

    sorted_songs, changed, duplicates_removed = deduplicate_and_sort_songs(songs)

    assert changed is True
    assert duplicates_removed == 1
    assert [(song.artist, song.title) for song in sorted_songs] == [
        ("Alpha", "A"),
        ("zeta", "B"),
    ]


def test_library_backfill_plans_rename_before_applying_it(
    tmp_path, monkeypatch
) -> None:
    music_dir = tmp_path / "Playlist"
    music_dir.mkdir()
    source = music_dir / "unexpected.mp3"
    source.write_bytes(b"mp3")

    class FakeEasyID3(dict):
        def __init__(self, _path: str) -> None:
            super().__init__(artist=["AC/DC"], title=["Thunderstruck"], date=["1990"])

    monkeypatch.setattr(library, "EasyID3", FakeEasyID3)

    plan = library.plan_songs_from_library("Playlist", [], music_dir)

    target = music_dir / "AC DC - Thunderstruck.mp3"
    assert source.exists()
    assert not target.exists()
    assert len(plan.renames) == 1
    assert plan.added_from_files == 1

    existing_paths = library.apply_library_renames(plan)

    assert not source.exists()
    assert target.exists()
    assert existing_paths[("ac/dc", "thunderstruck")] == target


def test_delete_marked_mp3_files_deletes_matching_files_across_playlists(tmp_path) -> None:
    songs_root = tmp_path / "songs"
    first = songs_root / "Playlist A"
    second = songs_root / "Playlist B"
    first.mkdir(parents=True)
    second.mkdir()
    for folder in (first, second):
        (folder / "AC DC - Thunderstruck.mp3").write_bytes(b"mp3")

    removed = library.delete_marked_mp3_files(
        {("ac dc", "thunderstruck"): Song(artist="AC/DC", title="Thunderstruck", year="1990")},
        songs_root,
        log=lambda _msg: None,
    )

    assert removed == 2
    assert not (first / "AC DC - Thunderstruck.mp3").exists()
    assert not (second / "AC DC - Thunderstruck.mp3").exists()


def test_find_marked_mp3_files_is_read_only(tmp_path) -> None:
    songs_root = tmp_path / "songs"
    playlist = songs_root / "Playlist"
    playlist.mkdir(parents=True)
    target = playlist / "AC DC - Thunderstruck.mp3"
    target.write_bytes(b"mp3")

    found = library.find_marked_mp3_files(
        {
            ("ac dc", "thunderstruck"): Song(
                artist="AC/DC", title="Thunderstruck", year="1990"
            )
        },
        songs_root,
    )

    assert found == [target]
    assert target.exists()
