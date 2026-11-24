"""Read-only indexer and mtime-cached loader for the Sonic Atlas & Station Vault."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from neuralcast.config import ALLOWED_STATION_SLUGS, station_dir_from_slug
from neuralcast.models import Song
from neuralcast.pipelines.episode_producer import publication as episode_publication
from neuralcast.pipelines.host_orchestrator.companion import (
    FILENAME as HOST_COMPANION_FILENAME,
)
from neuralcast.pipelines.schedule_generator.config import (
    OPEN_ROTATION_ONLY_PLAYLISTS,
    RESERVED_WINDOW_ONLY_PLAYLISTS,
    STATE_FILENAME as SCHEDULE_STATE_FILENAME,
)
from neuralcast.playlists.catalog import (
    StationPlaylistCatalog,
    normalize_year_value,
    playlist_song_key,
)

from .descriptions import (
    PlaylistEditorialCopy,
    load_playlist_descriptions,
    normalize_heading_key,
)
from .layout import compute_constellation_layout
from .models import (
    AtlasArtistEntry,
    AtlasArtistTrack,
    AtlasBridge,
    AtlasDecadeBucket,
    AtlasDecadeTopArtist,
    AtlasDecadeTopPlaylist,
    AtlasEpisodeRef,
    AtlasHostStory,
    AtlasPlaylistNode,
    AtlasSampleTrack,
    AtlasStats,
    AtlasTopArtist,
    PlaylistDetailBridge,
    PlaylistDetailDecade,
    PlaylistDetailEpisode,
    PlaylistDetailMatchedTrack,
    PlaylistDetailPayload,
    PlaylistDetailTrack,
    RotationRole,
    StationAtlasPayload,
)

STATION_META: dict[str, dict[str, str]] = {
    "neuralcast": {"name": "NeuralCast", "accent": "#1f8a78"},
    "neuralforge": {"name": "NeuralForge", "accent": "#b55748"},
}

SUPPORTED_STATIONS: tuple[str, ...] = tuple(ALLOWED_STATION_SLUGS)

_YEAR_PATTERN = re.compile(r"\b(1\d{3}|20\d{2}|2100)\b")
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_CACHE_LOCK = threading.Lock()


@dataclass
class _IndexedStation:
    signature: tuple[tuple[str, int, int], ...]
    atlas: StationAtlasPayload
    playlist_details: dict[str, PlaylistDetailPayload]


_ATLAS_CACHE: dict[tuple[str, str, str], _IndexedStation] = {}


def clear_atlas_cache() -> None:
    """Clear all cached station atlas payloads (useful for tests)."""
    with _CACHE_LOCK:
        _ATLAS_CACHE.clear()


def slugify(value: str, *, fallback_prefix: str = "item") -> str:
    """Create a URL-safe slug matching /^[a-z0-9][a-z0-9_-]{0,99}$/."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    ascii_text = ascii_text.replace("&", " and ").lower()
    slug = _NON_SLUG_CHARS.sub("-", ascii_text).strip("-")
    if not slug:
        digest = hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:10]
        slug = f"{fallback_prefix}-{digest}"
    return slug[:92].rstrip("-") or fallback_prefix


def parse_year_int(value: object) -> int | None:
    """Parse a valid 4-digit year integer from a CSV or metadata year value."""
    normalized = normalize_year_value(value)
    if not normalized or normalized.casefold() == "unknown":
        return None
    if normalized.isdigit():
        year = int(normalized)
        return year if 1000 <= year <= 2100 else None
    match = _YEAR_PATTERN.search(normalized)
    if match:
        year = int(match.group(1))
        return year if 1000 <= year <= 2100 else None
    return None


def normalize_text_key(value: str) -> str:
    """Casefold and collapse whitespace for stable matching."""
    return _WHITESPACE_PATTERN.sub(" ", (value or "").strip().casefold())


def normalize_fuzzy_key(value: str) -> str:
    """Accent- and punctuation-insensitive normalization for cross-source linking."""
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return _NON_SLUG_CHARS.sub(" ", stripped.casefold()).strip()


def _validate_station(station: str) -> str:
    slug = (station or "").strip().lower()
    if slug not in STATION_META:
        raise ValueError(f"Unknown station '{station}'.")
    return slug


