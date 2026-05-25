"""Unit tests for album-art selection and image normalization helpers."""

from __future__ import annotations

from io import BytesIO

import pytest
import requests
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


def test_download_cover_art_selects_best_image_and_caches(monkeypatch) -> None:
    album_art._COVER_ART_CACHE.clear()
    image_data = _image_bytes(mode="RGB", fmt="JPEG", size=(10, 10))
    calls = {"metadata": 0, "download": 0}

    def fake_image_list(_release_id: str) -> dict:
        calls["metadata"] += 1
        return {
            "images": [
                {
                    "id": "2",
                    "approved": False,
                    "front": True,
                    "image": "https://example.test/bad.jpg",
                },
                {
                    "id": "1",
                    "approved": True,
                    "types": ["Front"],
                    "thumbnails": {"large": "https://example.test/good.jpg"},
                },
            ]
        }

    class FakeResponse:
        headers = {"Content-Type": "image/jpeg"}
        content = image_data

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **_kwargs) -> FakeResponse:
        calls["download"] += 1
        assert url == "https://example.test/good.jpg"
        return FakeResponse()

    monkeypatch.setattr(album_art.musicbrainzngs, "get_image_list", fake_image_list)
    monkeypatch.setattr(album_art.requests, "get", fake_get)

    first = album_art._download_cover_art("release-1")
    second = album_art._download_cover_art("release-1")

    assert first == second
    assert first[1] == "image/jpeg"
    assert calls == {"metadata": 1, "download": 1}


def test_download_cover_art_falls_back_to_direct_archive_url(monkeypatch) -> None:
    album_art._COVER_ART_CACHE.clear()
    image_data = _image_bytes(mode="RGB", fmt="JPEG", size=(10, 10))

    class FakeResponse:
        headers = {}
        content = image_data

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        album_art.musicbrainzngs,
        "get_image_list",
        lambda _release_id: {"images": []},
    )
    monkeypatch.setattr(
        album_art.requests,
        "get",
        lambda url, **_kwargs: FakeResponse()
        if url == "https://coverartarchive.org/release/release-2/front"
        else pytest.fail(f"unexpected url {url}"),
    )

    _data, mime_type, art_url = album_art._download_cover_art("release-2")

    assert mime_type == "image/jpeg"
    assert art_url == "https://coverartarchive.org/release/release-2/front"


def test_embed_local_cover_art_handles_missing_and_success(tmp_path, monkeypatch) -> None:
    mp3_path = tmp_path / "song.mp3"
    image_path = tmp_path / "cover.png"
    mp3_path.write_bytes(b"mp3")
    image_path.write_bytes(_image_bytes(mode="RGB", fmt="PNG", size=(10, 10)))
    embedded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        album_art,
        "_embed_image",
        lambda path, _data, mime_type: embedded.append((path, mime_type)),
    )

    assert album_art.embed_local_cover_art(mp3_path, tmp_path / "missing.png") is False
    assert album_art.embed_local_cover_art(mp3_path, image_path) is True
    assert embedded == [(str(mp3_path), "image/png")]


def test_embed_from_release_id_returns_false_on_request_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        album_art,
        "_download_cover_art",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.exceptions.RequestException("offline")
        ),
    )

    assert album_art.embed_from_release_id(str(tmp_path / "song.mp3"), "release-1") is False
