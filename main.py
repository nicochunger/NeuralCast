#!/usr/bin/env python3
"""
main.py — AI-assisted local-network radio pipeline
-------------------------------------------------
• reads playlists from playlists/ directory
• yt-dlp + ffmpeg  → MP3s
• mutagen          → ID3 tags
• moves files into songs/ directory organized by playlist
"""

import json
import os, subprocess, pathlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import openai
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, error
from pydantic import BaseModel
import paramiko
import requests
from dotenv import load_dotenv
import pandas as pd
from validators import verified, verified_album
import argparse
from album_art import embed_from_artist_album  # NEW


# ─── CONFIG ──────────────────────────────────────────────────────────────
load_dotenv()
OPENAI_KEY = os.getenv("OPENAI_API_KEY")  # required
# The following paths will be set dynamically based on the station argument
STATION_PATH = None
PLAYLISTS_PATH = None

AZURACAST_URL = "http://192.168.1.162/"
STATION = "neuralcast"

TTS = False  # turn off if you only want music
VOICE_NAME = "Adam"  # ElevenLabs voice

# ──────────────────────────────────────────────────────────────────────────


_OPENAI_CLIENT: Optional[openai.OpenAI] = None


def get_openai_client() -> openai.OpenAI:
    if OPENAI_KEY is None or not OPENAI_KEY.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Please set it in your environment."
        )

    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = openai.OpenAI(api_key=OPENAI_KEY)
    return _OPENAI_CLIENT


# Pydantic models for structured output
class Song(BaseModel):
    artist: str
    title: str
    year: str
    album: Optional[str] = None  # optional album support
    validated: bool = False  # Add validated field with default value


class Playlist(BaseModel):
    songs: List[Song]


@dataclass
class ValidationResult:
    status: str  # 'valid', 'song_invalid', 'album_failed'
    song: Optional[Song]
    album: Optional[str] = None
    album_validated: Optional[bool] = None
    album_reason: Optional[str] = None

# —— helpers ————————————————————————————————————————————————————————————


