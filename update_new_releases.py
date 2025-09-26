"""Update station's New Releases playlist with latest tracks via Spotify."""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from difflib import SequenceMatcher

import pandas as pd
from dotenv import load_dotenv
from spotipy import Spotify, SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials
import logging
from tqdm import tqdm
import re


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    # handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


@dataclass
class ArtistRelease:
    artist: str
    title: str
    year: int
    album: str
    release_date: datetime
    track_id: str
    # New optional metadata used for ranking/filters
    popularity: Optional[int] = None
    is_single: bool = False
    album_type: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"ArtistRelease(\n"
            f"    artist={self.artist!r},\n"
            f"    title={self.title!r},\n"
            f"    year={self.year},\n"
            f"    album={self.album!r},\n"
            f"    release_date={self.release_date!r},\n"
            f"    track_id={self.track_id!r}\n"
            f")"
        )


def load_station_artists(playlists_dir: Path) -> list[str]:
    artists: set[str] = set()
    logger.debug(f"Scanning playlists directory: {playlists_dir}")
    for csv_path in playlists_dir.glob("*.csv"):
        logger.debug(f"Checking file: {csv_path}")
        if csv_path.name.lower() == "new releases.csv":
            logger.debug(f"Skipping New Releases file: {csv_path}")
            continue
        try:
            df = pd.read_csv(csv_path)
            logger.debug(f"Loaded CSV: {csv_path} with columns: {df.columns.tolist()}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed reading {csv_path}: {exc}")
            continue
        if "Artist" not in df.columns:
            logger.debug(f"No 'Artist' column in {csv_path}, skipping file.")
            continue
        for value in df["Artist"].dropna():
            name = str(value).strip()
            logger.debug(f"Found artist value: '{name}' in file: {csv_path}")
            if name:
                artists.add(name)
    logger.debug(f"Found {len(artists)} unique artists: {sorted(artists)}")
    return sorted(artists)


def parse_release_date(date_str: str, precision: str) -> Optional[datetime]:
    logger.debug(f"Parsing release date '{date_str}' with precision '{precision}'")
    if not date_str:
        logger.debug("No date string provided, returning None")
        return None
    try:
        match precision:
            case "day":
                dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            case "month":
                dt = datetime.strptime(date_str, "%Y-%m").replace(day=1, tzinfo=UTC)
            case "year":
                dt = datetime.strptime(date_str, "%Y").replace(month=1, day=1, tzinfo=UTC)
            case _:
                logger.debug(f"Unknown precision '{precision}', returning None")
                return None
        logger.debug(f"Parsed date: {dt}")
        return dt
    except ValueError as e:
        logger.debug(f"ValueError parsing date '{date_str}' with precision '{precision}': {e}")
        return None


def _best_artist_match(sp: Spotify, artist_name: str) -> Optional[dict]:
    query = f'artist:"{artist_name}"'
    logger.debug(f"Searching for artist: {artist_name} with query: {query}")
    try:
        res = sp.search(q=query, type="artist", limit=5)
    except SpotifyException as exc:
        if exc.http_status == 429:
            retry_after = int(exc.headers.get("Retry-After", "5"))
            logger.warning(f"Rate limited during artist search, sleeping {retry_after}s")
            time.sleep(retry_after)
            return _best_artist_match(sp, artist_name)
        logger.error(f"Spotify search failed for {artist_name}: {exc}")
        return None

    items = res.get("artists", {}).get("items", [])
    logger.debug(f"Artist search returned {len(items)} items for {artist_name}: {[item.get('name') for item in items]}")
    if not items:
        logger.debug(f"No items returned for artist search: {artist_name}")
        return None

    lower_target = artist_name.lower()
    for item in items:
        logger.debug(f"Checking for exact match: '{item.get('name', '').lower()}' == '{lower_target}'")
        if item.get("name", "").lower() == lower_target:
            logger.debug(f"Exact match found for artist: {artist_name}")
            return item

    best_item: Optional[dict] = None
    best_score = 0.0
    for item in items:
        candidate = item.get("name", "")
        score = _similarity(candidate, artist_name)
        logger.debug(f"Similarity score for '{candidate}' vs '{artist_name}': {score}")
        if score > best_score:
            best_item = item
            best_score = score
    if best_score >= 0.65:
        logger.debug(f"Best fuzzy match for artist '{artist_name}': '{best_item.get('name', '')}' ({best_score:.2f})")
    else:
        logger.debug(f"No suitable match found for artist: {artist_name}")
    return best_item if best_score >= 0.65 else None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


