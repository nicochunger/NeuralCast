"""Unit tests for playlist CSV and file helper behavior."""

from __future__ import annotations

import pandas as pd

from neuralcast.models import Song
from neuralcast.playlists import utils


def test_normalize_year_value_handles_common_csv_shapes() -> None:
    assert utils.normalize_year_value("1980.0") == "1980"
    assert utils.normalize_year_value("2026-00-00") == "2026"
    assert utils.normalize_year_value("unknown") == "Unknown"
    assert utils.normalize_year_value("") is None


def test_load_playlist_parses_delete_markers_and_youtube_overrides(tmp_path) -> None:
    playlist_path = tmp_path / "Playlist.csv"
    pd.DataFrame(
        [
            {
                "Artist": "[https://youtu.be/abc] Ghost",
                "Title": "Rats",
                "Album": "Prequelle",
                "Year": "2018.0",
                "Validated": "yes",
            },
            {
                "Artist": "[DEL] Old Artist",
                "Title": "Old Song",
                "Album": "",
                "Year": "1999",
                "Validated": "false",
            },
        ]
    ).to_csv(playlist_path, index=False)

    songs, needs_save, marked_for_deletion, _df = utils.load_playlist(playlist_path)

    assert needs_save is True
    assert len(songs) == 1
    assert songs[0].artist == "Ghost"
    assert songs[0].override_url == "https://youtu.be/abc"
    assert songs[0].year == "2018"
    assert songs[0].validated is True
    assert [(song.artist, song.title) for song in marked_for_deletion] == [
        ("Old Artist", "Old Song")
    ]


def test_deduplicate_and_sort_songs_removes_case_insensitive_duplicates() -> None:
    songs = [
        Song(artist="zeta", title="B", year="2000"),
        Song(artist="Alpha", title="A", year="2000"),
        Song(artist=" alpha ", title=" a ", year="2001"),
    ]

    sorted_songs, changed, duplicates_removed = utils.deduplicate_and_sort_songs(songs)

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

    monkeypatch.setattr(utils, "EasyID3", FakeEasyID3)

    plan = utils.plan_songs_from_library("Playlist", [], music_dir)

    target = music_dir / "AC DC - Thunderstruck.mp3"
    assert source.exists()
    assert not target.exists()
    assert len(plan.renames) == 1
    assert plan.added_from_files == 1

    existing_paths = utils.apply_library_renames(plan)

    assert not source.exists()
    assert target.exists()
    assert existing_paths[("ac/dc", "thunderstruck")] == target


def test_save_playlist_preserves_extra_columns_and_override_url(tmp_path) -> None:
    playlist_path = tmp_path / "Playlist.csv"
    df = pd.DataFrame(
        [
            {
                "Artist": "Ghost",
                "Title": "Rats",
                "Year": "2018",
                "Album": "",
                "Validated": False,
                "Mood": "dark",
            }
        ]
    )
    songs = [
        Song(
            artist="Ghost",
            title="Rats",
            year="2018.0",
            album="Prequelle",
            validated=True,
            override_url="https://youtube.com/watch?v=abc",
        )
    ]

    utils.save_playlist_with_validation(playlist_path, songs, df, log=lambda _msg: None)

    saved = pd.read_csv(playlist_path, dtype=str).fillna("")
    assert list(saved.columns) == ["Artist", "Title", "Year", "Album", "Validated", "Mood"]
    assert saved.iloc[0]["Artist"] == "[https://youtube.com/watch?v=abc] Ghost"
    assert saved.iloc[0]["Year"] == "2018"
    assert saved.iloc[0]["Album"] == "Prequelle"
    assert saved.iloc[0]["Mood"] == "dark"


def test_delete_marked_mp3_files_deletes_matching_files_across_playlists(tmp_path) -> None:
    songs_root = tmp_path / "songs"
    first = songs_root / "Playlist A"
    second = songs_root / "Playlist B"
    first.mkdir(parents=True)
    second.mkdir()
    for folder in (first, second):
        (folder / "AC DC - Thunderstruck.mp3").write_bytes(b"mp3")

    removed = utils.delete_marked_mp3_files(
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

    found = utils.find_marked_mp3_files(
        {
            ("ac dc", "thunderstruck"): Song(
                artist="AC/DC", title="Thunderstruck", year="1990"
            )
        },
        songs_root,
    )

    assert found == [target]
    assert target.exists()
