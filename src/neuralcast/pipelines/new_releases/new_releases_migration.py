"""Promotion and audio migration for aged New Releases tracks."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mutagen.easyid3 import EasyID3

from neuralcast.audio.download import tag_mp3
from neuralcast.metadata.album_lookup import guess_album
from neuralcast.metadata.track_resolution import (
    CallableAlbumResolutionPort,
    CallableTrackValidationPort,
    ResolutionMode,
    TrackMetadataResolver,
    TrackResolutionRequest,
)
from neuralcast.models import Song
from neuralcast.playlists.catalog import CatalogWritePolicy, StationPlaylistCatalog
from neuralcast.playlists.utils import sanitize_filename_component
from neuralcast.services.validation import verified, verified_album

from .models import ArtistRelease
from .new_releases_logging import log_debug, log_error, log_info, log_warning
from .new_releases_matching import _normalize_audio_label, _track_titles_match


def _resolve_destination_playlist(
    release: ArtistRelease, artist_playlist_map: dict[str, dict[Path, set[str]]]
) -> Optional[Path]:
    candidates = artist_playlist_map.get(release.artist)
    if not candidates:
        return None
    title_key = release.title.casefold()
    for path, titles in candidates.items():
        if any((title or "").casefold() == title_key for title in titles):
            return path
    return sorted(candidates.keys())[0]


def _append_release_to_playlist(
    csv_path: Path, release: ArtistRelease, dry_run: bool
) -> None:
    action = (
        f"Dry run: would append '{release.artist} - {release.title}' to {csv_path.name}"
        if dry_run
        else f"Appending '{release.artist} - {release.title}' to {csv_path.name}"
    )
    log_info(action)
    try:
        appended = StationPlaylistCatalog(
            csv_path.parent,
            log=log_debug,
        ).append(
            csv_path,
            Song(
                artist=release.artist,
                title=release.title,
                year=str(release.year),
                album=release.album or None,
                validated=False,
            ),
            policy=(
                CatalogWritePolicy.PREVIEW
                if dry_run
                else CatalogWritePolicy.PERSIST
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log_error(f"Failed reading {csv_path}: {exc}")
        return
    if not appended:
        log_debug(
            f"Track already present in {csv_path.name}: "
            f"{release.artist} - {release.title}"
        )
    elif not dry_run:
        log_debug(f"Appended '{release.title}' to {csv_path.name}")


def _promote_release_album(release: ArtistRelease) -> bool:
    """Update release.album to the studio album when a confident match exists."""

    current_album = (release.album or "").strip()
    current_type = (release.album_type or "").strip().casefold()
    # Some providers label a pre-album single as an "album" whose title is the
    # track title.  Treat that as provisional metadata so aging a release also
    # gives a now-published studio album a chance to replace it.
    album_is_track_title = _track_titles_match(current_album, release.title)
    should_attempt = (
        release.is_single
        or not current_album
        or current_type != "album"
        or album_is_track_title
    )
    if not should_attempt:
        return False

    resolver = TrackMetadataResolver(
        validator=CallableTrackValidationPort(
            track_exists_func=verified,
            album_matches_func=verified_album,
        ),
        album_resolver=CallableAlbumResolutionPort(guess_album_func=guess_album),
    )
    resolution = resolver.resolve(
        TrackResolutionRequest(
            song=Song(
                artist=release.artist,
                title=release.title,
                album=release.album,
                year=str(release.year),
                validated=release.validated,
            ),
            mode=ResolutionMode.PROMOTE_RELEASE_ALBUM,
            prefer_spotify=False,
            prefer_deezer=True,
            min_confidence=0.55,
            allow_fallback=True,
        )
    )
    for note in resolution.notes:
        if note.startswith("album_lookup_failed:"):
            detail = note.removeprefix("album_lookup_failed:")
            log_warning(
                f"Album lookup failed for {release.artist} - {release.title}: {detail}"
            )

    if not resolution.album_promoted or not resolution.song:
        return False

    new_album = (resolution.song.album or "").strip()
    if not new_album:
        return False

    normalized_current = current_album.casefold()
    normalized_new = new_album.casefold()
    album_changed = normalized_current != normalized_new
    type_changed = current_type != "album"

    if not album_changed and not type_changed and not release.is_single:
        return False

    previous_label = current_album or "single"
    release.album = new_album
    release.album_type = resolution.album_type or "album"
    release.is_single = False
    if resolution.release_date:
        release.year = resolution.release_date.year
    if resolution.track_id:
        release.track_id = resolution.track_id

    log_info(
        f"Updated album metadata for {release.artist} - {release.title}: {previous_label} -> {new_album}"
    )
    return True


def _move_track_audio(
    audio_root: Optional[Path],
    source_dir_name: str,
    destination_dir_name: str,
    release: ArtistRelease,
    dry_run: bool,
    refresh_album_art: bool = False,
) -> bool:
    if not audio_root:
        return True
    src_dir = audio_root / source_dir_name
    if not src_dir.exists():
        log_debug(f"Audio source directory missing: {src_dir}")
        return True
    dest_dir = audio_root / destination_dir_name
    target_key = _normalize_audio_label(release.artist, release.title)
    for candidate in src_dir.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in {".mp3", ".flac", ".wav"}:
            continue
        candidate_key = _normalize_audio_label(candidate.stem)
        if candidate_key != target_key and candidate.suffix.lower() == ".mp3":
            try:
                tags = EasyID3(str(candidate))
                tagged_artist = tags.get("artist", [""])[0]
                tagged_title = tags.get("title", [""])[0]
                candidate_key = _normalize_audio_label(tagged_artist, tagged_title)
            except Exception:  # noqa: BLE001 - filename matching remains available
                pass
        if candidate_key == target_key:
            canonical_name = (
                f"{sanitize_filename_component(release.artist)} - "
                f"{sanitize_filename_component(release.title)}{candidate.suffix.lower()}"
            )
            collision = next(
                (
                    path
                    for path in dest_dir.iterdir()
                    if path.is_file() and path.name.casefold() == canonical_name.casefold()
                ),
                None,
            ) if dest_dir.exists() else None
            if collision is not None:
                log_warning(
                    "Audio move blocked because the permanent playlist already has "
                    f"a case-insensitive filename match: {collision.name}. "
                    "The release will remain in New Releases for review."
                )
                return False
            dest_path = dest_dir / canonical_name
            if dry_run:
                log_info(
                    f"Dry run: would move {candidate.name} to "
                    f"{dest_dir / canonical_name}"
                )
                return True
            dest_dir.mkdir(parents=True, exist_ok=True)
            log_info(
                f"Moving audio for '{release.artist} - {release.title}' "
                f"from {src_dir} to {dest_dir}"
            )
            candidate.replace(dest_path)
            log_info(f"Moved {candidate.name} to {dest_path}")
            if dest_path.suffix.lower() == ".mp3":
                try:
                    tag_mp3(
                        str(dest_path),
                        release.artist,
                        release.title,
                        str(release.year),
                        destination_dir_name,
                        release.album,
                        log_prefix="      ",
                        refresh_art=refresh_album_art,
                        apply_replaygain=False,
                    )
                except Exception as exc:
                    log_warning(
                        "Moved audio but could not refresh destination metadata for "
                        f"{release.artist} - {release.title}: {exc}"
                    )
            else:
                log_warning(
                    f"Moved {dest_path.name}, but automatic destination tagging "
                    "currently supports MP3 files only"
                )
            return True
    log_warning(
        f"No audio found for {release.artist} - {release.title} in {src_dir}; nothing moved"
    )
    return True


def move_outdated_releases(
    releases: list[ArtistRelease],
    artist_playlist_map: dict[str, dict[Path, set[str]]],
    audio_root: Optional[Path],
    new_releases_dir_name: str,
    dry_run: bool,
) -> list[ArtistRelease]:
    if not releases:
        return []
    migrations: list[tuple[ArtistRelease, Path]] = []
    blocked_releases: list[ArtistRelease] = []
    for release in releases:
        destination = _resolve_destination_playlist(release, artist_playlist_map)
        if not destination:
            log_warning(f"No destination playlist for {release.artist} - {release.title}")
            blocked_releases.append(release)
            continue
        album_promoted = _promote_release_album(release)
        moved = _move_track_audio(
            audio_root,
            new_releases_dir_name,
            destination.stem,
            release,
            dry_run=dry_run,
            refresh_album_art=album_promoted,
        )
        if not moved:
            blocked_releases.append(release)
            continue
        migrations.append((release, destination))
        _append_release_to_playlist(destination, release, dry_run=dry_run)
    if not migrations:
        return blocked_releases
    action_phrase = (
        "Dry run: would move the following tracks to permanent playlists"
        if dry_run
        else "Moved the following tracks to permanent playlists"
    )
    log_info(action_phrase)
    for release, destination in migrations:
        log_info(f"  • {release.artist} – {release.title} → {destination.name}")
    return blocked_releases


__all__ = ["move_outdated_releases"]