def _normalize_csv_value(value: object) -> Optional[str]:
    """Convert raw CSV cell values to clean strings or None."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan":
            return None
        return text
    if pd.isna(value):  # handles NaN/NA from pandas
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


DELETE_MARKER = "[DEL]"


def _strip_delete_prefix(value: Optional[str]) -> Tuple[Optional[str], bool]:
    """Remove the deletion marker prefix and report whether it was present."""

    if value is None:
        return None, False

    if value.startswith(DELETE_MARKER):
        cleaned = value[len(DELETE_MARKER) :].strip()
        return (cleaned if cleaned else None), True

    return value, False


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return False
        return normalized in {"true", "1", "yes", "y"}
    return False


def sanitize_filename_component(value: str) -> str:
    return value.replace("/", " ").replace("\\", " ").strip()


def playlist_song_key(song: Song) -> Tuple[str, str]:
    return (song.artist.lower().strip(), song.title.lower().strip())


def load_playlist(
    playlist_path: pathlib.Path,
) -> Tuple[List[Song], bool, List[Song]]:
    """Load songs from a playlist CSV and detect deletion markers."""
    df = pd.read_csv(
        playlist_path,
        dtype={
            "Year": "string",
            "Artist": "string",
            "Title": "string",
            "Album": "string",
        },
        keep_default_na=False,
        na_filter=False,
    )

    needs_save = False
    if not any(col.lower() == "validated" for col in df.columns):
        df["Validated"] = False
        needs_save = True
        print(f"Added 'Validated' column to {playlist_path}")

    column_lookup = {col.lower(): col for col in df.columns}

    songs: List[Song] = []
    marked_for_deletion: List[Song] = []
    for _, row in df.iterrows():
        artist_raw = (
            _normalize_csv_value(row[column_lookup["artist"]])
            if "artist" in column_lookup
            else None
        )
        title_raw = (
            _normalize_csv_value(row[column_lookup["title"]])
            if "title" in column_lookup
            else None
        )
        year = (
            _normalize_csv_value(row[column_lookup["year"]])
            if "year" in column_lookup
            else None
        )
        album_raw = (
            _normalize_csv_value(row[column_lookup["album"]])
            if "album" in column_lookup
            else None
        )
        artist, artist_marked = _strip_delete_prefix(artist_raw)
        title, title_marked = _strip_delete_prefix(title_raw)
        album, _ = _strip_delete_prefix(album_raw)
        validated_raw = (
            row[column_lookup["validated"]]
            if "validated" in column_lookup
            else False
        )
        validated = _as_bool(validated_raw) if validated_raw is not None else False

        if artist_marked or title_marked:
            if artist and title:
                marked_for_deletion.append(
                    Song(
                        artist=artist,
                        title=title,
                        year=year or "",
                        album=album or None,
                        validated=False,
                    )
                )
            else:
                print(
                    f"Warning: Could not parse [DEL] row in {playlist_path}; missing artist/title"
                )
            needs_save = True
            continue

        if artist and title and year:
            songs.append(
                Song(artist=artist, title=title, year=year, album=album, validated=validated)
            )
        else:
            print(
                f"Warning: Skipping incomplete row in {playlist_path}: "
                f"Artist={artist}, Title={title}, Year={year}"
            )

    return songs, needs_save, marked_for_deletion


def backfill_songs_from_library(
    playlist_name: str, songs: List[Song], music_dir: Optional[pathlib.Path]
) -> Tuple[List[Song], bool, int]:
    """Augment playlist songs with tracks already present in the library."""
    if not music_dir:
        print("Warning: STATION_PATH is not set; skipping MP3 file check")
        return songs, False, 0

    if not music_dir.exists():
        print(
            f"Warning: Music directory '{music_dir}' does not exist, skipping MP3 file check"
        )
        return songs, False, 0

    songs_by_key: Dict[Tuple[str, str], Song] = {
        playlist_song_key(song): song for song in songs
    }
    updated_songs = list(songs)
    added_from_files = 0
    changes = False

    for mp3_file in music_dir.glob("*.mp3"):
        try:
            audio = EasyID3(str(mp3_file))
            file_artist = audio.get("artist", [""])[0] if audio.get("artist") else ""
            file_title = audio.get("title", [""])[0] if audio.get("title") else ""
            file_year = audio.get("date", [""])[0] if audio.get("date") else ""
            file_album = audio.get("album", [""])[0] if audio.get("album") else ""
        except Exception as exc:
            print(f"Warning: Could not read metadata from {mp3_file}: {exc}")
            continue

        if not file_artist or not file_title:
            filename = mp3_file.stem
            if " - " in filename:
                parts = filename.split(" - ", 1)
                file_artist = file_artist or parts[0].strip()
                file_title = file_title or parts[1].strip()

        if not file_artist or not file_title:
            continue

        key = (file_artist.lower().strip(), file_title.lower().strip())
        if key in songs_by_key:
            continue

        safe_artist = sanitize_filename_component(file_artist)
        safe_title = sanitize_filename_component(file_title)
        expected_name = f"{safe_artist} - {safe_title}.mp3"
        target_path = mp3_file.with_name(expected_name)
        if mp3_file.name != expected_name:
            try:
                if target_path.exists():
                    print(
                        f"Warning: Target exists, cannot rename {mp3_file.name} -> {expected_name}"
                    )
                else:
                    mp3_file.rename(target_path)
                    mp3_file = target_path
                    print(f"Renamed file: {target_path.name}")
                    changes = True
            except Exception as exc:
                print(f"Warning: Could not rename {mp3_file.name} -> {expected_name}: {exc}")

        year_to_use = file_year if file_year else "Unknown"
        new_song = Song(
            artist=file_artist,
            title=file_title,
            year=year_to_use,
            album=file_album or None,
            validated=False,
        )
        updated_songs.append(new_song)
        songs_by_key[key] = new_song
        added_from_files += 1
        changes = True
        print(f"Added from existing file: {file_artist} - {file_title}")

    if added_from_files > 0:
        print(f"Added {added_from_files} song(s) from existing MP3 files")

    return updated_songs, changes, added_from_files


def deduplicate_and_sort_songs(songs: List[Song]) -> Tuple[List[Song], bool, int]:
    """Remove duplicate songs and return a sorted list."""
    seen: Dict[Tuple[str, str], Song] = {}
    ordered_unique: List[Song] = []
    for song in songs:
        key = playlist_song_key(song)
        if key not in seen:
            seen[key] = song
            ordered_unique.append(song)

    duplicates_removed = len(songs) - len(ordered_unique)
    sorted_songs = sorted(
        ordered_unique, key=lambda s: (s.artist.lower().strip(), s.title.lower().strip())
    )
    changed = duplicates_removed > 0 or sorted_songs != songs
    return sorted_songs, changed, duplicates_removed


def replace_song_entry(songs: List[Song], updated_song: Song) -> None:
    """Replace a song in-place based on artist/title key."""
    target_key = playlist_song_key(updated_song)
    for idx, existing in enumerate(songs):
        if playlist_song_key(existing) == target_key:
            songs[idx] = updated_song
            return


def perform_song_validation(
    song: Song, playlist_name: str, invalid_albums: List[dict]
) -> ValidationResult:
    """Run song + optional album validation and capture the outcome."""

    if not verified(song.artist, song.title):
        return ValidationResult(status="song_invalid", song=None)

    album_value = (song.album or "").strip() if song.album else ""
    if album_value:
        try:
            if verified_album(song.artist, song.title, album_value):
                return ValidationResult(
                    status="valid",
                    song=song.copy(update={"validated": True}),
                    album=album_value,
                    album_validated=True,
                )

            invalid_albums.append(
                {
                    "Artist": song.artist,
                    "Title": song.title,
                    "Album": album_value,
                    "Playlist": playlist_name,
                    "Reason": "not_validated",
                }
            )
            return ValidationResult(
                status="album_failed",
                song=None,
                album=album_value,
                album_validated=False,
                album_reason="not_validated",
            )
        except Exception:
            invalid_albums.append(
                {
                    "Artist": song.artist,
                    "Title": song.title,
                    "Album": album_value,
                    "Playlist": playlist_name,
                    "Reason": "validation_error",
                }
            )
            return ValidationResult(
                status="album_failed",
                song=None,
                album=album_value,
                album_validated=False,
                album_reason="validation_error",
            )

    return ValidationResult(
        status="valid",
        song=song.copy(update={"validated": True}),
        album=None,
        album_validated=None,
    )


def openai_text_completion(
    prompt: str,
    system_prompt: str = None,
    model: str = "gpt-4o",
    response_format=None,
):
    client = get_openai_client()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    if response_format:
        # For structured output (pydantic model)
        completion = client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=response_format,
        )
        return completion.choices[0].message.parsed
    else:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return completion.choices[0].message.content


def openai_speech(
    text: str,
    outfile: str,
    model: str = "gpt-4o-mini-tts",
    voice: str = "ash",
    instructions: str = None,
):
    client = get_openai_client()
    kwargs = {
        "model": model,
        "voice": voice,
        "input": text,
    }
    if instructions:
        kwargs["instructions"] = instructions
    with client.audio.speech.with_streaming_response.create(**kwargs) as response:
        response.stream_to_file(outfile)


def youtube_to_mp3(query: str, outfile: str):
    # Exclude live, documentary, interview, cover, remix, and lyric videos in the search query
    filtered_query = f"{query}"
    cmd = [
        "yt-dlp",
        f"ytsearch1:{filtered_query}",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        outfile,
        "--quiet",
    ]
    subprocess.run(cmd, check=True)
    print(f"Downloaded: {outfile}")


def ensure_easyid3(path: str) -> EasyID3:
    """Load EasyID3 tags, creating a fresh header when missing."""
    try:
        return EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
        tags.save(path)
        return EasyID3(path)


def tag_mp3(
    path: str,
    artist: str,
    title: str,
    year: str,
    genre: str,
    album: Optional[str] = None,
):
    print(
        f"Tagging {path} with artist: {artist}, title: {title}, year: {year}, genre: {genre}"
    )
    audio = ensure_easyid3(path)
    audio["artist"] = artist
    audio["title"] = title
    audio["date"] = year
    audio["genre"] = genre
    if album and str(album).strip():  # write album if provided
        audio["album"] = str(album).strip()
    audio.save()

    # Add album art
    if album and str(album).strip():
        # Use MusicBrainz cover art when album is available
        try:
            embed_from_artist_album(path, artist, str(album).strip())
        except Exception as e:
            print(f"Warning: Failed to embed cover art from MusicBrainz: {e}")
    else:
        # Fallback to local thumbnail if no album available
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
            id3.save(path)
            id3 = ID3(path)
        except error:
            id3 = ID3()
        thumbnail_path = os.path.join(os.path.dirname(__file__), "Thumbnail_logo.png")
        if os.path.exists(thumbnail_path):
            with open(thumbnail_path, "rb") as img:
                id3.add(
                    APIC(
                        encoding=3,  # UTF-8
                        mime="image/png",
                        type=3,  # Cover (front)
                        desc="Cover",
                        data=img.read(),
                    )
                )
            id3.save(path)

    # Apply ReplayGain to the newly downloaded mp3
    print(f"Applying ReplayGain to {path}")
    try:
        subprocess.run(["mp3gain", "-r", "-k", str(path)], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error applying ReplayGain to {path}: {e}")


def make_fun_fact(artist: str, title: str) -> str:
    prompt = (
        f"In one short, upbeat radio-host sentence (≤25 words), "
        f"share a fun fact about the song '{title}' by {artist}."
        " Write it in argentinian spanish. "
    )
    return openai_text_completion(prompt).strip('"\n ')


def tts(text: str, outfile: str):
    instruction_prompt = (
        pathlib.Path("host_instructions_prompt.txt").read_text().strip()
    )
    openai_speech(
        text=text,
        outfile=outfile,
        model="gpt-4o-mini-tts",
        voice="ash",
        instructions=instruction_prompt,
    )


def sftp_upload(local_path, remote_path, hostname, username, password, port=2022):
    transport = paramiko.Transport((hostname, port))
    transport.connect(username=username, password=password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    sftp.put(local_path, remote_path)
    sftp.close()
    transport.close()
    print(f"Uploaded {local_path} to {remote_path} via SFTP")


def get_upcoming_song():
    url = f"{AZURACAST_URL}/api/nowplaying/{STATION}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    # Current song
    now_playing = data["now_playing"]["song"]
    # Next songs (queue)
    upcoming = data.get("playing_next", [])
    return now_playing, upcoming


# —— main pipeline ————————————————————————————————————————————————


def save_playlist_with_validation(playlist_path: pathlib.Path, songs: List[Song]):
    """Persist playlist songs back to CSV without altering data semantics."""

    def _serialize_year(value: Optional[str]) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return text

    path = pathlib.Path(playlist_path)
    cleaned_df = pd.DataFrame(
        [
            {
                "Artist": song.artist,
                "Title": song.title,
                "Year": _serialize_year(song.year),
                "Album": song.album or "",
                "Validated": bool(song.validated),
            }
            for song in songs
        ],
        columns=["Artist", "Title", "Year", "Album", "Validated"],
    )
    cleaned_df.to_csv(path, index=False)
    print(f"Cleaned and sorted playlist saved to {path}")


def delete_marked_mp3_files(
    delete_targets: Dict[Tuple[str, str], Song], songs_root: pathlib.Path
) -> int:
    """Remove MP3 files that correspond to songs flagged with the [DEL] marker."""

    if not delete_targets:
        return 0

    if songs_root is None or not songs_root.exists():
        print("Warning: Songs directory does not exist; cannot delete marked MP3 files")
        return 0

    removed = 0
    for playlist_dir in songs_root.iterdir():
        if not playlist_dir.is_dir():
            continue

        for song in delete_targets.values():
            safe_artist = sanitize_filename_component(song.artist)
            safe_title = sanitize_filename_component(song.title)
            target_file = playlist_dir / f"{safe_artist} - {safe_title}.mp3"
            if not target_file.exists():
                continue

            try:
                target_file.unlink()
                removed += 1
                try:
                    relative_path = target_file.relative_to(songs_root)
                except ValueError:
                    relative_path = target_file
                print(f"🗑️ Deleted MP3 due to [DEL]: {relative_path}")
            except Exception as exc:
                print(f"❌ Failed to delete MP3 {target_file}: {exc}")

    return removed


def main(station_name: str, dry_run: bool = False):  # dry_run flag
    global PLAYLISTS_PATH, STATION_PATH, STATION
    # Determine the base path for stations (the project dir where this script lives)
    script_dir = pathlib.Path(__file__).parent
    stations_base_dir = script_dir  # FIX: stations live under the project dir

    # Set paths based on the station name
    PLAYLISTS_PATH = stations_base_dir / station_name / "playlists"
    STATION_PATH = stations_base_dir / station_name / "songs"

    # Also set AzuraCast station slug from station name (lowercased)
    STATION = station_name.lower()

    print(f"Running for station: {station_name}")
    if dry_run:
        print("Mode: DRY-RUN (no downloads; existing MP3s will be re-tagged if needed)")
    print(f"Playlists path: {PLAYLISTS_PATH}")
    print(f"Songs path: {STATION_PATH}")

    # collect albums that are not validated
    invalid_albums: List[dict] = []

    playlists_dir = pathlib.Path(PLAYLISTS_PATH)
    if not playlists_dir.exists():
        print(f"Playlists directory '{PLAYLISTS_PATH}' does not exist!")
        return

    # Get all CSV files from playlists directory
    playlist_files = list(playlists_dir.glob("*.csv"))
    if not playlist_files:
        print(f"No playlist files found in '{PLAYLISTS_PATH}' directory!")
        return

    # First pass: load playlists and collect deletion markers
    playlist_entries = []
    for playlist_file in playlist_files:
        songs, playlist_needs_save, deletions = load_playlist(playlist_file)
        playlist_entries.append(
            {
                "file": playlist_file,
                "name": playlist_file.stem,
                "songs": songs,
                "needs_save": playlist_needs_save,
                "deletions": deletions,
            }
        )

    deletion_targets: Dict[Tuple[str, str], Song] = {}
    deletion_sources: Dict[Tuple[str, str], set] = {}
    for entry in playlist_entries:
        for song in entry["deletions"]:
            if not song.artist or not song.title:
                continue
            key = playlist_song_key(song)
            if key not in deletion_targets:
                deletion_targets[key] = song
            deletion_sources.setdefault(key, set()).add(entry["name"])

    if deletion_targets:
        print(
            f"\n🛑 Songs marked for deletion via [DEL]: {len(deletion_targets)}"
        )
        for key, song in deletion_targets.items():
            playlists_list = sorted(deletion_sources.get(key, []))
            playlists_note = ", ".join(playlists_list)
            print(
                f"   • {song.artist} - {song.title} (marked in: {playlists_note})"
            )

        deleted_files = delete_marked_mp3_files(deletion_targets, STATION_PATH)
        if deleted_files:
            print(
                f"🗑️ Deleted {deleted_files} MP3 file(s) due to [DEL] markers"
            )

        for entry in playlist_entries:
            songs = entry["songs"]
            filtered_songs = [
                song
                for song in songs
                if playlist_song_key(song) not in deletion_targets
            ]
            removed_count = len(songs) - len(filtered_songs)
            if removed_count > 0:
                entry["songs"] = filtered_songs
                entry["needs_save"] = True
                entry["removed_via_marker"] = removed_count

    # Store all songs across playlists for repetition analysis
    all_songs_by_playlist = {}

    for entry in playlist_entries:
        playlist_file = entry["file"]
        playlist_name = entry["name"]
        print(f"\n--------------------------------------------")
        print(f"Processing playlist: {playlist_name}")

        songs = entry["songs"]
        playlist_needs_save = entry["needs_save"]
        removed_via_marker = entry.get("removed_via_marker", 0)

        if removed_via_marker:
            print(
                f"   • Removed {removed_via_marker} song(s) marked with [DEL] from playlist"
            )

        # Create directory for this playlist (needed for MP3 backfill)
        music_dir = pathlib.Path(STATION_PATH, playlist_name)
        music_dir.mkdir(parents=True, exist_ok=True)

        songs, library_changed, added_from_files = backfill_songs_from_library(
            playlist_name, songs, music_dir
        )
        songs, normalized_changed, duplicates_removed = deduplicate_and_sort_songs(songs)

        if duplicates_removed > 0:
            print(f"Removed {duplicates_removed} duplicate(s) from {playlist_file}")

        if playlist_needs_save or library_changed or normalized_changed:
            save_playlist_with_validation(playlist_file, songs)

        if not songs:
            print(f"No valid songs found in {playlist_file}")
            entry["songs"] = songs
            continue

        # Store songs for analysis BEFORE any further processing
        all_songs_by_playlist[playlist_name] = [
            Song(
                artist=song.artist,
                title=song.title,
                year=song.year,
                album=song.album,
                validated=song.validated,
            )
            for song in songs
        ]

        entry["songs"] = songs

        print(f"Found {len(songs)} songs in playlist:")
        print("")

        # Separate songs into validated and unvalidated
        validated_songs = [song for song in songs if song.validated]
        unvalidated_songs = [song for song in songs if not song.validated]

        print(f"📊 Validation Statistics:")
        print(f"   Previously validated songs: {len(validated_songs)}")
        print(f"   Songs needing validation: {len(unvalidated_songs)}")

        # Check which songs already exist and which need to be downloaded
        existing_songs = []
        missing_songs = []

        for song in songs:
            artist = song.artist
            title = song.title
            year = song.year

            # Create safe filename
            safe_artist = sanitize_filename_component(artist)
            safe_title = sanitize_filename_component(title)
            song_path = music_dir / f"{safe_artist} - {safe_title}.mp3"

            if song_path.exists():
                existing_songs.append((song, song_path))
            else:
                missing_songs.append((song, song_path))

        # Report statistics
        total_songs = len(songs)
        existing_count = len(existing_songs)
        missing_count = len(missing_songs)

        print(f"📊 Download Statistics:")
        print(f"   Total songs in playlist: {total_songs}")
        print(f"   Already downloaded: {existing_count}")
        print(f"   Need to download: {missing_count}")

        # In dry-run, audit and fix tags on existing files (set Album/others if missing/mismatched)
        if dry_run and existing_songs:
            print("\n🖊️ DRY-RUN: Auditing and fixing ID3 tags for existing songs...")
            for song, song_path in existing_songs:
                try:
                    audio = EasyID3(str(song_path))
                    cur_artist = (
                        audio.get("artist", [""])[0] if audio.get("artist") else ""
                    )
                    cur_title = (
                        audio.get("title", [""])[0] if audio.get("title") else ""
                    )
                    cur_year = audio.get("date", [""])[0] if audio.get("date") else ""
                    cur_genre = (
                        audio.get("genre", [""])[0] if audio.get("genre") else ""
                    )
                    cur_album = (
                        audio.get("album", [""])[0] if audio.get("album") else ""
                    )
                except Exception as e:
                    print(
                        f"   • {song_path.name}: cannot read tags ({e}), writing fresh tags"
                    )
                    tag_mp3(
                        str(song_path),
                        song.artist,
                        song.title,
                        song.year,
                        playlist_name,
                        song.album,
                    )
                    continue

                needs = []
                if cur_artist.strip() != song.artist.strip():
                    needs.append("artist")
                if cur_title.strip() != song.title.strip():
                    needs.append("title")
                if cur_year.strip() != song.year.strip():
                    needs.append("year")
                if cur_genre.strip() != playlist_name:
                    needs.append("genre")
                if song.album and str(song.album).strip():
                    if cur_album.strip() != str(song.album).strip():
                        needs.append("album")
                # If album missing but provided in CSV
                elif cur_album.strip() and not (song.album and str(song.album).strip()):
                    # CSV has no album but file has one; do not erase it
                    pass

                if needs:
                    print(f"   • {song_path.name}: updating tags ({', '.join(needs)})")
                    tag_mp3(
                        str(song_path),
                        song.artist,
                        song.title,
                        song.year,
                        playlist_name,
                        song.album,
                    )

        # Validate existing songs (only unvalidated ones)
        songs_to_remove_from_playlist = []
        validation_updates = False

        if existing_count > 0:
            unvalidated_existing = [
                (song, path) for song, path in existing_songs if not song.validated
            ]

            if unvalidated_existing:
                print(
                    f"\n🔍 Validating {len(unvalidated_existing)} unvalidated existing songs..."
                )
                valid_existing: List[Tuple[Song, pathlib.Path]] = []
                invalid_existing: List[Tuple[Song, pathlib.Path]] = []

                for song, song_path in unvalidated_existing:
                    result = perform_song_validation(song, playlist_name, invalid_albums)

                    if result.album_validated is True and result.album:
                        print(f"   ↳ Album validated: {result.album}")
                    elif result.album_validated is False and result.album:
                        if result.album_reason == "validation_error":
                            print(
                                f"   ↳ Album validation error (skipped): {result.album}"
                            )
                        else:
                            print(f"   ↳ Album not validated: {result.album}")

                    if result.status == "valid" and result.song:
                        replace_song_entry(songs, result.song)
                        valid_existing.append((result.song, song_path))
                        validation_updates = True
                        print(
                            f"✓ Validated: {result.song.artist} - {result.song.title}"
                        )
                    elif result.status == "album_failed":
                        print(
                            f"   ↳ Keeping Validated=False due to album validation failure"
                        )
                    else:
                        invalid_existing.append((song, song_path))
                        songs_to_remove_from_playlist.append(song)

                if invalid_existing:
                    print(f"\n❌ Invalid existing songs ({len(invalid_existing)}):")
                    for song, song_path in invalid_existing:
                        print(
                            f"   • {song.artist} - {song.title} (file: {song_path.name})"
                        )

                    # Delete invalid MP3 files
                    for song, song_path in invalid_existing:
                        try:
                            song_path.unlink()
                            print(f"🗑️ Deleted invalid file: {song_path.name}")
                        except Exception as e:
                            print(f"❌ Failed to delete {song_path.name}: {e}")

                    print(f"   🗑️ Deleted {len(invalid_existing)} invalid MP3 file(s)")
            else:
                print(f"\n✅ All existing songs are already validated")
        else:
            print(f"\n📁 No existing songs to validate")

        # Validate missing songs (ensure BOTH previously validated and newly validated get downloaded)
        # BUGFIX: Previously, if any unvalidated songs existed, already validated-but-missing songs
        # were skipped from downloads. We now always include them.
        pre_validated_missing = [
            (song, path) for song, path in missing_songs if song.validated
        ]
        unvalidated_missing = [
            (song, path) for song, path in missing_songs if not song.validated
        ]

        if unvalidated_missing:
            print(
                f"\n🔍 Validating {len(unvalidated_missing)} songs before download..."
            )

            newly_validated: List[Tuple[Song, pathlib.Path]] = []
            invalid_songs: List[Tuple[Song, pathlib.Path]] = []

            for song, song_path in unvalidated_missing:
                result = perform_song_validation(song, playlist_name, invalid_albums)

                if result.album_validated is True and result.album:
                    print(f"   ↳ Album validated: {result.album}")
                elif result.album_validated is False and result.album:
                    if result.album_reason == "validation_error":
                        print(
                            f"   ↳ Album validation error (skipped): {result.album}"
                        )
                    else:
                        print(f"   ↳ Album not validated: {result.album}")

                if result.status == "valid" and result.song:
                    replace_song_entry(songs, result.song)
                    newly_validated.append((result.song, song_path))
                    validation_updates = True
                    print(
                        f"✓ Validated for download: {result.song.artist} - {result.song.title}"
                    )
                elif result.status == "album_failed":
                    print(
                        "   ↳ Skipping download; keeping Validated=False due to album validation failure"
                    )
                else:
                    invalid_songs.append((song, song_path))
                    print(f"❌ Invalid/not found: {song.artist} - {song.title}")
                    songs_to_remove_from_playlist.append(song)

            # Combine already validated + newly validated
            valid_songs = pre_validated_missing + newly_validated
            if pre_validated_missing:
                print(
                    f"✓ {len(pre_validated_missing)} already validated song(s) pending download"
                )

            valid_count = len(valid_songs)
            invalid_count = len(invalid_songs)
            print(f"   Valid songs to download: {valid_count}")
            print(f"   Invalid/not found songs: {invalid_count}")
        else:
            # No unvalidated songs; all missing songs are already validated
            invalid_songs = []
            valid_songs = pre_validated_missing  # all of them
            valid_count = len(valid_songs)
            invalid_count = 0
            print(
                f"\n⏭️ No unvalidated missing songs. {valid_count} already validated song(s) pending download."
            )

        # Save validation updates to playlist
        if validation_updates or songs_to_remove_from_playlist:
            # Remove invalid songs
            if songs_to_remove_from_playlist:
                original_song_count = len(songs)
                songs = [
                    song for song in songs if song not in songs_to_remove_from_playlist
                ]
                removed_count = original_song_count - len(songs)
                print(
                    f"📝 Removed {removed_count} invalid song(s) from playlist CSV file"
                )

            # Save updated playlist with validation status
            save_playlist_with_validation(str(playlist_file), songs)
            print(f"📝 Updated validation status in playlist CSV file")

        if valid_count == 0 and missing_count > 0:
            print(f"\n❌ No valid songs to download for playlist '{playlist_name}'!")
            # In DRY-RUN, still continue to summary
        elif missing_count == 0:
            print(
                f"\n🎉 All songs are already downloaded for playlist '{playlist_name}'!"
            )
            # continue  # keep existing control flow if present

        # Downloads are skipped in dry-run mode
        if dry_run:
            print(
                f"\n⬇ [DRY-RUN] Would download {valid_count} song(s): skipping downloads."
            )
            downloaded_count = 0
            failed_count = 0
        else:
            print(f"\n⬇ Starting downloads ({valid_count} valid songs):")

            # Process only valid missing songs
            downloaded_count = 0
            failed_count = 0

            for idx, (song, song_path) in enumerate(valid_songs, start=1):
                artist = song.artist
                title = song.title
                year = song.year
                try:
                    print(f"⬇ [{idx}/{valid_count}] Downloading: {artist} - {title}")
                    youtube_to_mp3(f"{artist} {title}", str(song_path))
                    tag_mp3(
                        str(song_path), artist, title, year, playlist_name, song.album
                    )
                    print(f"✓ Downloaded and tagged: {artist} - {title}")
                    downloaded_count += 1
                except subprocess.CalledProcessError as e:
                    print(f"✗ Failed to download {artist} - {title}: {e}")
                    failed_count += 1
                    continue

        # Final summary
        total_removed = len(songs_to_remove_from_playlist)
        final_song_count = len(songs)

        print(f"\n📋 Final Summary for '{playlist_name}':")
        print(f"   Original songs in playlist: {total_songs}")
        print(f"   Songs removed (invalid): {total_removed}")
        print(f"   Final songs in playlist: {final_song_count}")
        print(f"   Successfully downloaded: {downloaded_count}")
        print(f"   Failed downloads: {failed_count}")

        if failed_count > 0:
            print(f"   ⚠️ {failed_count} song(s) failed to download")
        if total_removed > 0:
            print(
                f"   🗑️ {total_removed} invalid song(s) removed from playlist and files deleted"
            )

        print(f"Finished processing playlist: {playlist_name}")

    # Analyze cross-playlist repetition
    # REPLACED prints with log-to-file
    analysis_lines: List[str] = []

    def log(line: str = ""):
        analysis_lines.append(line)

    log("\n" + "=" * 60)
    log("📊 CROSS-PLAYLIST REPETITION ANALYSIS")
    log("=" * 60)

    # Create a dictionary to track songs and which playlists they appear in
    song_appearances = {}

    for playlist_name, playlist_songs in all_songs_by_playlist.items():
        for song in playlist_songs:
            # Use a more robust key that handles case and whitespace
            song_key = (song.artist.lower().strip(), song.title.lower().strip())
            if song_key not in song_appearances:
                song_appearances[song_key] = {"song": song, "playlists": []}
            song_appearances[song_key]["playlists"].append(playlist_name)

    # Find duplicates (songs appearing in more than one playlist)
    duplicates = {k: v for k, v in song_appearances.items() if len(v["playlists"]) > 1}

    total_songs = sum(
        len(playlist_songs) for playlist_songs in all_songs_by_playlist.values()
    )
    total_unique_songs = len(song_appearances)
    duplicate_songs = len(duplicates)
    unique_songs = total_unique_songs - duplicate_songs

    log(f"\n📈 Summary:")
    log(f"   Total songs across all playlists: {total_songs}")
    log(f"   Total unique songs across all playlists: {total_unique_songs}")
    log(f"   Songs appearing in multiple playlists: {duplicate_songs}")
    log(f"   Songs appearing in only one playlist: {unique_songs}")

    if duplicate_songs > 0:
        duplication_percentage = (duplicate_songs / total_unique_songs) * 100
        log(f"   Duplication rate: {duplication_percentage:.1f}%")

        log(f"\n🔄 Songs appearing in multiple playlists:")

        # Sort duplicates by number of appearances (descending)
        sorted_duplicates = sorted(
            duplicates.items(), key=lambda x: len(x[1]["playlists"]), reverse=True
        )

        for song_key, info in sorted_duplicates:
            song = info["song"]
            playlists = info["playlists"]
            playlist_count = len(playlists)

            log(f"\n   🎵 {song.artist} - {song.title} ({song.year})")
            log(f"      Appears in {playlist_count} playlists: {', '.join(playlists)}")

        # Show statistics by number of appearances
        appearance_counts = {}
        for info in duplicates.values():
            count = len(info["playlists"])
            appearance_counts[count] = appearance_counts.get(count, 0) + 1

        log(f"\n📊 Breakdown by number of appearances:")
        for count in sorted(appearance_counts.keys(), reverse=True):
            songs_count = appearance_counts[count]
            log(f"   {songs_count} song(s) appear in {count} playlists")
    else:
        log(f"\n✅ No duplicate songs found across playlists!")

    log("\n" + "=" * 60)

    # Write analysis to a station-scoped log file
    analysis_log_file = STATION_PATH.parent / "duplicate_analysis.log"
    with open(analysis_log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(analysis_lines) + "\n")
    print(f"📝 Cross-playlist analysis written to {analysis_log_file}")

    # write albums that were not validated to CSV in the station directory
    invalid_albums_path = STATION_PATH.parent / "albums_not_validated.csv"
    # Deduplicate entries
    if invalid_albums:
        deduped = []
        seen = set()
        for row in invalid_albums:
            key = (
                row["Artist"].lower().strip(),
                row["Title"].lower().strip(),
                row["Album"].lower().strip(),
                row["Playlist"].lower().strip(),
                row["Reason"],
            )
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        invalid_albums = deduped

    df = pd.DataFrame(
        invalid_albums or [],
        columns=["Artist", "Title", "Album", "Playlist", "Reason"],
    )
    df.to_csv(invalid_albums_path, index=False)
    print(
        f"📝 Albums not validated written to {invalid_albums_path} ({len(df)} row(s))"
    )


def list_playlists(station_name: str):
    """List all available playlists."""
    global PLAYLISTS_PATH, STATION_PATH
    # Determine the base path for stations (the project dir where this script lives)
    script_dir = pathlib.Path(__file__).parent
    stations_base_dir = script_dir  # FIX: stations live under the project dir

    # Set paths based on the station name
    PLAYLISTS_PATH = stations_base_dir / station_name / "playlists"
    STATION_PATH = stations_base_dir / station_name / "songs"

    playlists_dir = pathlib.Path(PLAYLISTS_PATH)
    if not playlists_dir.exists():
        print(f"Playlists directory '{PLAYLISTS_PATH}' does not exist!")
        return

    playlist_files = list(playlists_dir.glob("*.csv"))
    if not playlist_files:
        print(f"No playlist files found in '{PLAYLISTS_PATH}' directory!")
        return

    print("Available playlists:")
    for idx, playlist_file in enumerate(playlist_files):
        playlist_name = playlist_file.stem
        songs, _, _ = load_playlist(playlist_file)
        print(f"{idx}: {playlist_name} ({len(songs)} songs)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI-assisted local-network radio pipeline."
    )
    parser.add_argument(
        "-s",
        "--station",
        type=str,
        help="The name of the radio station to process (e.g., NeuralCast, NeuralForge).",
    )
    # dry-run flag
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Dry run: validate and re-tag existing MP3s, but skip new downloads.",
    )
    args = parser.parse_args()

    # Default to 'NeuralCast' if not provided
    station = args.station or "NeuralCast"

    list_playlists(station)
    main(station, args.dry_run)  # pass dry-run flag
