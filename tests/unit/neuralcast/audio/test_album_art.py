"""Unit tests for album-art selection and image normalization helpers."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from neuralcast.audio import album_art


def _image_bytes(mode: str = "RGB", fmt: str = "PNG", size: tuple[int, int] = (20, 20)) -> bytes:
    image = Image.new(mode, size, (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_find_best_release_prefers_earliest_official_album() -> None:
    selected = album_art.find_best_release_from_releases(
        [
            {"status": "Official", "release-group": {"primary-type": "Single"}, "date": "1999"},
            {"status": "Official", "release-group": {"primary-type": "Album"}, "date": "2001-05-01"},
            {"status": "Official", "release-group": {"primary-type": "Album"}, "date": "1998"},
        ]
    )

    assert selected["date"] == "1998"


def test_release_matching_is_accent_and_credit_aware() -> None:
    normalized_artist = album_art._normalize_string("Motörhead")

    assert album_art._release_matches_artist(
        {"artist-credit-phrase": "Motorhead"},
        normalized_artist,
    )
    assert album_art._release_matches_artist(
        {"artist-credit": [{"name": "Motörhead"}]},
        normalized_artist,
    )


def test_release_scoring_rewards_official_front_cover_artist_match() -> None:
    normalized_artist = album_art._normalize_string("Ghost")
    normalized_album = album_art._normalize_string("Prequelle")
    strong = {
        "status": "Official",
        "title": "Prequelle",
        "artist-credit-phrase": "Ghost",
        "cover-art-archive": {"front": True},
    }
    weak = {
        "status": "Bootleg",
        "title": "Prequelle Karaoke",
        "artist-credit-phrase": "Someone Else",
        "cover-art-archive": {},
        "disambiguation": "karaoke tribute",
    }

    assert album_art._score_release(strong, normalized_artist, normalized_album) > album_art._score_release(
        weak,
        normalized_artist,
        normalized_album,
    )


def test_image_sort_key_prefers_approved_front_non_placeholder() -> None:
    images = [
        {"id": "3", "approved": False, "types": ["Front"]},
        {"id": "2", "approved": True, "types": ["Back"]},
        {"id": "1", "approved": True, "types": ["Front"], "comment": "placeholder"},
        {"id": "4", "approved": True, "types": ["Front"]},
    ]

    assert sorted(images, key=album_art._image_sort_key)[0]["id"] == "4"


def test_normalize_cover_art_resizes_large_jpeg_under_budget() -> None:
    original = _image_bytes(mode="RGB", fmt="JPEG", size=(160, 160))

    normalized, mime_type = album_art._normalize_cover_art(
        original,
        "image/jpeg",
        max_px=40,
        max_bytes=10_000,
    )

    assert mime_type == "image/jpeg"
    assert len(normalized) <= 10_000
    with Image.open(BytesIO(normalized)) as image:
        assert max(image.size) <= 40


def test_normalize_cover_art_preserves_meaningful_alpha_as_png() -> None:
    original = _image_bytes(mode="RGBA", fmt="PNG", size=(20, 20))

    normalized, mime_type = album_art._normalize_cover_art(original, "image/png")

    assert mime_type == "image/png"
    with Image.open(BytesIO(normalized)) as image:
        assert image.mode in {"RGBA", "P"}
