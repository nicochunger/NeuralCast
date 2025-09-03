#!/usr/bin/env python3
"""
main.py — AI-assisted local-network radio pipeline
-------------------------------------------------
• reads playlists from playlists/ directory
• yt-dlp + ffmpeg  → MP3s
• mutagen          → ID3 tags
• moves files into songs/ directory organized by playlist

# OPENAI_API_KEY="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"
"""

import json
import os, subprocess, pathlib
from typing import List, Optional
import openai
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, error
from pydantic import BaseModel
import paramiko
import requests
from dotenv import load_dotenv
import pandas as pd
from validators import verified, verified_album
import argparse


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


# Pydantic models for structured output
class Song(BaseModel):
    artist: str
    title: str
    year: str
    album: Optional[str] = None  # NEW: optional album support
    validated: bool = False  # Add validated field with default value


class Playlist(BaseModel):
    songs: List[Song]


# —— helpers ————————————————————————————————————————————————————————————


def read_playlist_file(playlist_path: str) -> List[Song]:
    """Read a CSV playlist file, remove duplicates, sort, and return list of Song objects."""
    # Make sure Year remains a string; avoid automatic NaN->float promotion
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
    songs = []

    # Check if Validated column exists, if not add it
    if "Validated" not in df.columns:
        df["Validated"] = False
        print(f"Added 'Validated' column to {playlist_path}")

    for _, row in df.iterrows():
        # Handle different possible column names (case insensitive)
        artist = None
        title = None
        year = None
        album = None  # NEW
        validated = False

        for col in df.columns:
            cl = col.lower()
            if cl == "artist":
                artist = str(row[col]).strip()
            elif cl == "title":
                title = str(row[col]).strip()
            elif cl == "year":
                year = str(row[col]).strip()
            elif cl == "album":  # NEW: optional album
                album = str(row[col]).strip() if pd.notna(row[col]) else None
            elif cl == "validated":
                validated = bool(row[col]) if pd.notna(row[col]) else False

        if (
            artist
            and title
            and year
            and artist != "nan"
            and title != "nan"
            and year != "nan"
        ):
            song = Song(
                artist=artist, title=title, year=year, album=album, validated=validated
            )  # NEW: pass album
            songs.append(song)
        else:
            print(
                f"Warning: Skipping incomplete row in {playlist_path}: Artist={artist}, Title={title}, Year={year}"
            )

    # Check for existing MP3 files in songs directory and add them to playlist if missing
    playlist_name = pathlib.Path(playlist_path).stem
    # music_dir = pathlib.Path(STATION_PATH, playlist_name)
    if STATION_PATH:
        music_dir = pathlib.Path(STATION_PATH, playlist_name)
    else:
        music_dir = None

    if music_dir and music_dir.exists():
        existing_mp3s = list(music_dir.glob("*.mp3"))
        added_from_files = 0

        for mp3_file in existing_mp3s:
            try:
                # Try to get metadata from the MP3 file
                audio = EasyID3(str(mp3_file))
                file_artist = (
                    audio.get("artist", [""])[0] if audio.get("artist") else ""
                )
                file_title = audio.get("title", [""])[0] if audio.get("title") else ""
                file_year = audio.get("date", [""])[0] if audio.get("date") else ""
                file_album = (
                    audio.get("album", [""])[0] if audio.get("album") else ""
                )  # NEW

                # If we can't get metadata, try to parse from filename
                if not file_artist or not file_title:
                    filename = mp3_file.stem
                    if " - " in filename:
                        parts = filename.split(" - ", 1)
                        file_artist = (
                            parts[0].strip() if not file_artist else file_artist
                        )
                        file_title = parts[1].strip() if not file_title else file_title

                # Only add if we have at least artist and title
                if file_artist and file_title:
                    # Check if this song is already in the playlist
                    existing = any(
                        song.artist.lower() == file_artist.lower()
                        and song.title.lower() == file_title.lower()
                        for song in songs
                    )

                    if not existing:
                        # NEW: ensure filename matches "{artist} - {title}.mp3"
                        safe_artist = (
                            file_artist.replace("/", " ").replace("\\", " ").strip()
                        )
                        safe_title = (
                            file_title.replace("/", " ").replace("\\", " ").strip()
                        )
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
                                    print(
                                        f"Renamed file: {mp3_file.name} -> {expected_name}"
                                    )
                                    mp3_file = target_path
                            except Exception as e:
                                print(
                                    f"Warning: Could not rename {mp3_file.name} -> {expected_name}: {e}"
                                )

                        # Use year from metadata or default to "Unknown"
                        year_to_use = file_year if file_year else "Unknown"
                        song = Song(
                            artist=file_artist,
                            title=file_title,
                            year=year_to_use,
                            album=file_album
                            or None,  # NEW: capture album from file if present
                            validated=False,
                        )
                        songs.append(song)
                        added_from_files += 1
                        print(f"Added from existing file: {file_artist} - {file_title}")
            except Exception as e:
                print(f"Warning: Could not read metadata from {mp3_file}: {e}")

        if added_from_files > 0:
            print(f"Added {added_from_files} song(s) from existing MP3 files")
    else:
        if music_dir:
            print(
                f"Warning: Music directory '{music_dir}' does not exist, skipping MP3 file check"
            )
        else:
            print("Warning: STATION_PATH is not set; skipping MP3 file check")
        added_from_files = 0

    # Remove duplicates based on artist and title (case insensitive)
    unique_songs = []
    seen_combinations = set()

    for song in songs:
        key = (song.artist.lower(), song.title.lower())
        if key not in seen_combinations:
            unique_songs.append(song)
            seen_combinations.add(key)

    duplicates_removed = len(songs) - len(unique_songs)
    if duplicates_removed > 0:
        print(f"Removed {duplicates_removed} duplicate(s) from {playlist_path}")

    # Sort by artist first, then by title
    unique_songs.sort(key=lambda song: (song.artist.lower(), song.title.lower()))

    # Save the cleaned and sorted playlist back to file
    if (
        duplicates_removed > 0
        or added_from_files > 0
        or songs != unique_songs
        or "Validated" not in pd.read_csv(playlist_path).columns
    ):
        cleaned_df = pd.DataFrame(
            [
                {
                    "Artist": song.artist,
                    "Title": song.title,
                    "Year": str(int(song.year)),
                    "Album": song.album or "",  # NEW: preserve album column
                    "Validated": song.validated,
                }
                for song in unique_songs
            ]
        )
        cleaned_df.to_csv(playlist_path, index=False)
        print(f"Cleaned and sorted playlist saved to {playlist_path}")

    return unique_songs


