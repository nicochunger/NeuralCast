"""Local asset, metadata, and cleanup helpers for host orchestrator."""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import re
import subprocess
from typing import Any, Dict, Mapping

from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TLAN, TPE1

from .config import (
    AI_SNIPPET_COVER_PATH_BY_STATION,
    HOST_ARTIST_NAME,
    LOGGER,
    STORY_OUTPUT_DIR,
)
from .models import Archetype, QueueTrack, StoryAssets, TrackMetadata
from .schedule import resolve_station_metadata_file
from .transport import AzuraCastClient
from .utils import normalize_component, track_key
from neuralcast.audio.album_art import embed_local_cover_art
from neuralcast.services.ai_client import (
    DEFAULT_GEMINI_TTS_MODEL,
    synthesize_speech,
)

try:
    from neuralcast.playlists.utils import sanitize_filename_component
except Exception:  # pragma: no cover - lightweight fallback for environments without pandas

    def sanitize_filename_component(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[<>:\"/\\\\|?*]", "", text)
        text = re.sub(r"\s+", " ", text)
        text = text.strip(". ").strip()
        return text or "unknown"


def load_station_track_metadata(station_dir: pathlib.Path) -> Dict[str, TrackMetadata]:
    metadata: Dict[str, TrackMetadata] = {}
    playlists_dir = station_dir / "playlists"

    if playlists_dir.exists():
        for csv_path in sorted(playlists_dir.glob("*.csv")):
            genre = csv_path.stem
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        artist = str(row.get("Artist") or "").strip()
                        title = str(row.get("Title") or "").strip()
                        if not artist or not title:
                            continue

                        key = track_key(artist, title)
                        item = metadata.setdefault(key, TrackMetadata())
                        year = str(row.get("Year") or "").strip()
                        album = str(row.get("Album") or "").strip()
                        if year and not item.year:
                            item.year = year
                        if album and not item.album:
                            item.album = album
                        if genre and not item.genre:
                            item.genre = genre
            except OSError:
                continue

    # Optional station metadata cache for New Releases.
    metadata_entries_path = resolve_station_metadata_file(
        station_dir, "New Releases.metadata.json"
    )
    if metadata_entries_path.exists():
        try:
            payload = json.loads(metadata_entries_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}

        entries = payload.get("entries") if isinstance(payload, Mapping) else None
        if not isinstance(entries, Mapping):
            entries = payload if isinstance(payload, Mapping) else {}

        for key, details in entries.items():
            if not isinstance(key, str):
                continue
            parts = key.split("|")
            if len(parts) < 2:
                continue
            normalized_key = (
                f"{normalize_component(parts[0])}|{normalize_component(parts[1])}"
            )
            item = metadata.setdefault(normalized_key, TrackMetadata())

            if len(parts) >= 3 and parts[2] and not item.album:
                item.album = parts[2]
            if len(parts) >= 4 and parts[3] and not item.year:
                item.year = parts[3]

            if isinstance(details, Mapping):
                notes: list[str] = []
                album_type = str(details.get("AlbumType") or "").strip()
                if album_type:
                    notes.append(f"album_type={album_type}")
                popularity = details.get("Popularity")
                if popularity not in (None, ""):
                    notes.append(f"popularity={popularity}")
                release_date = str(details.get("ReleaseDate") or "").strip()
                if release_date:
                    notes.append(f"release_date={release_date}")
                if notes and not item.notes:
                    item.notes = ", ".join(notes)

    return metadata


def apply_replaygain(audio_path: pathlib.Path) -> None:
    LOGGER.info("[audio] Applying ReplayGain: %s", audio_path.name)
    try:
        subprocess.run(
            ["mp3gain", "-q", "-r", "-k", str(audio_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        LOGGER.warning(
            "[audio] mp3gain not available (%s); continuing without ReplayGain.",
            exc,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        LOGGER.warning("[audio] ReplayGain failed: %s", detail)
    except OSError as exc:  # pragma: no cover - unexpected OS-level failure
        LOGGER.warning("[audio] ReplayGain skipped due to OS error: %s", exc)


def tag_story_audio(
    audio_path: pathlib.Path, title: str, language: str | None = None
) -> None:
    """Write the listener-facing identity while preserving embedded artwork."""
    try:
        tags = ID3(audio_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TPE1")
    tags.delall("TIT2")
    tags.delall("TLAN")
    tags.add(TPE1(encoding=3, text=[HOST_ARTIST_NAME]))
    tags.add(TIT2(encoding=3, text=[title]))
    if language:
        tags.add(TLAN(encoding=3, text=[language]))
    tags.save(audio_path, v2_version=3)


def ensure_story_assets(
    station_slug: str,
    current_track: QueueTrack,
    archetype: Archetype,
    script_text: str,
    tts_instructions: str,
    segment_title: str,
    *,
    channel_key: str | None = None,
    cover_station: str | None = None,
    remote_prefix: str = "AI Stories",
    tts_voice: str = "Enceladus",
    language: str | None = None,
) -> StoryAssets:
    safe_artist = sanitize_filename_component(current_track.artist).replace("'", "")
    safe_title = sanitize_filename_component(current_track.title).replace("'", "")
    timestamp = dt.datetime.now()
    date_str = timestamp.strftime("%Y-%m-%d")
    output_scope = channel_key or station_slug
    station_dir = STORY_OUTPUT_DIR / output_scope
    target_dir = station_dir / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"AIHost_{archetype.value}_{safe_artist}_{safe_title}_{timestamp.strftime('%H%M%S')}"
    text_path = target_dir / f"{base_name}.txt"
    audio_path = target_dir / f"{base_name}.mp3"

    text_path.write_text(script_text.strip() + "\n", encoding="utf-8")

    synthesize_speech(
        text=script_text,
        outfile=str(audio_path),
        instructions=tts_instructions,
        gemini_model=DEFAULT_GEMINI_TTS_MODEL,
        gemini_voice=tts_voice,
    )

    apply_replaygain(audio_path)

    cover_path = AI_SNIPPET_COVER_PATH_BY_STATION.get(
        (cover_station or station_slug).casefold()
    )
    if cover_path is not None:
        embed_local_cover_art(audio_path, cover_path)
    tag_story_audio(audio_path, segment_title, language=language)

    return StoryAssets(
        text_path=text_path,
        audio_path=audio_path,
        story_text=script_text,
        remote_path="/".join(
            [remote_prefix.strip("/"), date_str, f"{base_name}.mp3"]
        ),
    )


def cleanup_local_stories(station_slug: str, keep_days: int) -> None:
    if keep_days <= 0:
        return

    base_dir = STORY_OUTPUT_DIR / station_slug
    if not base_dir.exists():
        return

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    for file_path in base_dir.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in {".mp3", ".txt"}:
            continue
        try:
            mtime = dt.datetime.fromtimestamp(
                file_path.stat().st_mtime, tz=dt.timezone.utc
            )
        except OSError:
            continue
        if mtime < cutoff:
            file_path.unlink(missing_ok=True)

    # Prune empty dated folders left behind after file cleanup.
    subdirs = [
        path
        for path in base_dir.rglob("*")
        if path.is_dir()
    ]
    for dir_path in sorted(subdirs, key=lambda path: len(path.parts), reverse=True):
        try:
            dir_path.rmdir()
        except OSError:
            continue


def cleanup_remote_stories(
    client: AzuraCastClient,
    station_slug: str,
    keep_days: int,
    remote_prefix: str = "AI Stories",
) -> None:
    if keep_days <= 0:
        return

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    cutoff_ts = cutoff.timestamp()
    try:
        media_files = client.list_media_files(station_slug)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("[cleanup] Unable to list remote media files: %s", exc)
        return

    normalized_prefix = remote_prefix.strip("/") + "/"
    for entry in media_files:
        path = str(entry.get("path") or "")
        if not path.startswith(normalized_prefix):
            continue
        mtime = entry.get("mtime")
        media_id = entry.get("id") or entry.get("media_id")
        if mtime is None or media_id is None:
            continue
        try:
            if float(mtime) >= cutoff_ts:
                continue
            client.delete_media_file(station_slug, int(media_id))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "[cleanup] Failed deleting remote story file '%s' (media_id=%s): %s",
                path,
                media_id,
                exc,
            )