# Robust filters for non-new variants
_TITLE_EXCLUDE_PATTERNS = [
    re.compile(r"\blive\b", re.I),
    re.compile(r"\bremaster(?:ed|s|ing)?\b", re.I),
    re.compile(r"\bre[-\s]?record(?:ed|ing)?\b", re.I),  # re-recorded, rerecorded
    re.compile(r"\bre[-\s]?imagined\b", re.I),
    re.compile(r"\bredux\b", re.I),
    re.compile(r"\bacoustic\b", re.I),
    re.compile(r"\bdemo\b", re.I),
    re.compile(r"\bradio\s+edit\b", re.I),
    re.compile(r"\bedit\b", re.I),
]
_ALBUM_EXCLUDE_PATTERNS = _TITLE_EXCLUDE_PATTERNS + [
    re.compile(r"\banniversary\b", re.I),
    re.compile(r"\bdeluxe\b", re.I),
    re.compile(r"\bexpanded\b", re.I),
    re.compile(r"\breissue\b", re.I),
    re.compile(r"\bmono\b", re.I),
    re.compile(r"\bstereo\b", re.I),
    re.compile(r"\bgreatest\s+hits\b", re.I),
    re.compile(r"\bbest\s+of\b", re.I),
    re.compile(r"\bcollection\b", re.I),
    re.compile(r"\banthology\b", re.I),
]


def _is_alt_or_reissue(title: str, album_name: str) -> bool:
    """Return True if the track likely represents a non-new version (live, remaster, re-recorded, deluxe, anniversary, etc.)."""
    t = title or ""
    a = album_name or ""
    for pat in _TITLE_EXCLUDE_PATTERNS:
        if pat.search(t):
            return True
    for pat in _ALBUM_EXCLUDE_PATTERNS:
        if pat.search(a):
            return True
    return False


def _album_tracks_by_artist(sp: Spotify, album_id: str, artist_id: str) -> list[dict]:
    tracks: list[dict] = []
    offset = 0
    logger.debug(f"Fetching tracks for album {album_id} and artist {artist_id}")
    while True:
        page = sp.album_tracks(album_id, limit=50, offset=offset)
        items = page.get("items", [])
        logger.debug(f"Fetched {len(items)} tracks at offset {offset} for album {album_id}")
        if not items:
            logger.debug(f"No more tracks found for album {album_id} at offset {offset}")
            break
        for track in items:
            artists = track.get("artists", [])
            logger.debug(f"Track '{track.get('name', '')}' artists: {[art.get('id') for art in artists]}")
            if any(art.get("id") == artist_id for art in artists if art.get("id")):
                logger.debug(f"Track '{track.get('name', '')}' matches artist {artist_id}")
                tracks.append(track)
        if not page.get("next"):
            logger.debug(f"No next page for album tracks in album {album_id}")
            break
        offset += len(items)
    logger.debug(f"Total tracks found for artist {artist_id} in album {album_id}: {len(tracks)}")
    return tracks