def _resolve_roots(
    station: str,
    *,
    station_dir: pathlib.Path | None = None,
    catalogue_dir: pathlib.Path | None = None,
) -> tuple[str, pathlib.Path, pathlib.Path]:
    slug = _validate_station(station)
    resolved_station_dir = (
        pathlib.Path(station_dir)
        if station_dir is not None
        else station_dir_from_slug(slug)
    )
    resolved_catalogue_dir = (
        pathlib.Path(catalogue_dir)
        if catalogue_dir is not None
        else episode_publication.catalogue_root()
    )
    return slug, resolved_station_dir, resolved_catalogue_dir


def _collect_source_files(
    station: str,
    station_dir: pathlib.Path,
    catalogue_dir: pathlib.Path,
) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    playlists_dir = station_dir / "playlists"
    if playlists_dir.is_dir():
        paths.extend(sorted(playlists_dir.glob("*.csv")))

    descriptions_path = station_dir / "playlist_descriptions.md"
    if descriptions_path.is_file():
        paths.append(descriptions_path)

    metadata_dir = station_dir / "metadata"
    if metadata_dir.is_dir():
        schedule_path = metadata_dir / SCHEDULE_STATE_FILENAME
        if schedule_path.is_file():
            paths.append(schedule_path)
        root_companion = metadata_dir / HOST_COMPANION_FILENAME
        if root_companion.is_file():
            paths.append(root_companion)
        channels_dir = metadata_dir / "host_channels"
        if channels_dir.is_dir():
            paths.extend(sorted(channels_dir.glob(f"*/{HOST_COMPANION_FILENAME}")))

    station_catalogue = catalogue_dir / station
    if station_catalogue.is_dir():
        paths.extend(sorted(station_catalogue.glob("*/manifest.json")))

    return paths


def _compute_signature(paths: Sequence[pathlib.Path]) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    for path in sorted(set(paths), key=lambda p: str(p)):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _load_schedule_state_readonly(station_dir: pathlib.Path) -> Mapping[str, Any] | None:
    path = station_dir / "metadata" / SCHEDULE_STATE_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, Mapping) else None


def _classify_rotation_role(
    station: str,
    playlist_name: str,
    schedule_state: Mapping[str, Any] | None,
) -> RotationRole:
    normalized_name = normalize_heading_key(playlist_name)
    reserved_keys = {
        normalize_heading_key(name)
        for name in RESERVED_WINDOW_ONLY_PLAYLISTS.get(station, ())
    }
    if normalized_name in reserved_keys:
        return "reserved_window"

    open_only_keys = {
        normalize_heading_key(name)
        for name in OPEN_ROTATION_ONLY_PLAYLISTS.get(station, ())
    }
    if normalized_name in open_only_keys:
        return "open_only"
    # Tolerate minor singular/plural drift in schedule_generator config
    # (e.g. "Symphonic Classic" vs "Symphonic Classics").
    if normalized_name.endswith("s") and normalized_name[:-1] in open_only_keys:
        return "open_only"
    if f"{normalized_name}s" in open_only_keys:
        return "open_only"

    # If schedule_state explicitly annotates reserved_window/open_only roles, consult it;
    # otherwise themed is the default for scheduled playlists.
    if isinstance(schedule_state, Mapping):
        for block in schedule_state.get("expanded_blocks") or ():
            if not isinstance(block, Mapping):
                continue
            mode = str(block.get("mode") or block.get("kind") or "").strip().lower()
            names = [
                normalize_heading_key(n)
                for n in (block.get("playlist_names") or ())
                if isinstance(n, str)
            ]
            if normalized_name in names and mode == "reserved_window":
                return "reserved_window"

    return "themed"