def openai_text_completion(
    prompt: str,
    system_prompt: str = None,
    model: str = "gpt-4o",
    response_format=None,
):
    client = openai.OpenAI(api_key=OPENAI_KEY)
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
    client = openai.OpenAI(api_key=OPENAI_KEY)
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
    audio = EasyID3(path)
    audio["artist"] = artist
    audio["title"] = title
    audio["date"] = year
    audio["genre"] = genre
    if album and str(album).strip():  # NEW: write album if provided
        audio["album"] = str(album).strip()
    audio.save()

    # Add album art (thumbnail)
    try:
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


def save_playlist_with_validation(playlist_path: str, songs: List[Song]):
    """Save playlist with validation status."""
    cleaned_df = pd.DataFrame(
        [
            {
                "Artist": song.artist,
                "Title": song.title,
                "Album": song.album or "",
                "Year": str(int(song.year)),
                "Validated": song.validated,
            }
            for song in songs
        ]
    )
    cleaned_df.to_csv(playlist_path, index=False)


def main(station_name: str, dry_run: bool = False):  # NEW: dry_run flag
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

    # NEW: collect albums that are not validated
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

    # Store all songs across playlists for repetition analysis
    all_songs_by_playlist = {}

    for playlist_file in playlist_files:
        playlist_name = playlist_file.stem  # filename without extension
        print(f"\n--------------------------------------------")
        print(f"Processing playlist: {playlist_name}")

        # Read songs from playlist file
        songs = read_playlist_file(str(playlist_file))
        if not songs:
            print(f"No valid songs found in {playlist_file}")
            continue

        # Store songs for analysis BEFORE any processing/modification
        # Create a deep copy to ensure independence
        all_songs_by_playlist[playlist_name] = [
            Song(
                artist=song.artist,
                title=song.title,
                year=song.year,
                album=song.album,  # NEW
                validated=song.validated,
            )
            for song in songs
        ]

        print(f"Found {len(songs)} songs in playlist:")
        print("")

        # Create directory for this playlist
        music_dir = pathlib.Path(STATION_PATH, playlist_name)
        music_dir.mkdir(parents=True, exist_ok=True)

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
            safe_artist = artist.replace("/", " ").replace("\\", " ")
            safe_title = title.replace("/", " ").replace("\\", " ")
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

        # NEW: In dry-run, audit and fix tags on existing files (set Album/others if missing/mismatched)
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
                valid_existing = []
                invalid_existing = []

                for song, song_path in unvalidated_existing:
                    if verified(song.artist, song.title):
                        # NEW: Only set validated=True if album (when present) is validated
                        album_ok = True
                        if song.album and str(song.album).strip():
                            try:
                                if verified_album(song.artist, song.title, song.album):
                                    print(f"   ↳ Album validated: {song.album}")
                                else:
                                    album_ok = False
                                    print(f"   ↳ Album not validated: {song.album}")
                                    invalid_albums.append(
                                        {
                                            "Artist": song.artist,
                                            "Title": song.title,
                                            "Album": str(song.album).strip(),
                                            "Playlist": playlist_name,
                                            "Reason": "album_not_validated",
                                        }
                                    )
                            except Exception:
                                album_ok = False
                                print(
                                    f"   ↳ Album validation error (skipped): {song.album}"
                                )
                                invalid_albums.append(
                                    {
                                        "Artist": song.artist,
                                        "Title": song.title,
                                        "Album": str(song.album).strip(),
                                        "Playlist": playlist_name,
                                        "Reason": "album_validation_error",
                                    }
                                )

                        if album_ok:
                            updated_song = Song(
                                artist=song.artist,
                                title=song.title,
                                year=song.year,
                                album=song.album,
                                validated=True,
                            )
                            for i, s in enumerate(songs):
                                if s.artist == song.artist and s.title == song.title:
                                    songs[i] = updated_song
                                    break
                            valid_existing.append((updated_song, song_path))
                            validation_updates = True
                            print(f"✓ Validated: {song.artist} - {song.title}")
                        else:
                            # Keep entry and file; remain unvalidated
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

        # Validate missing songs (only unvalidated ones)
        unvalidated_missing = [
            (song, path) for song, path in missing_songs if not song.validated
        ]

        if unvalidated_missing:
            print(
                f"\n🔍 Validating {len(unvalidated_missing)} songs before download..."
            )

            # Validate missing songs before downloading
            valid_songs = []
            invalid_songs = []

            for song, song_path in unvalidated_missing:
                if verified(song.artist, song.title):
                    # NEW: Only set validated=True and schedule download if album (when present) is validated
                    album_ok = True
                    if song.album and str(song.album).strip():
                        try:
                            if verified_album(song.artist, song.title, song.album):
                                print(f"   ↳ Album validated: {song.album}")
                            else:
                                album_ok = False
                                print(f"   ↳ Album not validated: {song.album}")
                                invalid_albums.append(
                                    {
                                        "Artist": song.artist,
                                        "Title": song.title,
                                        "Album": str(song.album).strip(),
                                        "Playlist": playlist_name,
                                        "Reason": "not_validated",
                                    }
                                )
                        except Exception:
                            album_ok = False
                            print(
                                f"   ↳ Album validation error (skipped): {song.album}"
                            )
                            invalid_albums.append(
                                {
                                    "Artist": song.artist,
                                    "Title": song.title,
                                    "Album": str(song.album).strip(),
                                    "Playlist": playlist_name,
                                    "Reason": "validation_error",
                                }
                            )

                    if album_ok:
                        updated_song = Song(
                            artist=song.artist,
                            title=song.title,
                            year=song.year,
                            album=song.album,
                            validated=True,
                        )
                        # Replace the song in the songs list
                        for i, s in enumerate(songs):
                            if s.artist == song.artist and s.title == song.title:
                                songs[i] = updated_song
                                break
                        valid_songs.append((updated_song, song_path))
                        validation_updates = True
                        print(f"✓ Validated for download: {song.artist} - {song.title}")
                    else:
                        # Keep entry; do not download; remain unvalidated
                        print(
                            f"   ↳ Skipping download; keeping Validated=False due to album validation failure"
                        )
                else:
                    invalid_songs.append((song, song_path))
                    print(f"❌ Invalid/not found: {song.artist} - {song.title}")
                    songs_to_remove_from_playlist.append(song)

            valid_count = len(valid_songs)
            invalid_count = len(invalid_songs)
            print(f"   Valid songs to download: {valid_count}")
            print(f"   Invalid/not found songs: {invalid_count}")
        else:
            valid_songs = [
                (
                    song,
                    music_dir
                    / f"{song.artist.replace('/', ' ').replace('\\', ' ')} - {song.title.replace('/', ' ').replace('\\', ' ')}.mp3",
                )
                for song, _ in missing_songs
                if song.validated
            ]
            invalid_songs = []
            valid_count = len(valid_songs)
            invalid_count = 0
            print(
                f"\n⏭️ Skipping validation for {len(missing_songs) - valid_count} already validated missing songs"
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

        # NEW: Downloads are skipped in dry-run mode
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

    # NEW: write albums that were not validated to CSV in the station directory
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
        songs = read_playlist_file(str(playlist_file))
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
    # NEW: dry-run flag
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
    main(station, args.dry_run)  # NEW: pass dry-run flag
