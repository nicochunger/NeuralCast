#!/usr/bin/env python3
"""
main.py — AI-assisted local-network radio pipeline
-------------------------------------------------
• reads playlists from playlists/ directory
• yt-dlp + ffmpeg  → MP3s
• mutagen          → ID3 tags
• moves files into songs/ directory organized by playlist
"""

import argparse
import json
import pathlib
import unicodedata
from subprocess import CalledProcessError
from typing import Dict, List, Optional, Tuple

import pandas as pd
from mutagen.easyid3 import EasyID3

from audio_utils import tag_mp3, youtube_to_mp3
from models import Song, ValidationResult
from playlist_utils import (
    backfill_songs_from_library,
    deduplicate_and_sort_songs,
    delete_marked_mp3_files,
    load_playlist,
    playlist_song_key,
    replace_song_entry,
    sanitize_filename_component,
    save_playlist_with_validation,
)
from openai_utils import make_fun_fact, openai_speech, openai_text_completion, tts
from validation_utils import perform_song_validation


# The following paths will be set dynamically based on the station argument
STATION_PATH = None
PLAYLISTS_PATH = None

AZURACAST_URL = "http://192.168.1.162/"
STATION = "neuralcast"

TTS = False  # turn off if you only want music
VOICE_NAME = "Adam"  # ElevenLabs voice


