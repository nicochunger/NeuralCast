"""Boundary tests for public Sonic Atlas & Station Vault HTTP routes."""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from neuralcast.admin_api.app import create_app
from neuralcast.pipelines.episode_producer import publication
from neuralcast.pipelines.sonic_atlas import clear_atlas_cache
from neuralcast.pipelines.sonic_atlas import indexer as atlas_indexer

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


@pytest.fixture(autouse=True)
def _reset_atlas_cache():
    clear_atlas_cache()
    yield
    clear_atlas_cache()


def _seed_station_fixture(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    nc_dir = tmp_path / "NeuralCast"
    nf_dir = tmp_path / "NeuralForge"
    cat_dir = tmp_path / "catalogue"

    (nc_dir / "playlists").mkdir(parents=True)
    (nc_dir / "playlists" / "Classic Rock.csv").write_text(
        "Artist,Title,Year,Album,Validated\n"
        "Queen,Bohemian Rhapsody,1975,A Night at the Opera,True\n"
        "[https://youtu.be/xyz123] Pink Floyd,Wish You Were Here,1975,Wish You Were Here,True\n"
        "[DEL] Bad Entry,Ignore Me,1990,None,False\n",
        encoding="utf-8",
    )
    (nc_dir / "playlists" / "Aspen Vibes.csv").write_text(
        "Artist,Title,Year,Album,Validated\n"
        "Queen,Bohemian Rhapsody,1975,A Night at the Opera,True\n"
        "Toto,Africa,1982,Toto IV,True\n",
        encoding="utf-8",
    )
    (nc_dir / "playlist_descriptions.md").write_text(
        "## Playlist catalog\n\n"
        "### Classic Rock\n\n"
        "Guitar-led rock classics. Prefer Aspen Vibes for soft ballads.\n\n"
        "### Aspen Vibes\n\n"
        "Soft-ish familiar listening. Overlap with other playlists is intentional here.\n",
        encoding="utf-8",
    )

    (nf_dir / "playlists").mkdir(parents=True)
    (nf_dir / "playlists" / "Classic Metal.csv").write_text(
        "Artist,Title,Year,Album,Validated\n"
        "Iron Maiden,Hallowed Be Thy Name,1982,The Number of the Beast,True\n",
        encoding="utf-8",
    )

    ep_dir = cat_dir / "neuralcast" / "rock-story"
    ep_dir.mkdir(parents=True)
    (ep_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "id": "rock-story",
                "station": "neuralcast",
                "channel": "neuralcast-es",
                "language": "es-AR",
                "title": "Rock Stories",
                "episode_number": 1,
                "description": "Inside classic rock epics.",
                "published_at": "2026-11-01T10:00:00+00:00",
                "duration": 1500.0,
                "chapters": [
                    {
                        "kind": "music",
                        "id": "queen-bohemian",
                        "artist": "Queen",
                        "title": "Bohemian Rhapsody",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path, cat_dir


def test_atlas_public_endpoints_headers_etag_head_and_404s(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cat_dir = _seed_station_fixture(tmp_path)
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_TOKEN", "super-secret-admin-token")
    monkeypatch.setattr(
        atlas_indexer,
        "station_dir_from_slug",
        lambda slug: root / ("NeuralCast" if slug == "neuralcast" else "NeuralForge"),
    )
    monkeypatch.setattr(publication, "catalogue_root", lambda: cat_dir)

    client = TestClient(create_app())

    # Public endpoint requires NO Authorization header even when admin token is configured.
    res = client.get("/public/atlas/neuralcast")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "public, max-age=120"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["access-control-allow-origin"] == "*"
    assert res.headers["content-type"].startswith("application/json")
    etag = res.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')

    payload = res.json()
    assert payload["station"] == "neuralcast"
    assert payload["station_name"] == "NeuralCast"
    assert payload["accent"] == "#1f8a78"
    assert payload["stats"]["playlist_count"] == 2
    assert payload["stats"]["unique_tracks"] == 3
    assert payload["stats"]["bridge_count"] == 1
    assert payload["stats"]["episode_count"] == 1

    # HEAD returns empty body with Content-Length and matching ETag.
    head_res = client.head("/public/atlas/neuralcast")
    assert head_res.status_code == 200
    assert head_res.content == b""
    assert head_res.headers["etag"] == etag
    assert int(head_res.headers["content-length"]) == len(res.content)
    assert head_res.headers["cache-control"] == "public, max-age=120"
    assert head_res.headers["x-content-type-options"] == "nosniff"
    assert head_res.headers["access-control-allow-origin"] == "*"

    # Conditional GET with ETag (exact, weak, and wildcard) -> 304.
    for if_none_match in (etag, f"W/{etag}", "*"):
        not_mod = client.get(
            "/public/atlas/neuralcast", headers={"If-None-Match": if_none_match}
        )
        assert not_mod.status_code == 304
        assert not_mod.content == b""
        assert not_mod.headers["etag"] == etag
        assert not_mod.headers["cache-control"] == "public, max-age=120"

    # Playlist detail endpoint GET / HEAD / 304.
    detail_res = client.get("/public/atlas/neuralcast/playlist/classic-rock")
    assert detail_res.status_code == 200
    assert detail_res.headers["cache-control"] == "public, max-age=120"
    assert detail_res.headers["x-content-type-options"] == "nosniff"
    assert detail_res.headers["access-control-allow-origin"] == "*"
    detail_etag = detail_res.headers["etag"]

    detail = detail_res.json()
    assert detail["station"] == "neuralcast"
    assert detail["playlist"]["slug"] == "classic-rock"
    assert len(detail["tracks"]) == 2
    assert detail["bridges"][0]["playlist_slug"] == "aspen-vibes"
    assert detail["decades"] == [{"decade": 1970, "label": "1970s", "track_count": 2}]
    assert detail["episodes"][0]["id"] == "rock-story"
    assert detail["episodes"][0]["matched_tracks"] == [
        {"artist": "Queen", "title": "Bohemian Rhapsody"}
    ]

    detail_head = client.head("/public/atlas/neuralcast/playlist/classic-rock")
    assert detail_head.status_code == 200
    assert detail_head.content == b""
    assert detail_head.headers["etag"] == detail_etag
    assert int(detail_head.headers["content-length"]) == len(detail_res.content)

    detail_304 = client.get(
        "/public/atlas/neuralcast/playlist/classic-rock",
        headers={"If-None-Match": detail_etag},
    )
    assert detail_304.status_code == 304

    # 404 cases.
    assert client.get("/public/atlas/unknown-station").status_code == 404
    assert client.head("/public/atlas/unknown-station").status_code == 404
    assert (
        client.get("/public/atlas/unknown-station/playlist/classic-rock").status_code
        == 404
    )
    assert (
        client.get("/public/atlas/neuralcast/playlist/nonexistent-slug").status_code
        == 404
    )
    assert (
        client.head("/public/atlas/neuralcast/playlist/nonexistent-slug").status_code
        == 404
    )


def test_real_station_files_smoke_index_cleanly() -> None:
    """Read-only smoke check against the real NeuralCast and NeuralForge data."""
    client = TestClient(create_app())

    expected_accents = {
        "neuralcast": ("NeuralCast", "#1f8a78"),
        "neuralforge": ("NeuralForge", "#b55748"),
    }

    for station, (expected_name, expected_accent) in expected_accents.items():
        res = client.get(f"/public/atlas/{station}")
        assert res.status_code == 200
        data = res.json()

        assert data["station"] == station
        assert data["station_name"] == expected_name
        assert data["accent"] == expected_accent
        assert data["stats"]["playlist_count"] > 0
        assert data["stats"]["unique_tracks"] > 0
        assert data["stats"]["unique_artists"] > 0
        assert len(data["playlists"]) == data["stats"]["playlist_count"]
        assert len(data["bridges"]) == data["stats"]["bridge_count"]
        assert len(data["decades"]) > 0

        for pl in data["playlists"]:
            assert SLUG_RE.match(pl["slug"]), f"Invalid slug: {pl['slug']}"
            assert 0.08 <= pl["x"] <= 0.92
            assert 0.08 <= pl["y"] <= 0.92
            assert pl["rotation_role"] in {"themed", "open_only", "reserved_window"}
            assert pl["description"], f"Missing editorial description for {pl['name']}"

        for artist in data["artists"]:
            assert SLUG_RE.match(artist["slug"]), f"Invalid artist slug: {artist['slug']}"
            assert not any(
                "youtu" in artist["name"].casefold() for _ in (0,)
            ), f"Unstripped URL in artist: {artist['name']}"

        # Verify first playlist detail route succeeds cleanly.
        first_slug = data["playlists"][0]["slug"]
        detail_res = client.get(f"/public/atlas/{station}/playlist/{first_slug}")
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["station"] == station
        assert detail["playlist"]["slug"] == first_slug
        assert len(detail["tracks"]) == data["playlists"][0]["track_count"]