def _load_published_episodes_readonly(
    station: str, catalogue_dir: pathlib.Path
) -> list[dict[str, Any]]:
    station_dir = catalogue_dir / station
    if not station_dir.is_dir():
        return []
    manifests: list[dict[str, Any]] = []
    for manifest_path in sorted(station_dir.glob("*/manifest.json")):
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        if raw.get("station") != station or not isinstance(raw.get("id"), str):
            continue
        manifests.append(raw)

    manifests.sort(
        key=lambda item: (str(item.get("published_at") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    return manifests


def _load_host_stories_readonly(
    station_dir: pathlib.Path,
) -> list[tuple[str, float, AtlasHostStory]]:
    """Read host_companion.json files gracefully, returning (artist, ts, story) tuples."""
    metadata_dir = station_dir / "metadata"
    if not metadata_dir.is_dir():
        return []

    candidate_paths: list[pathlib.Path] = []
    root_file = metadata_dir / HOST_COMPANION_FILENAME
    if root_file.is_file():
        candidate_paths.append(root_file)
    channels_dir = metadata_dir / "host_channels"
    if channels_dir.is_dir():
        candidate_paths.extend(sorted(channels_dir.glob(f"*/{HOST_COMPANION_FILENAME}")))

    collected: list[tuple[str, float, AtlasHostStory]] = []
    for path in candidate_paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        records = raw.get("records")
        if not isinstance(records, list):
            continue
        for item in records:
            if not isinstance(item, dict):
                continue
            archetype = item.get("archetype")
            content = item.get("content")
            if not isinstance(archetype, str) or not isinstance(content, dict):
                continue
            subject = content.get("subject")
            if not isinstance(subject, dict):
                continue
            artist = str(subject.get("artist") or "").strip()
            if not artist:
                continue
            title = str(content.get("title") or "").strip()
            summary = str(content.get("summary") or "").strip()
            if not title and not summary:
                continue
            track_title = str(subject.get("title") or "").strip() or None
            created_at: str | None = None
            ts_value = 0.0
            raw_start = item.get("expected_start")
            if isinstance(raw_start, (int, float)) and math.isfinite(float(raw_start)):
                ts_value = float(raw_start)
                try:
                    created_at = dt.datetime.fromtimestamp(
                        ts_value, tz=dt.timezone.utc
                    ).isoformat()
                except (OverflowError, OSError, ValueError):
                    created_at = None
            elif isinstance(item.get("created_at"), str):
                created_at = item["created_at"].strip() or None

            collected.append(
                (
                    artist,
                    ts_value,
                    AtlasHostStory(
                        archetype=archetype,
                        title=title or track_title or artist,
                        summary=summary,
                        created_at=created_at,
                        track_title=track_title,
                    ),
                )
            )

    collected.sort(
        key=lambda row: (row[1], row[2].title.casefold()),
        reverse=True,
    )
    return collected


def _unique_slug(base_name: str, used: set[str], *, fallback_prefix: str) -> str:
    base = slugify(base_name, fallback_prefix=fallback_prefix)
    candidate = base
    suffix = 2
    while candidate in used:
        suffix_text = f"-{suffix}"
        candidate = f"{base[: 99 - len(suffix_text)].rstrip('-')}{suffix_text}"
        suffix += 1
    used.add(candidate)
    return candidate


@dataclass
class _PlaylistTrackRecord:
    song: Song
    track_key: tuple[str, str]
    artist_key: str
    year: int | None
    decade: int | None


@dataclass
class _PlaylistRaw:
    slug: str
    name: str
    description: str
    placement_notes: str
    rotation_role: RotationRole
    tracks: list[_PlaylistTrackRecord]
    track_by_key: dict[tuple[str, str], _PlaylistTrackRecord]


def _build_indexed_station(
    station: str,
    station_dir: pathlib.Path,
    catalogue_dir: pathlib.Path,
    signature: tuple[tuple[str, int, int], ...],
) -> _IndexedStation:
    playlists_dir = station_dir / "playlists"
    catalog = StationPlaylistCatalog(playlists_dir, log=lambda _msg: None)
    descriptions: dict[str, PlaylistEditorialCopy] = load_playlist_descriptions(
        station_dir / "playlist_descriptions.md"
    )
    schedule_state = _load_schedule_state_readonly(station_dir)

    csv_paths = sorted(playlists_dir.glob("*.csv")) if playlists_dir.is_dir() else []
    used_playlist_slugs: set[str] = set()
    raw_playlists: list[_PlaylistRaw] = []

    # Track canonical artist display casing across the station.
    artist_display_counts: dict[str, Counter[str]] = defaultdict(Counter)
    artist_fuzzy_to_key: dict[str, str] = {}

    for csv_path in csv_paths:
        try:
            snapshot = catalog.load(csv_path)
        except Exception:  # noqa: BLE001
            continue

        playlist_name = snapshot.name
        playlist_slug = _unique_slug(
            playlist_name, used_playlist_slugs, fallback_prefix="playlist"
        )
        editorial = descriptions.get(normalize_heading_key(playlist_name))
        description = editorial.description if editorial else ""
        placement_notes = editorial.placement_notes if editorial else ""
        rotation_role = _classify_rotation_role(station, playlist_name, schedule_state)

        deduped_tracks: list[_PlaylistTrackRecord] = []
        track_by_key: dict[tuple[str, str], _PlaylistTrackRecord] = {}
        for song in snapshot.songs:
            key = playlist_song_key(song)
            if key in track_by_key:
                continue
            artist_key = key[0]
            year_int = parse_year_int(song.year)
            decade_int = (year_int // 10) * 10 if year_int is not None else None
            record = _PlaylistTrackRecord(
                song=song,
                track_key=key,
                artist_key=artist_key,
                year=year_int,
                decade=decade_int,
            )
            deduped_tracks.append(record)
            track_by_key[key] = record
            artist_display_counts[artist_key][song.artist.strip()] += 1
            fuzzy = normalize_fuzzy_key(song.artist)
            if fuzzy and fuzzy not in artist_fuzzy_to_key:
                artist_fuzzy_to_key[fuzzy] = artist_key

        deduped_tracks.sort(
            key=lambda item: (item.track_key[0], item.track_key[1])
        )
        raw_playlists.append(
            _PlaylistRaw(
                slug=playlist_slug,
                name=playlist_name,
                description=description,
                placement_notes=placement_notes,
                rotation_role=rotation_role,
                tracks=deduped_tracks,
                track_by_key=track_by_key,
            )
        )

    # Canonical artist names and slugs.
    canonical_artist_name: dict[str, str] = {}
    artist_slug_by_key: dict[str, str] = {}
    used_artist_slugs: set[str] = set()
    for artist_key in sorted(artist_display_counts):
        counts = artist_display_counts[artist_key]
        best_name = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        canonical_artist_name[artist_key] = best_name
        artist_slug_by_key[artist_key] = _unique_slug(
            best_name, used_artist_slugs, fallback_prefix="artist"
        )

    # Global track index: track_key -> representative metadata + playlist slugs.
    track_playlists: dict[tuple[str, str], list[str]] = defaultdict(list)
    track_representative: dict[tuple[str, str], _PlaylistTrackRecord] = {}
    fuzzy_track_to_key: dict[tuple[str, str], tuple[str, str]] = {}
    for playlist in raw_playlists:
        for item in playlist.tracks:
            track_playlists[item.track_key].append(playlist.slug)
            existing = track_representative.get(item.track_key)
            if existing is None or (
                (existing.year is None and item.year is not None)
                or (not existing.song.album and item.song.album)
            ):
                track_representative[item.track_key] = item
            fuzzy_key = (
                normalize_fuzzy_key(item.song.artist),
                normalize_fuzzy_key(item.song.title),
            )
            if fuzzy_key[0] and fuzzy_key[1] and fuzzy_key not in fuzzy_track_to_key:
                fuzzy_track_to_key[fuzzy_key] = item.track_key

    # Bridges between playlists.
    bridges: list[AtlasBridge] = []
    bridge_count_by_playlist: Counter[str] = Counter()
    playlist_bridges_map: dict[str, list[PlaylistDetailBridge]] = defaultdict(list)

    for idx_a in range(len(raw_playlists)):
        pl_a = raw_playlists[idx_a]
        keys_a = set(pl_a.track_by_key)
        for idx_b in range(idx_a + 1, len(raw_playlists)):
            pl_b = raw_playlists[idx_b]
            keys_b = set(pl_b.track_by_key)
            shared_keys = keys_a & keys_b
            if not shared_keys:
                continue
            union_size = len(keys_a | keys_b)
            jaccard = round(len(shared_keys) / union_size, 4) if union_size else 0.0
            ordered_shared = sorted(shared_keys)
            sample_tracks = [
                AtlasSampleTrack(
                    artist=canonical_artist_name.get(
                        track_representative[k].artist_key,
                        track_representative[k].song.artist,
                    ),
                    title=track_representative[k].song.title,
                    album=track_representative[k].song.album or "",
                    year=track_representative[k].year,
                )
                for k in ordered_shared[:5]
            ]
            source_slug, target_slug = (
                (pl_a.slug, pl_b.slug)
                if pl_a.slug <= pl_b.slug
                else (pl_b.slug, pl_a.slug)
            )
            bridges.append(
                AtlasBridge(
                    source=source_slug,
                    target=target_slug,
                    shared_count=len(shared_keys),
                    jaccard=jaccard,
                    sample_tracks=sample_tracks,
                )
            )
            bridge_count_by_playlist[pl_a.slug] += 1
            bridge_count_by_playlist[pl_b.slug] += 1
            playlist_bridges_map[pl_a.slug].append(
                PlaylistDetailBridge(
                    playlist_slug=pl_b.slug,
                    playlist_name=pl_b.name,
                    shared_count=len(shared_keys),
                    jaccard=jaccard,
                    sample_tracks=sample_tracks,
                )
            )
            playlist_bridges_map[pl_b.slug].append(
                PlaylistDetailBridge(
                    playlist_slug=pl_a.slug,
                    playlist_name=pl_a.name,
                    shared_count=len(shared_keys),
                    jaccard=jaccard,
                    sample_tracks=sample_tracks,
                )
            )

    bridges.sort(
        key=lambda b: (-b.shared_count, -b.jaccard, b.source, b.target)
    )
    for slug in playlist_bridges_map:
        playlist_bridges_map[slug].sort(
            key=lambda b: (-b.shared_count, -b.jaccard, b.playlist_slug)
        )

    # Published podcast episodes cross-linking.
    episode_manifests = _load_published_episodes_readonly(station, catalogue_dir)
    episodes_refs: list[AtlasEpisodeRef] = []
    playlist_episode_ids: dict[str, list[str]] = defaultdict(list)
    artist_episode_ids: dict[str, list[str]] = defaultdict(list)
    track_episode_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    playlist_detail_episodes: dict[str, list[PlaylistDetailEpisode]] = defaultdict(list)

    for manifest in episode_manifests:
        ep_id = str(manifest["id"])
        ep_title = str(manifest.get("title") or ep_id)
        ep_number_raw = manifest.get("episode_number")
        ep_number = int(ep_number_raw) if isinstance(ep_number_raw, int) else None
        ep_description = str(manifest.get("description") or "")
        ep_published_at = (
            str(manifest["published_at"])
            if isinstance(manifest.get("published_at"), str)
            else None
        )
        raw_duration = manifest.get("duration")
        ep_duration = (
            float(raw_duration)
            if isinstance(raw_duration, (int, float)) and math.isfinite(float(raw_duration))
            else None
        )

        matched_playlist_slugs: set[str] = set()
        matched_artist_keys: set[str] = set()
        matched_tracks_by_playlist: dict[str, list[PlaylistDetailMatchedTrack]] = (
            defaultdict(list)
        )
        seen_playlist_track: set[tuple[str, tuple[str, str]]] = set()

        for chapter in manifest.get("chapters") or ():
            if not isinstance(chapter, dict) or chapter.get("kind") != "music":
                continue
            ch_artist = str(chapter.get("artist") or "").strip()
            ch_title = str(chapter.get("title") or "").strip()
            if not ch_artist:
                continue

            exact_track_key = (ch_artist.lower(), ch_title.lower())
            resolved_track_key: tuple[str, str] | None = None
            if exact_track_key in track_playlists:
                resolved_track_key = exact_track_key
            elif ch_title:
                fuzzy_pair = (
                    normalize_fuzzy_key(ch_artist),
                    normalize_fuzzy_key(ch_title),
                )
                resolved_track_key = fuzzy_track_to_key.get(fuzzy_pair)

            if resolved_track_key is not None:
                if ep_id not in track_episode_ids[resolved_track_key]:
                    track_episode_ids[resolved_track_key].append(ep_id)
                rep = track_representative[resolved_track_key]
                matched_artist_keys.add(rep.artist_key)
                for pl_slug in track_playlists[resolved_track_key]:
                    matched_playlist_slugs.add(pl_slug)
                    dedupe_key = (pl_slug, resolved_track_key)
                    if dedupe_key not in seen_playlist_track:
                        seen_playlist_track.add(dedupe_key)
                        matched_tracks_by_playlist[pl_slug].append(
                            PlaylistDetailMatchedTrack(
                                artist=canonical_artist_name.get(
                                    rep.artist_key, rep.song.artist
                                ),
                                title=rep.song.title,
                            )
                        )
            else:
                # Fall back to artist-only link if the chapter's track isn't in CSVs.
                artist_key = ch_artist.lower()
                if artist_key not in canonical_artist_name:
                    artist_key = artist_fuzzy_to_key.get(
                        normalize_fuzzy_key(ch_artist), ""
                    )
                if artist_key:
                    matched_artist_keys.add(artist_key)

        sorted_pl_slugs = sorted(matched_playlist_slugs)
        sorted_artist_names = sorted(
            (canonical_artist_name[k] for k in matched_artist_keys),
            key=lambda name: name.casefold(),
        )
        for pl_slug in sorted_pl_slugs:
            playlist_episode_ids[pl_slug].append(ep_id)
            playlist_detail_episodes[pl_slug].append(
                PlaylistDetailEpisode(
                    id=ep_id,
                    station=station,
                    title=ep_title,
                    episode_number=ep_number,
                    description=ep_description,
                    published_at=ep_published_at,
                    duration=ep_duration,
                    matched_tracks=matched_tracks_by_playlist[pl_slug],
                )
            )
        for artist_key in matched_artist_keys:
            artist_episode_ids[artist_key].append(ep_id)

        episodes_refs.append(
            AtlasEpisodeRef(
                id=ep_id,
                station=station,
                title=ep_title,
                episode_number=ep_number,
                description=ep_description,
                published_at=ep_published_at,
                duration=ep_duration,
                playlist_slugs=sorted_pl_slugs,
                matched_artists=sorted_artist_names,
            )
        )

    # Host companion stories cross-linking.
    artist_stories: dict[str, list[AtlasHostStory]] = defaultdict(list)
    artist_story_seen: dict[str, set[tuple[str, str, str | None]]] = defaultdict(set)
    for raw_artist, _ts, story in _load_host_stories_readonly(station_dir):
        artist_key = raw_artist.lower().strip()
        if artist_key not in canonical_artist_name:
            artist_key = artist_fuzzy_to_key.get(normalize_fuzzy_key(raw_artist), "")
        if not artist_key:
            continue
        if len(artist_stories[artist_key]) >= 5:
            continue
        dedupe = (story.archetype, story.title.casefold(), story.track_title)
        if dedupe in artist_story_seen[artist_key]:
            continue
        artist_story_seen[artist_key].add(dedupe)
        artist_stories[artist_key].append(story)

    # Deterministic 2D constellation coordinates.
    layout_edges = [
        (b.source, b.target, float(b.shared_count) + b.jaccard * 10.0)
        for b in bridges
    ]
    node_weights = {pl.slug: float(len(pl.tracks)) for pl in raw_playlists}
    coords = compute_constellation_layout(
        station,
        [pl.slug for pl in raw_playlists],
        layout_edges,
        node_weights=node_weights,
    )

    # Assemble Playlist nodes and Decade aggregates.
    playlist_nodes: list[AtlasPlaylistNode] = []
    playlist_node_by_slug: dict[str, AtlasPlaylistNode] = {}

    decade_entry_counts: Counter[int] = Counter()
    decade_unique_tracks: dict[int, set[tuple[str, str]]] = defaultdict(set)
    decade_playlist_counts: dict[int, Counter[str]] = defaultdict(Counter)
    decade_artist_tracks: dict[int, dict[str, set[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(set)
    )
    playlist_name_by_slug = {pl.slug: pl.name for pl in raw_playlists}

    all_years: list[int] = []
    total_entries = 0

    for pl in raw_playlists:
        total_entries += len(pl.tracks)
        artist_counts: Counter[str] = Counter()
        pl_decades: Counter[int] = Counter()
        pl_years: list[int] = []

        for item in pl.tracks:
            artist_counts[item.artist_key] += 1
            if item.year is not None:
                pl_years.append(item.year)
                all_years.append(item.year)
            if item.decade is not None:
                pl_decades[item.decade] += 1
                decade_entry_counts[item.decade] += 1
                decade_unique_tracks[item.decade].add(item.track_key)
                decade_playlist_counts[item.decade][pl.slug] += 1
                decade_artist_tracks[item.decade][item.artist_key].add(item.track_key)

        top_artists = [
            AtlasTopArtist(
                name=canonical_artist_name[a_key],
                slug=artist_slug_by_key[a_key],
                track_count=count,
            )
            for a_key, count in sorted(
                artist_counts.items(),
                key=lambda pair: (-pair[1], canonical_artist_name[pair[0]].casefold()),
            )[:6]
        ]

        x, y = coords.get(pl.slug, (0.5, 0.5))
        node = AtlasPlaylistNode(
            slug=pl.slug,
            name=pl.name,
            description=pl.description,
            placement_notes=pl.placement_notes,
            rotation_role=pl.rotation_role,
            track_count=len(pl.tracks),
            unique_artist_count=len(artist_counts),
            min_year=min(pl_years) if pl_years else None,
            max_year=max(pl_years) if pl_years else None,
            decades={str(dec): pl_decades[dec] for dec in sorted(pl_decades)},
            top_artists=top_artists,
            episode_ids=playlist_episode_ids.get(pl.slug, []),
            bridge_count=bridge_count_by_playlist.get(pl.slug, 0),
            x=x,
            y=y,
        )
        playlist_nodes.append(node)
        playlist_node_by_slug[pl.slug] = node

    playlist_nodes.sort(key=lambda node: node.name.casefold())

    # Assemble station-wide Decade buckets.
    decades: list[AtlasDecadeBucket] = []
    for decade in sorted(decade_entry_counts):
        top_pls = [
            AtlasDecadeTopPlaylist(
                slug=pl_slug,
                name=playlist_name_by_slug[pl_slug],
                count=count,
            )
            for pl_slug, count in sorted(
                decade_playlist_counts[decade].items(),
                key=lambda pair: (-pair[1], playlist_name_by_slug[pair[0]].casefold()),
            )[:4]
        ]
        top_arts = [
            AtlasDecadeTopArtist(
                name=canonical_artist_name[a_key],
                slug=artist_slug_by_key[a_key],
                count=len(tracks_set),
            )
            for a_key, tracks_set in sorted(
                decade_artist_tracks[decade].items(),
                key=lambda pair: (
                    -len(pair[1]),
                    canonical_artist_name[pair[0]].casefold(),
                ),
            )[:5]
        ]
        decades.append(
            AtlasDecadeBucket(
                decade=decade,
                label=f"{decade}s",
                track_count=decade_entry_counts[decade],
                unique_track_count=len(decade_unique_tracks[decade]),
                top_playlists=top_pls,
                top_artists=top_arts,
            )
        )

    # Assemble Artist Vault entries.
    artist_tracks_map: dict[str, list[AtlasArtistTrack]] = defaultdict(list)
    artist_playlists_map: dict[str, set[str]] = defaultdict(set)
    artist_years_map: dict[str, list[int]] = defaultdict(list)

    for track_key, rep in sorted(track_representative.items()):
        pl_slugs = sorted(track_playlists[track_key])
        artist_tracks_map[rep.artist_key].append(
            AtlasArtistTrack(
                artist=canonical_artist_name[rep.artist_key],
                title=rep.song.title,
                album=rep.song.album or "",
                year=rep.year,
                playlists=pl_slugs,
            )
        )
        artist_playlists_map[rep.artist_key].update(pl_slugs)
        if rep.year is not None:
            artist_years_map[rep.artist_key].append(rep.year)

    artists: list[AtlasArtistEntry] = []
    for artist_key, display_name in canonical_artist_name.items():
        tracks_list = sorted(
            artist_tracks_map[artist_key],
            key=lambda t: (t.year if t.year is not None else 9999, t.title.casefold()),
        )
        years_list = artist_years_map.get(artist_key, [])
        artists.append(
            AtlasArtistEntry(
                slug=artist_slug_by_key[artist_key],
                name=display_name,
                track_count=len(tracks_list),
                min_year=min(years_list) if years_list else None,
                max_year=max(years_list) if years_list else None,
                playlists=sorted(artist_playlists_map[artist_key]),
                episode_ids=artist_episode_ids.get(artist_key, []),
                host_stories=artist_stories.get(artist_key, []),
                tracks=tracks_list,
            )
        )

    artists.sort(
        key=lambda a: (-a.track_count, -len(a.playlists), a.name.casefold(), a.slug)
    )

    stats = AtlasStats(
        playlist_count=len(playlist_nodes),
        total_entries=total_entries,
        unique_tracks=len(track_representative),
        unique_artists=len(artists),
        bridge_count=len(bridges),
        min_year=min(all_years) if all_years else None,
        max_year=max(all_years) if all_years else None,
        episode_count=len(episodes_refs),
    )

    meta = STATION_META[station]
    atlas = StationAtlasPayload(
        station=station,
        station_name=meta["name"],
        accent=meta["accent"],
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        stats=stats,
        playlists=playlist_nodes,
        bridges=bridges,
        decades=decades,
        episodes=episodes_refs,
        artists=artists,
    )

    # Pre-build PlaylistDetailPayload per playlist slug.
    playlist_details: dict[str, PlaylistDetailPayload] = {}
    for pl in raw_playlists:
        node = playlist_node_by_slug[pl.slug]
        detail_tracks = [
            PlaylistDetailTrack(
                artist=canonical_artist_name[item.artist_key],
                artist_slug=artist_slug_by_key[item.artist_key],
                title=item.song.title,
                album=item.song.album or "",
                year=item.year,
                decade=item.decade,
                other_playlists=[
                    other
                    for other in sorted(track_playlists[item.track_key])
                    if other != pl.slug
                ],
                episode_ids=list(track_episode_ids.get(item.track_key, ())),
            )
            for item in pl.tracks
        ]
        detail_decades = [
            PlaylistDetailDecade(
                decade=int(dec_str),
                label=f"{dec_str}s",
                track_count=count,
            )
            for dec_str, count in sorted(
                node.decades.items(), key=lambda pair: int(pair[0])
            )
        ]
        playlist_details[pl.slug] = PlaylistDetailPayload(
            station=station,
            playlist=node,
            tracks=detail_tracks,
            bridges=playlist_bridges_map.get(pl.slug, []),
            decades=detail_decades,
            episodes=playlist_detail_episodes.get(pl.slug, []),
        )

    return _IndexedStation(
        signature=signature,
        atlas=atlas,
        playlist_details=playlist_details,
    )


def _get_or_build_station(
    station: str,
    *,
    station_dir: pathlib.Path | None = None,
    catalogue_dir: pathlib.Path | None = None,
) -> _IndexedStation:
    slug, resolved_station_dir, resolved_catalogue_dir = _resolve_roots(
        station, station_dir=station_dir, catalogue_dir=catalogue_dir
    )
    source_files = _collect_source_files(
        slug, resolved_station_dir, resolved_catalogue_dir
    )
    signature = _compute_signature(source_files)
    cache_key = (slug, str(resolved_station_dir), str(resolved_catalogue_dir))

    with _CACHE_LOCK:
        cached = _ATLAS_CACHE.get(cache_key)
        if cached is not None and cached.signature == signature:
            return cached

    rebuilt = _build_indexed_station(
        slug, resolved_station_dir, resolved_catalogue_dir, signature
    )
    with _CACHE_LOCK:
        _ATLAS_CACHE[cache_key] = rebuilt
    return rebuilt


def get_station_atlas(
    station: str,
    *,
    station_dir: pathlib.Path | None = None,
    catalogue_dir: pathlib.Path | None = None,
) -> StationAtlasPayload:
    """Return the cached (or freshly built) StationAtlasPayload for a station."""
    return _get_or_build_station(
        station, station_dir=station_dir, catalogue_dir=catalogue_dir
    ).atlas


def get_playlist_detail(
    station: str,
    slug: str,
    *,
    station_dir: pathlib.Path | None = None,
    catalogue_dir: pathlib.Path | None = None,
) -> PlaylistDetailPayload:
    """Return the cached PlaylistDetailPayload for a station playlist slug."""
    indexed = _get_or_build_station(
        station, station_dir=station_dir, catalogue_dir=catalogue_dir
    )
    detail = indexed.playlist_details.get(slug)
    if detail is None:
        raise KeyError(f"Unknown playlist '{slug}' for station '{indexed.atlas.station}'.")
    return detail
