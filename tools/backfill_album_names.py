#!/usr/bin/env python3
"""Fill missing album names in playlist CSV files using album_lookup."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Sequence


def _ensure_project_root() -> None:
    root = Path(__file__).resolve().parents[1]
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


_ensure_project_root()

from album_lookup import guess_album


def _normalize(value: str | None) -> str:
    return value.strip() if value else ""


def _resolve_column(fieldnames: Sequence[str] | None, target: str) -> str:
    if not fieldnames:
        raise ValueError("Playlist is missing headers")
    for name in fieldnames:
        if name.lower() == target.lower():
            return name
    return target


def backfill_playlist(
    playlist_path: Path,
    *,
    dry_run: bool = False,
    prefer_spotify: bool = True,
    min_confidence: float = 0.5,
) -> tuple[int, list[tuple[str, str]]]:
    with playlist_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    artist_col = _resolve_column(fieldnames, "Artist")
    title_col = _resolve_column(fieldnames, "Title")
    album_col = _resolve_column(fieldnames, "Album")
    if album_col not in fieldnames:
        insertion_index = (
            max(
                [
                    fieldnames.index(col)
                    for col in (artist_col, title_col)
                    if col in fieldnames
                ]
                or [-1]
            )
            + 1
        )
        fieldnames.insert(insertion_index, album_col)

    updates = 0
    failures: list[tuple[str, str]] = []

    for row in rows:
        artist = _normalize(row.get(artist_col))
        title = _normalize(row.get(title_col))
        if not artist or not title:
            continue

        existing_album = _normalize(row.get(album_col))
        if existing_album:
            continue

        match = guess_album(
            artist,
            title,
            prefer_spotify=prefer_spotify,
            min_confidence=min_confidence,
            allow_fallback=True,
        )

        if match:
            row[album_col] = match.album
            updates += 1
            print(
                f"Filled album for '{artist} - {title}': {match.album} "
                f"(source={match.source}, confidence={match.confidence:.2f})"
            )
        else:
            failures.append((artist, title))
            print(f"⚠️  No album match for '{artist} - {title}'")

    if updates and not dry_run:
        with playlist_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)

    return updates, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill missing album fields in playlist CSV files."
    )
    parser.add_argument(
        "playlists",
        nargs="+",
        type=Path,
        help="One or more playlist CSV paths to update.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform lookups without writing the playlists.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimum confidence required to accept an album guess.",
    )
    parser.add_argument(
        "--prefer-musicbrainz",
        action="store_true",
        help="Prefer MusicBrainz over Spotify.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prefer_spotify = not args.prefer_musicbrainz

    total_updates = 0
    total_failures: list[tuple[str, str]] = []

    for playlist in args.playlists:
        print(f"Processing {playlist} ...")
        updates, failures = backfill_playlist(
            playlist,
            dry_run=args.dry_run,
            prefer_spotify=prefer_spotify,
            min_confidence=args.min_confidence,
        )
        total_updates += updates
        total_failures.extend(failures)
        print(
            f"Done with {playlist}: wrote {updates} album name(s)"
            f"{' (dry run)' if args.dry_run else ''}."
        )

    print(
        f"Completed {len(args.playlists)} playlist(s): "
        f"{total_updates} album(s) filled, "
        f"{len(total_failures)} remaining without matches."
    )
    if total_failures:
        print("Unmatched tracks:")
        for artist, title in total_failures:
            print(f" - {artist} — {title}")


if __name__ == "__main__":
    main()
