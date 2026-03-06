#!/usr/bin/env python3
"""Normalize oversized embedded MP3 cover art for a station."""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError


def _ensure_project_root() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    root_str = str(root)
    src_str = str(src)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


_ensure_project_root()

from neuralcast.audio import album_art
from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG, station_dir_from_slug


@dataclass
class FileResult:
    path: Path
    has_apic: bool
    apic_count: int = 0
    changed: bool = False
    too_big: bool = False
    before_bytes: int = 0
    after_bytes: int = 0
    note: str = ""
    error: str = ""


def _pick_cover_apic(apics: list[APIC]) -> APIC:
    for frame in apics:
        if int(getattr(frame, "type", -1)) == 3:
            return frame
    return apics[0]


def _is_too_big(apic: APIC, *, max_px: int, max_bytes: int) -> bool:
    if len(apic.data) > max_bytes:
        return True
    image_cls = album_art.Image
    if image_cls is None:
        return False
    try:
        with image_cls.open(BytesIO(apic.data)) as image:
            return max(image.size) > max_px
    except Exception:
        return False


def _normalize_file(
    mp3_path: Path,
    *,
    max_px: int,
    max_bytes: int,
    apply: bool,
) -> FileResult:
    try:
        tags = ID3(str(mp3_path))
    except ID3NoHeaderError:
        return FileResult(path=mp3_path, has_apic=False, note="no-id3")
    except Exception as exc:
        return FileResult(path=mp3_path, has_apic=False, error=f"id3-read-error: {exc}")

    apics = list(tags.getall("APIC"))
    if not apics:
        return FileResult(path=mp3_path, has_apic=False, note="no-apic")

    selected = _pick_cover_apic(apics)
    too_big = _is_too_big(selected, max_px=max_px, max_bytes=max_bytes)
    has_multiple_apics = len(apics) > 1

    normalized_data = selected.data
    normalized_mime = selected.mime or "image/jpeg"
    if too_big:
        with contextlib.redirect_stdout(io.StringIO()):
            normalized_data, normalized_mime = album_art._normalize_cover_art(
                selected.data,
                selected.mime or "image/jpeg",
                max_px=max_px,
                max_bytes=max_bytes,
            )

    needs_update = has_multiple_apics or (
        too_big and (normalized_data != selected.data or normalized_mime != selected.mime)
    )
    notes: list[str] = []
    if has_multiple_apics:
        notes.append("multiple-apic")
    if too_big:
        notes.append("oversized")
    if too_big and len(normalized_data) > max_bytes:
        notes.append("still-over-max-bytes")
    result = FileResult(
        path=mp3_path,
        has_apic=True,
        apic_count=len(apics),
        changed=needs_update,
        too_big=too_big,
        before_bytes=len(selected.data),
        after_bytes=len(normalized_data),
        note=",".join(notes) if notes else "ok",
    )

    if not needs_update or not apply:
        return result

    try:
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime=normalized_mime or "image/jpeg",
                type=3,
                desc="Cover",
                data=normalized_data,
            )
        )
        tags.save(str(mp3_path))
        return result
    except Exception as exc:
        result.error = f"id3-write-error: {exc}"
        result.changed = False
        return result


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{value}B"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan station MP3 files, normalize oversized embedded cover images, "
            "and enforce a single APIC frame."
        )
    )
    parser.add_argument(
        "-s",
        "--station",
        choices=ALLOWED_STATION_SLUGS,
        default=DEFAULT_STATION_SLUG,
        help="Station slug (default: %(default)s).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write fixes in-place. Default is dry-run/report-only.",
    )
    parser.add_argument(
        "--max-px",
        type=int,
        default=1200,
        help="Maximum allowed cover-art edge size in pixels (default: %(default)s).",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=1_000_000,
        help="Maximum allowed cover-art payload size in bytes (default: %(default)s).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N MP3 files (default: no limit).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    station_dir = station_dir_from_slug(args.station)
    songs_root = station_dir / "songs"
    if not songs_root.exists():
        print(f"❌ Songs directory not found: {songs_root}")
        return 1
    if not songs_root.is_dir():
        print(f"❌ Songs path is not a directory: {songs_root}")
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[cover-art] mode={mode} station={args.station} "
        f"max_px={args.max_px} max_bytes={args.max_bytes}"
    )
    print(f"[cover-art] scanning {songs_root}")

    mp3_files = sorted(songs_root.rglob("*.mp3"))
    if args.limit and args.limit > 0:
        mp3_files = mp3_files[: args.limit]
    total = len(mp3_files)
    print(f"[cover-art] found {total} mp3 file(s)")

    with_apic = 0
    oversized = 0
    changed = 0
    no_apic = 0
    errors = 0
    total_bytes_saved = 0

    for idx, path in enumerate(mp3_files, start=1):
        result = _normalize_file(
            path,
            max_px=args.max_px,
            max_bytes=args.max_bytes,
            apply=args.apply,
        )

        if result.error:
            errors += 1
            rel = path.relative_to(songs_root)
            print(f"⚠️  [{idx}/{total}] {rel} | {result.error}")
            continue

        if not result.has_apic:
            no_apic += 1
            continue

        with_apic += 1
        if result.too_big:
            oversized += 1

        if result.changed:
            changed += 1
            bytes_saved = max(result.before_bytes - result.after_bytes, 0)
            total_bytes_saved += bytes_saved
            rel = path.relative_to(songs_root)
            action = "FIXED" if args.apply else "WOULD_FIX"
            print(
                f"🎨 [{idx}/{total}] {action} {rel} | apic={result.apic_count}->1 "
                f"| {_format_bytes(result.before_bytes)} -> {_format_bytes(result.after_bytes)} "
                f"| note={result.note}"
            )

        if idx % 250 == 0:
            print(f"[cover-art] progress {idx}/{total}")

    unchanged = with_apic - changed
    print("\n[cover-art] summary")
    print(f"  scanned: {total}")
    print(f"  with_apic: {with_apic}")
    print(f"  without_apic_or_id3: {no_apic}")
    print(f"  oversized_detected: {oversized}")
    print(f"  {'fixed' if args.apply else 'would_fix'}: {changed}")
    print(f"  unchanged: {unchanged}")
    print(f"  errors: {errors}")
    print(f"  estimated_bytes_saved: {_format_bytes(total_bytes_saved)}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