def remove_new_releases_metadata_entries(
    playlists_dir: pathlib.Path, songs_to_remove: List[Song]
) -> int:
    metadata_path = playlists_dir / "New Releases.metadata.json"
    if not songs_to_remove:
        return 0
    if not metadata_path.exists():
        print(
            f"⚠️ Metadata file not found at {metadata_path}; skipping metadata cleanup for New Releases"
        )
        return 0

    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Failed to parse JSON from metadata file {metadata_path}: {exc}")
        return 0

    if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
        entries = payload["entries"]
        wrapped = True
    elif isinstance(payload, dict):
        entries = payload
        wrapped = False
    else:
        print(
            f"⚠️ Unexpected metadata structure in {metadata_path}; skipping removal of New Releases entries"
        )
        return 0

    def normalize_component(value: Optional[str]) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        return normalized.strip().casefold()

    def normalize_year(value: Optional[str]) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            return str(int(text))
        except ValueError:
            return text

    def matching_keys(song: Song) -> List[str]:
        artist_component = normalize_component(song.artist)
        title_component = normalize_component(song.title)
        album_component = normalize_component(song.album) if song.album else ""
        year_component = normalize_year(song.year)

        primary_key = "|".join(
            (artist_component, title_component, album_component, year_component)
        )
        if primary_key in entries:
            return [primary_key]

        album_filter = album_component or None
        year_filter = year_component or None
        candidates: List[str] = []
        for existing_key in entries.keys():
            parts = existing_key.split("|")
            if len(parts) != 4:
                continue
            if parts[0] != artist_component or parts[1] != title_component:
                continue
            if album_filter is not None and parts[2] != album_component:
                continue
            if year_filter is not None and parts[3] != year_component:
                continue
            candidates.append(existing_key)
        return candidates

    removed = 0
    missing: List[Song] = []
    ambiguous: List[Song] = []
    seen_keys = set()

    for song in songs_to_remove:
        unique_key = (
            song.artist.lower().strip(),
            song.title.lower().strip(),
            (song.album or "").strip().lower(),
            (song.year or "").strip(),
        )
        if unique_key in seen_keys:
            continue
        seen_keys.add(unique_key)

        matches = matching_keys(song)
        if not matches:
            missing.append(song)
            continue
        if len(matches) > 1:
            ambiguous.append(song)
            continue

        entries.pop(matches[0], None)
        removed += 1
        song_year = normalize_year(song.year)
        print(f"🗑️ Removed metadata entry for {song.artist} - {song.title} ({song_year})")

    if removed > 0:
        output_payload = {"entries": entries} if wrapped else entries
        try:
            with metadata_path.open("w", encoding="utf-8") as handle:
                json.dump(output_payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print(
                f"🗂️ Updated metadata file: {metadata_path.name} (removed {removed} entr{'y' if removed == 1 else 'ies'})"
            )
        except TypeError as exc:
            print(f"⚠️ JSON serialization error while writing metadata file {metadata_path}: {exc}")
        except OSError as exc:
            print(f"⚠️ File write permission error for metadata file {metadata_path}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Unexpected error while writing metadata file {metadata_path}: {exc}")

    for song in missing:
        song_year = normalize_year(song.year)
        print(
            f"⚠️ New Releases metadata entry not found for {song.artist} - {song.title} ({song_year}); nothing removed"
        )
    for song in ambiguous:
        print(
            f"⚠️ Multiple New Releases metadata entries matched {song.artist} - {song.title}; skipped removal"
        )

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
        # CHANGED: load_playlist now returns (songs, playlist_needs_save, deletions, df)
        songs, playlist_needs_save, deletions, playlist_df = load_playlist(
            playlist_file
        )
        playlist_entries.append(
            {
                "file": playlist_file,
                "name": playlist_file.stem,
                "songs": songs,
                "needs_save": playlist_needs_save,
                "deletions": deletions,
                "df": playlist_df,  # keep the full DataFrame for extra columns
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
        print(f"\n🛑 Songs marked for deletion via [DEL]: {len(deletion_targets)}")
        for key, song in deletion_targets.items():
            playlists_list = sorted(deletion_sources.get(key, []))
            playlists_note = ", ".join(playlists_list)
            print(f"   • {song.artist} - {song.title} (marked in: {playlists_note})")

        deleted_files = delete_marked_mp3_files(deletion_targets, STATION_PATH)
        if deleted_files:
            print(f"🗑️ Deleted {deleted_files} MP3 file(s) due to [DEL] markers")

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
        if entry["deletions"] and entry["name"].casefold() == "new releases":
            removed_metadata = remove_new_releases_metadata_entries(
                entry["file"].parent, entry["deletions"]
            )
            if removed_metadata:
                entry["metadata_removed"] = removed_metadata

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
        songs, normalized_changed, duplicates_removed = deduplicate_and_sort_songs(
            songs
        )

        if duplicates_removed > 0:
            print(f"Removed {duplicates_removed} duplicate(s) from {playlist_file}")

        # When updating playlist, update the DataFrame, not just the list of songs
        # For example, after deduplication, validation, or removal:
        # - Update the DataFrame rows for standard columns (artist, title, etc.)
        # - Keep all other columns unchanged

        # When saving:
        # save_playlist_with_validation should now take the DataFrame and write all columns
        if playlist_needs_save or library_changed or normalized_changed:
            save_playlist_with_validation(playlist_file, songs, entry["df"])

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

        # Handle forced YouTube overrides before standard download detection
        override_candidates = []
        for song in songs:
            if not song.override_url:
                continue

            safe_artist = (
                sanitize_filename_component(song.artist) if song.artist else ""
            )
            safe_title = sanitize_filename_component(song.title) if song.title else ""
            override_path = (
                music_dir / f"{safe_artist} - {safe_title}.mp3"
                if safe_artist and safe_title
                else None
            )
            override_candidates.append((song, override_path))

        override_updates = False

        for song, song_path in override_candidates:
            url = song.override_url

            if not song.artist or not song.title:
                print(f"⚠️ Override skipped; missing artist/title for URL {url}")
                continue

            if not url or not any(
                host in url.lower() for host in ("youtube.com", "youtu.be")
            ):
                print(f"⚠️ Override skipped; unsupported URL {url}")
                continue

            if song_path is None:
                print(
                    f"⚠️ Override skipped; could not determine target path for {song.artist} - {song.title}"
                )
                continue

            print(f"Forced YouTube override: {song.artist} - {song.title} (URL {url})")

            if dry_run:
                print(
                    f"   Would replace {song.artist} - {song.title} via forced override (dry-run)"
                )
                continue

            file_existed = song_path.exists()
            backup_path = None
            backup_created = False

            try:
                if file_existed:
                    backup_path = song_path.with_suffix(song_path.suffix + ".bak")
                    if backup_path.exists():
                        backup_path.unlink()
                    song_path.rename(backup_path)
                    backup_created = True

                youtube_to_mp3(url, str(song_path), use_search=False)
                tag_mp3(
                    str(song_path),
                    song.artist,
                    song.title,
                    song.year,
                    playlist_name,
                    song.album,
                )

                if backup_path and backup_path.exists():
                    backup_path.unlink()

                song.override_url = None
                override_updates = True

                replacement_note = (
                    "Replaced existing file"
                    if file_existed
                    else "Downloaded (new override)"
                )
                print(f"   {replacement_note}")

            except CalledProcessError as exc:
                print("   Override failed; original retained")
                print(f"     Reason: {exc}")

                if song_path.exists() and backup_created:
                    try:
                        song_path.unlink()
                    except Exception:
                        pass

                if backup_created and backup_path and backup_path.exists():
                    try:
                        backup_path.rename(song_path)
                    except Exception as restore_exc:
                        print(
                            f"     Warning: failed to restore original file from backup: {restore_exc}"
                        )

            except Exception as exc:
                print("   Override failed; original retained")
                print(f"     Reason: {exc}")

                if song_path.exists() and backup_created:
                    try:
                        song_path.unlink()
                    except Exception:
                        pass

                if backup_created and backup_path and backup_path.exists():
                    try:
                        backup_path.rename(song_path)
                    except Exception as restore_exc:
                        print(
                            f"     Warning: failed to restore original file from backup: {restore_exc}"
                        )

        if override_updates:
            save_playlist_with_validation(playlist_file, songs, entry["df"])

        # Check which songs already exist and which need to be downloaded
        existing_songs = []
        missing_songs = []

        pending_overrides = 0

        for song in songs:
            artist = song.artist
            title = song.title
            year = song.year

            # Create safe filename
            safe_artist = sanitize_filename_component(artist)
            safe_title = sanitize_filename_component(title)
            song_path = music_dir / f"{safe_artist} - {safe_title}.mp3"

            if song.override_url:
                pending_overrides += 1
                if song_path.exists():
                    existing_songs.append((song, song_path))
                continue

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
        if pending_overrides:
            print(f"   Pending overrides awaiting retry: {pending_overrides}")

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
                scope = "[DRY-RUN] " if dry_run else ""
                print(
                    f"\n🔍 {scope}Validating {len(unvalidated_existing)} unvalidated existing songs..."
                )
                valid_existing: List[Tuple[Song, pathlib.Path]] = []
                invalid_existing: List[Tuple[Song, pathlib.Path]] = []

                for song, song_path in unvalidated_existing:
                    result = perform_song_validation(
                        song, playlist_name, invalid_albums
                    )

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
            scope = "[DRY-RUN] " if dry_run else ""
            print(
                f"\n🔍 {scope}Validating {len(unvalidated_missing)} songs before download..."
            )

            newly_validated: List[Tuple[Song, pathlib.Path]] = []
            invalid_songs: List[Tuple[Song, pathlib.Path]] = []

            for song, song_path in unvalidated_missing:
                result = perform_song_validation(song, playlist_name, invalid_albums)

                if result.album_validated is True and result.album:
                    print(f"   ↳ Album validated: {result.album}")
                elif result.album_validated is False and result.album:
                    if result.album_reason == "validation_error":
                        print(f"   ↳ Album validation error (skipped): {result.album}")
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

            # Save updated playlist with validation status and all columns
            save_playlist_with_validation(str(playlist_file), songs, entry["df"])
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
                except CalledProcessError as e:
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
        songs, _, _, _ = load_playlist(playlist_file)
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
