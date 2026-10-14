"""Apply validated editorial choices through playlist-local media operations."""

from __future__ import annotations

from pathlib import Path

from mutagen.easyid3 import EasyID3

from neuralcast.playlists.catalog import StationPlaylistCatalog
from neuralcast.playlists.library import sanitize_filename_component

from .editorial import EditorialCatalogChanged, review_releases, track_key
from .logging import log_info, log_warning
from .matching import _normalize_audio_label
from .migration import move_outdated_releases
from .models import ArtistRelease


def retire_release(
    release: ArtistRelease,
    playlists_dir: Path,
    audio_root: Path | None,
    *,
    dry_run: bool,
) -> None:
    """Remove only an unambiguous canonical file from New Releases."""
    if audio_root is None:
        raise ValueError("Media root unavailable; cannot safely retire recording")
    source = audio_root / "New Releases"
    if source.is_symlink():
        raise ValueError("New Releases directory must not be a symlink")
    filename = (
        f"{sanitize_filename_component(release.artist)} - "
        f"{sanitize_filename_component(release.title)}.mp3"
    )
    songs = StationPlaylistCatalog(playlists_dir).load("New Releases").songs
    collisions = [
        song
        for song in songs
        if (
            f"{sanitize_filename_component(song.artist)} - "
            f"{sanitize_filename_component(song.title)}.mp3"
        ).casefold()
        == filename.casefold()
    ]
    if len(collisions) != 1:
        raise ValueError("Ambiguous or missing source catalog identity")
    target = source / filename
    if target.is_symlink():
        raise ValueError("Refusing to retire a symlinked recording")
    if source.exists():
        for candidate in source.iterdir():
            if candidate.suffix.lower() not in {".mp3", ".flac", ".wav"}:
                continue
            matches = _normalize_audio_label(candidate.stem) == _normalize_audio_label(
                release.artist, release.title
            )
            if candidate.suffix.lower() == ".mp3" and not candidate.is_symlink():
                try:
                    tags = EasyID3(candidate)
                    artist, title = (
                        tags.get("artist", [""])[0],
                        tags.get("title", [""])[0],
                    )
                except (
                    Exception
                ):  # Filename identity remains authoritative without tags.
                    artist = title = ""
                if artist and title:
                    tag_matches = [
                        artist.strip().casefold(),
                        title.strip().casefold(),
                    ] == [
                        release.artist.strip().casefold(),
                        release.title.strip().casefold(),
                    ]
                    if candidate == target and not tag_matches:
                        raise ValueError("Source recording tags disagree with catalog")
                    matches = matches or tag_matches
            if matches and candidate != target:
                raise ValueError("Noncanonical or additional source media needs review")
    log_info(f"{'Dry run: would retire' if dry_run else 'Retiring'} {target}")
    if not dry_run:
        target.unlink(missing_ok=True)


def process_outdated_releases(
    releases: list[ArtistRelease],
    artist_playlist_map: dict[str, dict[Path, set[str]]],
    audio_root: Path | None,
    new_releases_dir_name: str,
    *,
    playlists_dir: Path,
    dry_run: bool,
) -> list[ArtistRelease]:
    """Return pending releases, including the whole batch on review failure."""
    if not releases:
        return []
    try:
        decisions = review_releases(playlists_dir, releases, dry_run=dry_run)
    except EditorialCatalogChanged:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        log_warning(f"Editorial review deferred; keeping aging releases: {exc}")
        return releases
    pending = []
    for release in releases:
        decision = decisions[track_key(release)]
        try:
            if decision["action"] == "discard":
                retire_release(release, playlists_dir, audio_root, dry_run=dry_run)
            else:
                destination = playlists_dir / f"{decision['destination']}.csv"
                # Feed the approved destination through the existing migration
                # port, rather than introducing a second persistence mechanism.
                pending.extend(
                    move_outdated_releases(
                        [release],
                        {release.artist: {destination: {release.title}}},
                        audio_root,
                        new_releases_dir_name,
                        dry_run=dry_run,
                    )
                )
        except (OSError, ValueError, RuntimeError) as exc:
            log_warning(
                f"Editorial application pending for {release.artist} - {release.title}: {exc}"
            )
            pending.append(release)
    return pending
