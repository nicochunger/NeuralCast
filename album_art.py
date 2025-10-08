import datetime
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

import musicbrainzngs
import requests
from IPython.display import Image as IPyImage, display
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, ID3NoHeaderError

# Set up musicbrainzngs library
musicbrainzngs.set_useragent("NeuralCastArtEmbedder", "1.0", "https://github.com/your-repo")

LOG_FILE = os.path.join(os.path.dirname(__file__), "logs/album_art_skipped.log")

MAX_RELEASE_GROUPS = 6
MAX_RELEASES_PER_GROUP = 6
MAX_TOTAL_CANDIDATES = 14

_RELEASE_CACHE: dict[str, dict] = {}


def _log_skip(entry: dict):
    try:
        # Ensure directory exists (in case LOG_FILE points to a subdir)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"-> Failed to write skip log: {e}")


def _parse_release_date(date_str: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        try:
            return datetime.datetime.strptime(date_str, "%Y-%m")
        except ValueError:
            try:
                return datetime.datetime.strptime(date_str, "%Y")
            except ValueError:
                return datetime.datetime.max


def find_best_release_from_releases(releases):
    """
    Finds the best release from a list of releases (result of search_releases).
    Prioritizes the earliest, official album release.
    """
    candidate_releases = []
    for release in releases:
        if (
            release.get("status") == "Official"
            and release.get("release-group", {}).get("primary-type") == "Album"
            and "date" in release
        ):
            candidate_releases.append(release)

    if not candidate_releases:
        return None

    candidate_releases.sort(key=lambda r: _parse_release_date(r.get("date", "")))
    return candidate_releases[0]


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _normalize_string(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_only = ascii_only.lower()
    ascii_only = re.sub(r"[^a-z0-9]+", " ", ascii_only)
    return " ".join(ascii_only.split())


def _string_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _release_primary_date(release: dict) -> str | None:
    if release.get("date"):
        return release["date"]
    for event in release.get("release-event-list", []):
        if isinstance(event, dict) and event.get("date"):
            return event["date"]
    return None


def _release_primary_country(release: dict) -> str | None:
    if release.get("country"):
        return release["country"]
    for event in release.get("release-event-list", []):
        if not isinstance(event, dict):
            continue
        area = event.get("area")
        if isinstance(area, dict):
            if area.get("name"):
                return area["name"]
            if area.get("iso-3166-1-code-list"):
                codes = area["iso-3166-1-code-list"]
                if isinstance(codes, list) and codes:
                    return codes[0]
    return None


def _release_matches_artist(release: dict, normalized_artist: str) -> bool:
    if not normalized_artist:
        return True
    phrase = _normalize_string(release.get("artist-credit-phrase", ""))
    if phrase and (normalized_artist in phrase or phrase in normalized_artist):
        return True
    if phrase and _string_similarity(normalized_artist, phrase) >= 0.82:
        return True

    for credit in release.get("artist-credit", []):
        if isinstance(credit, dict):
            name = _normalize_string(credit.get("name", ""))
            if not name:
                continue
            if (
                normalized_artist in name
                or name in normalized_artist
                or _string_similarity(normalized_artist, name) >= 0.82
            ):
                return True
    return False


def _score_release_group(
    release_group: dict, normalized_artist: str, normalized_album: str
) -> float:
    base_score = float(release_group.get("score", 0) or 0)
    title_sim = _string_similarity(
        normalized_album, _normalize_string(release_group.get("title", ""))
    )
    artist_phrase = _normalize_string(release_group.get("artist-credit-phrase", ""))
    artist_sim = _string_similarity(normalized_artist, artist_phrase)
    if artist_sim < 0.8:
        for credit in release_group.get("artist-credit", []):
            if isinstance(credit, dict):
                artist_sim = max(
                    artist_sim,
                    _string_similarity(
                        normalized_artist, _normalize_string(credit.get("name", ""))
                    ),
                )

    base_score += title_sim * 35
    base_score += artist_sim * 25

    primary_type = release_group.get("primary-type")
    if primary_type == "Album":
        base_score += 8
    elif primary_type:
        base_score -= 4

    secondary_types = release_group.get("secondary-type-list", [])
    if isinstance(secondary_types, list):
        if any(
            st in {"Compilation", "Interview", "Audiobook", "Spokenword", "DJ-mix"}
            for st in secondary_types
        ):
            base_score -= 12

    disambig = _normalize_string(release_group.get("disambiguation", ""))
    if disambig and any(
        keyword in disambig for keyword in ("tribute", "karaoke", "cover", "instrumental", "demo")
    ):
        base_score -= 10

    return base_score


def _score_release(release: dict, normalized_artist: str, normalized_album: str) -> float:
    score = 0.0

    status = release.get("status") or ""
    if status == "Official":
        score += 25
    elif status in {"Promotion", "Bootleg"}:
        score -= 10

    title_sim = _string_similarity(normalized_album, _normalize_string(release.get("title", "")))
    score += title_sim * 40

    if _release_matches_artist(release, normalized_artist):
        score += 25
    else:
        phrase = _normalize_string(release.get("artist-credit-phrase", ""))
        score += _string_similarity(normalized_artist, phrase) * 15

    cover_info = release.get("cover-art-archive") or {}
    if _coerce_bool(cover_info.get("front")):
        score += 15
    elif _coerce_bool(cover_info.get("artwork")):
        score += 8
    elif cover_info:
        score -= 5
    else:
        score -= 10

    disambig = _normalize_string(release.get("disambiguation", ""))
    if disambig and any(
        keyword in disambig for keyword in ("karaoke", "tribute", "backing track")
    ):
        score -= 18
    elif disambig and any(
        keyword in disambig for keyword in ("demo", "remix", "instrumental", "live")
    ):
        score -= 6

    packaging = _normalize_string(release.get("packaging", ""))
    if packaging and "promo" in packaging:
        score -= 5

    return score


def _score_release_search_entry(
    release: dict, normalized_artist: str, normalized_album: str
) -> float:
    base = float(release.get("score", 0) or 0)
    base += _string_similarity(normalized_album, _normalize_string(release.get("title", ""))) * 30
    phrase = _normalize_string(release.get("artist-credit-phrase", ""))
    base += _string_similarity(normalized_artist, phrase) * 20

    release_group = release.get("release-group", {}) or {}
    if release_group.get("primary-type") == "Album":
        base += 6
    secondary = release_group.get("secondary-type-list", [])
    if isinstance(secondary, list) and any(st in {"Compilation", "Interview"} for st in secondary):
        base -= 8

    if release.get("status") == "Official":
        base += 8

    disambig = _normalize_string(release.get("disambiguation", ""))
    if disambig and any(keyword in disambig for keyword in ("karaoke", "tribute", "cover")):
        base -= 10

    return base


def _get_release_details(release_id: str) -> dict | None:
    if release_id in _RELEASE_CACHE:
        return _RELEASE_CACHE[release_id]
    try:
        response = musicbrainzngs.get_release_by_id(
            release_id, includes=["artists", "release-groups", "labels"]
        )
    except musicbrainzngs.WebServiceError as exc:
        print(f"-> Failed to fetch release {release_id}: {exc}")
        return None
    except Exception as exc:
        print(f"-> Unexpected error fetching release {release_id}: {exc}")
        return None

    release = response.get("release")
    if release:
        _RELEASE_CACHE[release_id] = release
    return release


def _collect_release_candidates(artist: str, album: str) -> list[dict]:
    normalized_artist = _normalize_string(artist)
    normalized_album = _normalize_string(album)

    preliminary_scores: dict[str, float] = {}
    metadata: dict[str, dict] = {}
    sources: dict[str, set[str]] = defaultdict(set)

    rg_result = {}
    try:
        rg_result = musicbrainzngs.search_release_groups(
            artist=artist, release=album, primarytype="Album", strict=True, limit=25
        )
    except TypeError:
        try:
            rg_result = musicbrainzngs.search_release_groups(
                artist=artist, release=album, strict=True, limit=25
            )
        except musicbrainzngs.WebServiceError as exc:
            print(f"-> MusicBrainz release-group search error: {exc}")
            rg_result = {}
    except musicbrainzngs.WebServiceError as exc:
        print(f"-> MusicBrainz release-group search error: {exc}")
        rg_result = {}

    release_group_candidates: list[tuple[float, dict]] = []
    for group in rg_result.get("release-group-list", []):
        group_score = _score_release_group(group, normalized_artist, normalized_album)
        if group_score < 55:
            continue
        release_group_candidates.append((group_score, group))

    release_group_candidates.sort(key=lambda item: item[0], reverse=True)

    for group_rank, (group_score, group) in enumerate(
        release_group_candidates[:MAX_RELEASE_GROUPS]
    ):
        group_id = group.get("id")
        if not group_id:
            continue
        try:
            expanded = musicbrainzngs.get_release_group_by_id(group_id, includes=["releases"])
        except musicbrainzngs.WebServiceError as exc:
            print(f"-> Failed to expand release-group {group_id}: {exc}")
            continue

        release_list = expanded.get("release-group", {}).get("release-list", [])
        release_list.sort(key=lambda r: _parse_release_date(r.get("date", "")))

        for rel_rank, release in enumerate(release_list[:MAX_RELEASES_PER_GROUP]):
            release_id = release.get("id")
            if not release_id:
                continue

            base_value = group_score - group_rank * 5 - rel_rank * 2
            if release_id in preliminary_scores:
                preliminary_scores[release_id] = max(preliminary_scores[release_id], base_value)
            else:
                preliminary_scores[release_id] = base_value

            meta = metadata.setdefault(release_id, {})
            meta.setdefault("release_group_id", group_id)
            meta.setdefault("release_group_title", group.get("title"))
            meta["release_group_score"] = group_score
            if release.get("title"):
                meta.setdefault("release_title", release.get("title"))
            if release.get("status"):
                meta.setdefault("status_hint", release.get("status"))
            if release.get("disambiguation"):
                meta.setdefault("disambiguation_hint", release.get("disambiguation"))

            sources[release_id].add("release-group")

    release_result = {}
    try:
        release_result = musicbrainzngs.search_releases(
            artist=artist, release=album, strict=True, limit=25
        )
    except TypeError:
        try:
            release_result = musicbrainzngs.search_releases(artist=artist, release=album, limit=25)
        except musicbrainzngs.WebServiceError as exc:
            print(f"-> MusicBrainz release search error: {exc}")
            release_result = {}
    except musicbrainzngs.WebServiceError as exc:
        print(f"-> MusicBrainz release search error: {exc}")
        release_result = {}

    for idx, release in enumerate(release_result.get("release-list", [])):
        release_id = release.get("id")
        if not release_id:
            continue
        base_score = _score_release_search_entry(release, normalized_artist, normalized_album)
        base_score -= idx * 1.5
        if release_id in preliminary_scores:
            preliminary_scores[release_id] = max(preliminary_scores[release_id], base_score)
        else:
            preliminary_scores[release_id] = base_score

        meta = metadata.setdefault(release_id, {})
        if release.get("title"):
            meta.setdefault("release_title", release.get("title"))
        if release.get("status"):
            meta.setdefault("status_hint", release.get("status"))
        release_group = release.get("release-group", {})
        if release_group:
            meta.setdefault("release_group_title", release_group.get("title"))
            meta.setdefault("release_group_id", release_group.get("id"))
        if release.get("disambiguation"):
            meta.setdefault("disambiguation_hint", release.get("disambiguation"))

        sources[release_id].add("release-search")

    candidate_items = sorted(preliminary_scores.items(), key=lambda item: item[1], reverse=True)[
        : MAX_TOTAL_CANDIDATES * 2
    ]

    candidates: list[dict] = []
    for release_id, base_value in candidate_items:
        release_details = _get_release_details(release_id)
        if not release_details:
            continue

        final_score = base_value + _score_release(
            release_details, normalized_artist, normalized_album
        )

        candidate_info = {
            "release": release_details,
            "base_score": round(base_value, 2),
            "final_score": round(final_score, 2),
            "primary_date": _release_primary_date(release_details),
            "primary_country": _release_primary_country(release_details),
            "sources": sorted(sources.get(release_id, [])),
            "status": release_details.get("status"),
            "artist_credit": release_details.get("artist-credit-phrase"),
        }
        candidate_info.update(metadata.get(release_id, {}))
        candidates.append(candidate_info)

    candidates.sort(
        key=lambda item: (
            -item["final_score"],
            _parse_release_date(item.get("primary_date") or ""),
        )
    )

    return candidates[:MAX_TOTAL_CANDIDATES]


def _image_sort_key(image: dict) -> tuple[int, int, int, int]:
    approved = 0 if _coerce_bool(image.get("approved")) else 1
    types = image.get("types") or []
    if isinstance(types, list):
        types_lower = {t.lower() for t in types if isinstance(t, str)}
    else:
        types_lower = set()
    is_front = 0 if (_coerce_bool(image.get("front")) or "front" in types_lower) else 1
    comment = _normalize_string(image.get("comment", ""))
    comment_penalty = (
        1
        if comment and any(keyword in comment for keyword in ("promo", "placeholder", "temp"))
        else 0
    )
    image_id = image.get("id")
    try:
        order = int(image_id)
    except (TypeError, ValueError):
        order = 0
    return (approved, is_front, comment_penalty, order)


def _download_cover_art(release_id: str):
    art_url = None
    try:
        image_list = musicbrainzngs.get_image_list(release_id)
        images = image_list.get("images", []) if isinstance(image_list, dict) else []
        front_images = [
            image
            for image in images
            if isinstance(image, dict)
            and (
                _coerce_bool(image.get("front"))
                or "front" in {t.lower() for t in image.get("types", []) if isinstance(t, str)}
            )
        ]
        candidates = front_images or [image for image in images if isinstance(image, dict)]
        if candidates:
            candidates.sort(key=_image_sort_key)
            selected = candidates[0]
            thumbnails = selected.get("thumbnails") or {}
            art_url = selected.get("image") or thumbnails.get("large") or thumbnails.get("small")
    except musicbrainzngs.ResponseError as exc:
        print(f"-> Cover Art Archive metadata not available for release {release_id}: {exc}")
    except Exception as exc:
        print(f"-> Unexpected error retrieving cover art metadata for {release_id}: {exc}")

    if not art_url:
        art_url = f"https://coverartarchive.org/release/{release_id}/front"

    response = requests.get(art_url, allow_redirects=True, timeout=15)
    response.raise_for_status()
    image_data = response.content
    mime_type = response.headers.get("Content-Type", "image/jpeg")
    return image_data, mime_type, art_url


def _embed_image(mp3_path: str, image_data: bytes, mime_type: str):
    try:
        audio = ID3(mp3_path)
    except ID3NoHeaderError:
        audio = ID3()
    audio.delall("APIC")
    audio.add(APIC(encoding=3, mime=mime_type, type=3, desc="Cover", data=image_data))
    audio.save(mp3_path)


def embed_from_release_id(mp3_path: str, release_id: str, release_title: str | None = None):
    try:
        image_data, mime_type, art_url = _download_cover_art(release_id)
        print(f"-> Successfully downloaded cover art from {art_url}")
        _embed_image(mp3_path, image_data, mime_type)
        if release_title:
            print(
                f"-> Successfully embedded artwork into '{mp3_path}' (Release: '{release_title}')"
            )
        else:
            print(f"-> Successfully embedded artwork into '{mp3_path}'")
        return True
    except requests.exceptions.RequestException as e:
        # Do not log here; let caller try other releases and log only if all fail.
        print(f"-> Failed to download cover art: {e}")
        return False
    except Exception as e:
        # Do not log here; let caller handle final logging.
        print(f"-> An unexpected error occurred while embedding from release id: {e}")
        return False


def _legacy_exact_match_attempt(
    mp3_path: str,
    artist: str,
    album: str,
    *,
    skip_release_ids: set[str],
    attempted_release_ids: list[str],
) -> tuple[bool, dict]:
    normalized_album = album.strip().lower()
    try:
        result = musicbrainzngs.search_releases(artist=artist, release=album, limit=25)
    except musicbrainzngs.WebServiceError as exc:
        print(f"-> MusicBrainz API error during legacy fallback: {exc}")
        return False, {"reason": "musicbrainz_error", "error": str(exc)}
    except Exception as exc:
        print(f"-> Unexpected error during legacy fallback: {exc}")
        return False, {"reason": "unexpected_error", "error": str(exc)}

    releases = result.get("release-list", [])
    if not releases:
        print("-> No releases found for given artist/album query (legacy fallback).")
        return False, {"reason": "no_releases"}

    exact_matches = [
        release
        for release in releases
        if release.get("title", "").strip().lower() == normalized_album
    ]

    if not exact_matches:
        print("-> No exact (case-insensitive) title match found (legacy fallback).")
        return False, {
            "reason": "no_exact_case_insensitive_match",
            "sample_titles": [r.get("title") for r in releases[:5]],
        }

    def _sort_key(entry):
        is_official_album = (
            entry.get("status") == "Official"
            and entry.get("release-group", {}).get("primary-type") == "Album"
        )
        date = _parse_release_date(entry.get("date", ""))
        return (0 if is_official_album else 1, date)

    exact_matches.sort(key=_sort_key)

    print(
        f"-> Legacy fallback found {len(exact_matches)} exact match(es): "
        + ", ".join([r.get("title", "?") for r in exact_matches])
    )

    attempted_ids = []
    for release in exact_matches:
        release_id = release.get("id")
        if not release_id or release_id in skip_release_ids:
            continue
        release_title = release.get("title", album)
        print(f"-> Trying legacy exact-match release '{release_title}' (ID: {release_id})")
        attempted_ids.append(release_id)
        attempted_release_ids.append(release_id)
        if embed_from_release_id(mp3_path, release_id, release_title):
            return True, {}

    if not attempted_ids:
        attempted_ids = [r.get("id") for r in exact_matches if r.get("id")]

    return False, {
        "reason": "no_cover_art_found_for_exact_title",
        "attempted_release_ids": attempted_ids,
    }


def embed_from_artist_album(mp3_path: str, artist: str, album: str):
    """
    Embed cover art for an MP3 by looking up the most relevant MusicBrainz release
    for the given artist and album. The search uses release-group heuristics plus a
    legacy exact-title fallback for safety.
    """
    print(f"Searching for album '{album}' by '{artist}' on MusicBrainz...")

    candidates = _collect_release_candidates(artist, album)
    attempted_release_ids: list[str] = []

    if not candidates:
        print("-> No high-confidence release candidates found; falling back to legacy logic.")

    eligible_candidates = [
        candidate for candidate in candidates if candidate.get("final_score", 0) >= 60
    ]
    if not eligible_candidates and candidates:
        eligible_candidates = candidates[:2]

    for candidate in eligible_candidates:
        release = candidate.get("release") or {}
        release_id = release.get("id")
        if not release_id:
            continue
        if release_id in attempted_release_ids:
            continue

        release_title = release.get("title") or candidate.get("release_title") or album
        score = candidate.get("final_score")
        sources = candidate.get("sources") or []
        debug_parts = []
        if isinstance(score, (int, float)):
            debug_parts.append(f"score={score:.1f}")
        if candidate.get("primary_date"):
            debug_parts.append(f"date={candidate['primary_date']}")
        if candidate.get("primary_country"):
            debug_parts.append(f"country={candidate['primary_country']}")
        if sources:
            debug_parts.append(f"sources={','.join(sources)}")
        status = candidate.get("status") or candidate.get("status_hint")
        if status:
            debug_parts.append(f"status={status}")
        debug_summary = ", ".join(debug_parts)

        print(
            f"-> Candidate release '{release_title}' (ID: {release_id})"
            + (f" [{debug_summary}]" if debug_summary else "")
        )

        attempted_release_ids.append(release_id)
        if embed_from_release_id(mp3_path, release_id, release_title):
            return

    legacy_success, legacy_info = _legacy_exact_match_attempt(
        mp3_path,
        artist,
        album,
        skip_release_ids=set(attempted_release_ids),
        attempted_release_ids=attempted_release_ids,
    )
    if legacy_success:
        return

    reason = "no_suitable_cover_art"
    if legacy_info and legacy_info.get("reason"):
        reason = legacy_info["reason"]

    candidate_rankings = []
    for candidate in candidates[:10]:
        release = candidate.get("release") or {}
        candidate_rankings.append(
            {
                "release_id": release.get("id"),
                "title": release.get("title"),
                "final_score": candidate.get("final_score"),
                "base_score": candidate.get("base_score"),
                "primary_date": candidate.get("primary_date"),
                "primary_country": candidate.get("primary_country"),
                "sources": candidate.get("sources"),
                "status": candidate.get("status") or candidate.get("status_hint"),
                "release_group_id": candidate.get("release_group_id"),
                "release_group_title": candidate.get("release_group_title"),
                "disambiguation_hint": candidate.get("disambiguation_hint"),
            }
        )

    log_entry = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "input": {"artist": artist, "album": album, "mp3_path": mp3_path},
        "reason": reason,
        "attempted_release_ids": attempted_release_ids,
    }
    if candidate_rankings:
        log_entry["candidate_rankings"] = candidate_rankings
    if legacy_info:
        log_entry["legacy_fallback"] = legacy_info

    _log_skip(log_entry)
    print("-> Failed to embed cover art after trying available candidates.")


def show_embedded_art(mp3_path: str):
    """Display the embedded cover art (preferring front cover) for the given MP3."""
    print(f"[show] Loading ID3 from: {mp3_path}")
    id3 = ID3(mp3_path)

    apics = id3.getall("APIC")
    print(f"[show] Found {len(apics)} APIC frame(s).")
    if not apics:
        print("[show] No embedded artwork found.")
        return None

    apic = next((a for a in apics if getattr(a, "type", None) == 3), apics[0])
    mime = apic.mime
    print(f"[show] Selected APIC type={getattr(apic, 'type', None)}, MIME={mime}")

    fmt = "png" if "png" in (mime or "").lower() else "jpeg"
    print(f"[show] Displaying image (format={fmt}, fixed width=400)...")
    display(IPyImage(data=apic.data, format=fmt, width=400))

    # Save to a temporary file to view
    fd, path = tempfile.mkstemp(suffix=f".{fmt}")
    with os.fdopen(fd, "wb") as f:
        f.write(apic.data)
    print(f"Saved embedded art to: {path}")

    return mime
