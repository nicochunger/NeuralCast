"""Runtime interface for refreshing a station's New Releases playlist."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

legacy = importlib.import_module("neuralcast.pipelines.new_releases.main")


@dataclass(frozen=True)
class NewReleasesRequest:
    station: str
    days: int = 120
    per_artist: int = 3
    min_rank: int = 0
    prefer_singles: bool = False
    dry_run: bool = False
    verbose: bool = False


@dataclass(frozen=True)
class NewReleasesResult:
    station: str
    playlists_dir: Path
    final_releases: list[legacy.ArtistRelease]
    new_releases: list[legacy.ArtistRelease]
    valid_existing: list[legacy.ArtistRelease]
    outdated_existing: list[legacy.ArtistRelease]
    artist_count: int
    dry_run: bool

    @property
    def final_count(self) -> int:
        return len(self.final_releases)


@dataclass(frozen=True)
class NewReleasesRuntimeDependencies:
    station_paths: Callable[[str], tuple[Path, Path]]
    load_station_artists: Callable[
        [Path], tuple[list[str], dict[str, set[str]], dict[str, dict[Path, set[str]]]]
    ]
    load_artist_id_cache: Callable[[Path], legacy.ArtistIDCache]
    load_existing_new_releases: Callable[[Path], list[legacy.ArtistRelease]]
    build_new_releases: Callable[..., list[legacy.ArtistRelease]]
    save_artist_id_cache: Callable[[Path, legacy.ArtistIDCache], None]
    move_outdated_releases: Callable[..., list[legacy.ArtistRelease] | None]
    save_new_releases: Callable[[Path, list[legacy.ArtistRelease]], None]
    now: Callable[[], datetime]
    set_debug_mode: Callable[[bool], None]
    log_debug: Callable[[str], None]
    log_info: Callable[[str], None]


def default_station_paths(station: str) -> tuple[Path, Path]:
    return legacy._resolve_station_paths(station)


def default_dependencies(request: NewReleasesRequest) -> NewReleasesRuntimeDependencies:
    return NewReleasesRuntimeDependencies(
        station_paths=default_station_paths,
        load_station_artists=legacy.load_station_artists,
        load_artist_id_cache=legacy.load_artist_id_cache,
        load_existing_new_releases=legacy.load_existing_new_releases,
        build_new_releases=legacy.build_new_releases,
        save_artist_id_cache=lambda playlists_dir, cache: legacy.save_artist_id_cache(
            playlists_dir, cache, dry_run=request.dry_run
        ),
        move_outdated_releases=lambda releases, artist_playlist_map, audio_root, name: legacy.move_outdated_releases(
            releases,
            artist_playlist_map,
            audio_root,
            name,
            dry_run=request.dry_run,
        ),
        save_new_releases=lambda playlists_dir, releases: legacy.save_new_releases(
            playlists_dir, releases, dry_run=request.dry_run
        ),
        now=lambda: datetime.now(UTC),
        set_debug_mode=legacy.set_debug_mode,
        log_debug=legacy.log_debug,
        log_info=legacy.log_info,
    )


class NewReleasesRuntime:
    def __init__(
        self,
        dependencies_factory: Callable[
            [NewReleasesRequest], NewReleasesRuntimeDependencies
        ] = default_dependencies,
    ) -> None:
        self._dependencies_factory = dependencies_factory

    def run(self, request: NewReleasesRequest) -> NewReleasesResult:
        deps = self._dependencies_factory(request)
        deps.set_debug_mode(request.verbose)
        station_dir, playlists_dir = deps.station_paths(request.station)
        if not playlists_dir.exists():
            raise SystemExit(f"Playlists directory not found: {playlists_dir}")

        artists, artist_tracks, artist_playlist_map = deps.load_station_artists(
            playlists_dir
        )
        artist_cache = deps.load_artist_id_cache(playlists_dir)
        audio_root = station_dir / "songs"
        if not audio_root.exists():
            deps.log_debug(f"Audio root not found; skipping audio moves: {audio_root}")
            audio_root = None

        cutoff = deps.now() - timedelta(days=request.days)
        existing_releases = deps.load_existing_new_releases(playlists_dir)
        valid_existing, outdated_existing = legacy.partition_releases_by_cutoff(
            existing_releases, cutoff
        )
        existing_ids = {release.track_id for release in valid_existing if release.track_id}
        existing_keys = {
            legacy._normalize_audio_label(release.artist, release.title)
            for release in valid_existing
        }
        existing_artist_counts: dict[str, int] = {}
        for release in valid_existing:
            artist_key = legacy._normalize_text(release.artist)
            existing_artist_counts[artist_key] = (
                existing_artist_counts.get(artist_key, 0) + 1
            )

        new_releases = deps.build_new_releases(
            artists,
            days=request.days,
            per_artist=request.per_artist,
            min_rank=request.min_rank,
            prefer_singles=request.prefer_singles,
            known_tracks=artist_tracks,
            artist_cache=artist_cache,
            cutoff=cutoff,
            seen_tracks=existing_ids,
            seen_keys=existing_keys,
            existing_artist_counts=existing_artist_counts,
        )
        deps.save_artist_id_cache(playlists_dir, artist_cache)

        blocked_releases: list[legacy.ArtistRelease] = []
        if outdated_existing:
            blocked_releases = deps.move_outdated_releases(
                outdated_existing,
                artist_playlist_map,
                audio_root,
                "New Releases",
            ) or []

        final_releases = _dedupe_releases(
            valid_existing + new_releases + blocked_releases
        )

        if final_releases:
            deps.log_info(f"Collected {len(final_releases)} recent tracks")
            print(f"Collected {len(final_releases)} recent tracks", flush=True)
        else:
            deps.log_info("No releases found within the window")
            print("No releases found within the window", flush=True)
        deps.save_new_releases(playlists_dir, final_releases)

        return NewReleasesResult(
            station=request.station,
            playlists_dir=playlists_dir,
            final_releases=final_releases,
            new_releases=new_releases,
            valid_existing=valid_existing,
            outdated_existing=outdated_existing,
            artist_count=len(artists),
            dry_run=request.dry_run,
        )


def _dedupe_releases(
    releases: list[legacy.ArtistRelease],
) -> list[legacy.ArtistRelease]:
    combined = sorted(
        releases,
        key=lambda item: (item.release_date, item.rank or 0),
        reverse=True,
    )
    final_releases: list[legacy.ArtistRelease] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for release in combined:
        title_key = legacy._normalize_audio_label(release.artist, release.title)
        if (release.track_id and release.track_id in seen_ids) or title_key in seen_keys:
            continue
        final_releases.append(release)
        seen_keys.add(title_key)
        if release.track_id:
            seen_ids.add(release.track_id)
    return final_releases


__all__ = [
    "NewReleasesRequest",
    "NewReleasesResult",
    "NewReleasesRuntime",
    "NewReleasesRuntimeDependencies",
    "default_dependencies",
    "default_station_paths",
]