def _iter_recent_albums(sp: Spotify, artist_id: str, cutoff: datetime) -> Iterable[tuple[datetime, dict]]:
    offset = 0
    seen_albums: set[str] = set()
    logger.debug(f"Fetching recent albums for artist {artist_id} after {cutoff}")
    while True:
        try:
            page = sp.artist_albums(
                artist_id,
                include_groups="album,single",
                limit=50,
                offset=offset,
                country="US",
            )
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                logger.warning(f"Rate limited during albums fetch, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            logger.error(f"Spotify albums failed for {artist_id}: {exc}")
            break
        items = page.get("items", [])
        logger.debug(f"Fetched {len(items)} albums at offset {offset} for artist {artist_id}")
        if not items:
            logger.debug(f"No more albums found for artist {artist_id} at offset {offset}")
            break
        for album in items:
            album_id = album.get("id")
            logger.debug(f"Processing album: {album_id}")
            if not album_id or album_id in seen_albums:
                logger.debug(f"Skipping album {album_id}: already seen or missing ID")
                continue
            seen_albums.add(album_id)
            release = parse_release_date(album.get("release_date", ""), album.get("release_date_precision", ""))
            logger.debug(f"Album {album_id} release date: {release}")
            if not release or release < cutoff:
                logger.debug(f"Skipping album {album_id}: release date {release} before cutoff {cutoff}")
                continue
            logger.debug(f"Yielding album {album_id} released on {release}")
            yield release, album
        if not page.get("next"):
            logger.debug(f"No next page for artist albums for artist {artist_id}")
            break
        offset += len(items)


def _fetch_popularity_bulk(sp: Spotify, track_ids: list[str]) -> dict[str, int]:
    """Fetch popularity for up to len(track_ids) tracks in batches."""
    result: dict[str, int] = {}
    i = 0
    while i < len(track_ids):
        chunk = track_ids[i : i + 50]
        try:
            resp = sp.tracks(chunk)
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                logger.warning(f"Rate limited during tracks fetch, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            logger.error(f"Spotify tracks batch failed: {exc}")
            break
        for t in (resp.get("tracks") or []):
            if not t:
                continue
            tid = t.get("id")
            if tid:
                result[tid] = int(t.get("popularity") or 0)
        i += len(chunk)
    return result


def _annotate_popularity(sp: Spotify, releases: list[ArtistRelease]) -> None:
    """Mutates releases to fill popularity using batch lookup."""
    ids = [r.track_id for r in releases if r.track_id]
    if not ids:
        return
    pops = _fetch_popularity_bulk(sp, ids)
    for r in releases:
        r.popularity = pops.get(r.track_id, 0)


def fetch_recent_releases(sp: Spotify, artist_name: str, cutoff: datetime) -> list[ArtistRelease]:
    """Return all recent release candidates for an artist (filtered), not just one."""
    logger.debug(f"Fetching recent releases for artist: {artist_name}")
    artist = _best_artist_match(sp, artist_name)
    if not artist:
        logger.debug(f"No artist found for {artist_name}")
        return []

    artist_id = artist.get("id")
    logger.debug(f"Artist ID for '{artist_name}': {artist_id}")
    if not artist_id:
        logger.debug(f"No artist ID found for {artist_name}")
        return []

    candidates: list[ArtistRelease] = []
    for release_date, album in _iter_recent_albums(sp, artist_id, cutoff):
        album_id = album.get("id")
        if not album_id:
            continue
        tracks = _album_tracks_by_artist(sp, album_id, artist_id)
        if not tracks:
            continue

        # Choose the first track in album order to represent the release (skip "Live")
        tracks.sort(key=lambda t: (t.get("disc_number", 0), t.get("track_number", 0)))
        track = tracks[0]
        title = track.get("name", "").strip()
        album_name = album.get("name", "").strip()
        # Skip non-new variants by robust patterns
        if _is_alt_or_reissue(title, album_name):
            continue
        if not title or not album_name:
            continue
        track_id = track.get("id")
        if not track_id:
            continue

        album_type = (album.get("album_group") or album.get("album_type") or "").lower()
        is_single = album_type == "single"

        candidates.append(
            ArtistRelease(
                artist=artist_name,
                title=title,
                year=release_date.year,
                album=album_name,
                release_date=release_date,
                track_id=track_id,
                album_type=album_type,
                is_single=is_single,
            )
        )

    # Sort newest first (popularity annotation/ranking happens later)
    candidates.sort(key=lambda item: item.release_date, reverse=True)
    if not candidates:
        logger.debug(f"No recent releases found for {artist_name}")
    else:
        logger.debug(f"Found {len(candidates)} candidates for {artist_name}")
    return candidates


def build_new_releases(
    sp: Spotify,
    artists: Iterable[str],
    days: int,
    per_artist: int = 1,
    min_popularity: int = 0,
    prefer_singles: bool = False,
) -> list[ArtistRelease]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    releases: list[ArtistRelease] = []
    seen_tracks: set[str] = set()
    artists_list = list(artists)
    logger.debug(f"Building new releases for {len(artists_list)} artists with cutoff {cutoff}")

    for idx, artist in enumerate(
        tqdm(artists_list, desc="Artists", unit="artist", disable=not sys.stdout.isatty()),
        start=1,
    ):
        candidates = fetch_recent_releases(sp, artist, cutoff)
        if not candidates:
            continue

        # Fetch popularity for this artist's candidates
        _annotate_popularity(sp, candidates)

        # Filter by popularity threshold
        filtered = [c for c in candidates if (c.popularity or 0) >= min_popularity]
        if not filtered:
            logger.debug(f"No candidates passed min_popularity for {artist}")
            continue

        # Rank: optionally prefer singles, then by popularity, then by recency
        def rank_key(r: ArtistRelease):
            single_score = 1 if (prefer_singles and r.is_single) else 0
            return (single_score, r.popularity or 0, r.release_date)

        filtered.sort(key=rank_key, reverse=True)

        kept = 0
        for cand in filtered:
            if cand.track_id in seen_tracks:
                continue
            seen_tracks.add(cand.track_id)
            releases.append(cand)
            kept += 1
            if kept >= per_artist:
                break

    # Final ordering: newest first, tiebreaker by popularity
    releases.sort(key=lambda item: (item.release_date, item.popularity or 0), reverse=True)
    logger.debug(f"Total new releases collected: {len(releases)}")
    logger.debug(f"Release list: {[f'{r.artist} - {r.title} (pop {r.popularity}, {r.release_date}, single={r.is_single})' for r in releases]}")
    return releases


def save_new_releases(playlists_dir: Path, releases: list[ArtistRelease], dry_run: bool) -> None:
    output_path = playlists_dir / "New Releases.csv"
    logger.debug(f"Saving new releases to {output_path}, dry_run={dry_run}")
    if not releases:
        logger.info("No new releases to write.")
        print("No new releases to write.", file=sys.stderr)
        return
    df = pd.DataFrame(
        [
            {
                "Artist": item.artist,
                "Title": item.title,
                "Year": str(item.year),
                "Album": item.album,
                "Validated": False,
            }
            for item in releases
        ]
    ).sort_values(by="Artist")
    logger.debug(f"DataFrame to be written:\n{df}")
    if dry_run:
        logger.info("Dry run: not writing CSV")
        print("Dry run: not writing CSV", file=sys.stderr)
        print(df.to_string(index=False), flush=True)
        return
    df.to_csv(output_path, index=False)
    logger.info(f"Wrote {len(df)} tracks to {output_path}")
    print(f"Wrote {len(df)} tracks to {output_path}", flush=True)


def build_spotify_client() -> Spotify:
    logger.debug("Loading Spotify credentials from .env")
    load_dotenv(dotenv_path=".env", override=False)
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    logger.debug(f"SPOTIFY_CLIENT_ID: {cid}, SPOTIFY_CLIENT_SECRET: {'set' if secret else 'missing'}")
    if not cid or not secret:
        logger.error("Spotify credentials missing. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
        raise SystemExit("Spotify credentials missing. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
    auth = SpotifyClientCredentials(client_id=cid, client_secret=secret)
    logger.debug("Spotify client initialized")
    return Spotify(auth_manager=auth)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the New Releases playlist for a station.")
    parser.add_argument(
        "station",
        metavar="STATION",
        help="Station directory name (e.g., NeuralForge or NeuralCast)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=120,
        help="Lookback window in days for releases (default: 120)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and display results without writing the CSV",
    )
    # New knobs for multi-track selection and ranking
    parser.add_argument(
        "--per-artist",
        type=int,
        default=3,
        help="Max tracks to keep per artist (default: 3)",
    )
    parser.add_argument(
        "--min-popularity",
        type=int,
        default=0,
        help="Minimum Spotify popularity (0-100) to keep (default: 0)",
    )
    parser.add_argument(
        "--prefer-singles",
        action="store_true",
        help="Prefer singles when ranking candidates (default: off)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info(f"Starting update for station: {args.station}")
    station_dir = Path(args.station)
    playlists_dir = station_dir / "playlists"
    logger.debug(f"Station directory: {station_dir}, Playlists directory: {playlists_dir}")
    if not playlists_dir.exists():
        logger.error(f"Playlists directory not found: {playlists_dir}")
        raise SystemExit(f"Playlists directory not found: {playlists_dir}")

    artists = load_station_artists(playlists_dir)
    logger.info(f"Loaded {len(artists)} artists from {playlists_dir}")

    sp = build_spotify_client()
    releases = build_new_releases(
        sp,
        artists,
        days=args.days,
        per_artist=args.per_artist,
        min_popularity=args.min_popularity,
        prefer_singles=args.prefer_singles,
    )

    if releases:
        logger.info(f"Collected {len(releases)} recent tracks")
        print(f"Collected {len(releases)} recent tracks", flush=True)
    else:
        logger.info("No releases found within the window")
        print("No releases found within the window", flush=True)
    save_new_releases(playlists_dir, releases, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
