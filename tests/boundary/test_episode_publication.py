"""Publication isolation, stable metadata, and anonymous seekable delivery."""

import shutil
import json
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from neuralcast.admin_api.app import create_app
from neuralcast.pipelines.episode_producer import publication, rss
from neuralcast.pipelines.episode_producer.models import Proposal
from neuralcast.pipelines.episode_producer.storage import (
    Project,
    file_hash,
    read_json,
    write_json,
)


@pytest.fixture
def episode(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "catalogue_root", lambda: tmp_path / "catalogue")
    station = tmp_path / "station"
    monkeypatch.setattr(publication, "station_dir_from_slug", lambda _: station)
    project = Project(
        "neuralforge",
        "journey",
        station_root=station,
        runtime_root=tmp_path / "runtime",
    )
    revision = "a" * 64
    directory = project.runtime / revision
    directory.mkdir(parents=True)
    audio = directory / "episode.mp3"
    audio.write_bytes(b"0123456789abcdef")
    (directory / "song.jpg").write_bytes(b"artwork")
    remote = station / "songs" / "episodes" / "journey" / revision / "episode.mp3"
    remote.parent.mkdir(parents=True)
    shutil.copyfile(audio, remote)
    receipt = {
        "station": "neuralforge",
        "episode_id": "journey",
        "channel": "neuralforge-es",
        "revision": revision,
        "render_hash": file_hash(audio),
        "title": "A journey",
        "summary": "Music and stories",
        "duration": 100,
        "uploaded": {"path": f"episodes/journey/{revision}/episode.mp3"},
        "queue": None,
        "chapters": [
            {
                "id": "intro",
                "kind": "commentary",
                "start": 0,
                "end": 10,
                "title": "Introduction",
                "summary": "Welcome",
                "transcript": "Hello",
                "path": "/private/speech.mp3",
                "sha256": "secret",
            },
            {
                "id": "song",
                "kind": "music",
                "start": 10,
                "end": 100,
                "title": "Song",
                "artist": "Artist",
                "artwork": "song.jpg",
                "path": "/private/library.mp3",
            },
        ],
    }
    write_json(directory / "production.json", receipt)
    write_json(project.runtime / "latest.json", {"revision": revision})
    return project, receipt, directory, remote


def test_publish_is_explicit_snapshot_and_does_not_need_queue_or_source_documents(
    episode,
):
    project, receipt, directory, remote = episode
    assert publication.list_episodes() == []
    with project.lock():
        value = publication.publish(
            project, receipt["render_hash"], "Listener description"
        )
    assert value["description"] == "Listener description"
    assert "private" not in str(value) and "sha256" not in str(value)
    assert "audio_path" not in value and "render_hash" not in value
    assert value["transcript"] == "Hello"
    assert publication.list_episodes("neuralcast") == []
    assert len(publication.list_episodes("neuralforge")) == 1
    assert publication.published_audio("neuralforge", "journey") == remote
    with project.lock():
        assert publication.publish(project, receipt["render_hash"]) == value
    shutil.rmtree(directory)
    assert (
        publication.published_artwork("neuralforge", "journey", "song").read_bytes()
        == b"artwork"
    )
    assert (
        publication.public_episode(
            publication.load_publication("neuralforge", "journey")
        )
        == value
    )


def write_proposal(project):
    proposal = Proposal(
        episode_id=project.id,
        channel="neuralforge-es",
        title="A journey",
        premise="Music and stories",
        outline=["Opening", "Music", "Closing"],
    )
    write_json(project.documents / "proposal.json", proposal.model_dump())


def test_number_existing_publication_preserves_audio_urls_and_snapshot(episode):
    project, receipt, directory, remote = episode
    before = publication.publish(project, receipt["render_hash"])
    manifest = (
        publication.episode_directory(project.station, project.id) / "manifest.json"
    )
    original = read_json(manifest)
    production = (directory / "production.json").read_bytes()
    audio_hash = file_hash(remote)
    write_proposal(project)
    with project.lock():
        result = publication.set_episode_number(project, 2)
    assert result["episode_number"] == 2
    assert project.documents.name == "002-journey"
    assert read_json(manifest) == {**original, "episode_number": 2}
    after = publication.public_episode(
        publication.load_publication(project.station, project.id)
    )
    assert after == {**before, "episode_number": 2}
    assert publication.list_episodes(project.station)[0]["episode_number"] == 2
    assert publication.publish(project, receipt["render_hash"]) == after
    assert publication.published_audio(project.station, project.id) == remote
    assert file_hash(remote) == audio_hash
    assert (directory / "production.json").read_bytes() == production
    assert publication.set_episode_number(project, 2) == result
    with pytest.raises(ValueError, match="different number"):
        publication.set_episode_number(project, 3)


def test_new_publication_copies_number_and_public_api_exposes_it(episode):
    project, receipt, _, _ = episode
    write_proposal(project)
    publication.set_episode_number(project, 3)
    value = publication.publish(project, receipt["render_hash"])
    assert value["episode_number"] == 3
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        assert (
            client.get("/public/episodes").json()["episodes"][0]["episode_number"] == 3
        )
        assert (
            client.get("/public/episodes/neuralforge/journey").json()["episode_number"]
            == 3
        )


def test_episode_number_cannot_duplicate_a_published_number(episode):
    project, receipt, _, _ = episode
    write_proposal(project)
    publication.set_episode_number(project, 1)
    publication.publish(project, receipt["render_hash"])
    other = Project(project.station, "other", station_root=project.station_root)
    with pytest.raises(ValueError, match="already published"):
        publication.set_episode_number(other, 1)
    assert not other.documents.exists()


def test_publication_checks_both_copies_of_reviewed_audio(episode):
    project, receipt, _, remote = episode
    with pytest.raises(ValueError, match="reviewed render"):
        publication.publish(project, "b" * 64)
    remote.write_bytes(b"different recording")
    with pytest.raises(ValueError, match="differs"):
        publication.publish(project, receipt["render_hash"])
    assert publication.list_episodes() == []


def test_unknown_preview_and_path_escape_are_not_public(episode):
    project, receipt, _, _ = episode
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        assert (
            client.get("/public/episodes/neuralforge/journey/audio").status_code == 404
        )
    for path in (
        "../secret.mp3",
        "songs/private.mp3",
        "episodes/../../secret.mp3",
        "/secret.mp3",
    ):
        with pytest.raises(ValueError):
            publication._media_path("neuralforge", path)
    publication.publish(project, receipt["render_hash"])
    catalogue = publication.episode_directory("neuralforge", "journey")
    (catalogue / "song.jpg").unlink()
    (catalogue / "song.jpg").symlink_to(project.runtime / "latest.json")
    with pytest.raises(ValueError):
        publication.published_artwork("neuralforge", "journey", "song")


def test_public_http_audio_supports_head_range_and_artwork_without_admin_token(episode):
    project, receipt, _, _ = episode
    publication.publish(project, receipt["render_hash"])
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        prefix = "/public/episodes/neuralforge/journey"
        assert client.get("/public/episodes").json()["episodes"][0]["id"] == "journey"
        assert client.get(prefix).json()["chapters"][0]["summary"] == "Welcome"
        response = client.get(prefix + "/audio", headers={"Range": "bytes=3-6"})
        assert response.status_code == 206 and response.content == b"3456"
        assert response.headers["content-range"] == "bytes 3-6/16"
        assert client.head(prefix + "/audio").headers["content-length"] == "16"
        assert (
            client.get(prefix + "/audio", headers={"Range": "bytes=99-100"}).status_code
            == 416
        )
        assert client.get(prefix + "/art/song").content == b"artwork"
        assert client.get(prefix + "/art/intro").status_code == 404
        assert client.get("/public/episodes?station=unknown").status_code == 404
        assert client.get("/admin/options").status_code in (401, 503)


@pytest.fixture
def podcast_assets(tmp_path, monkeypatch):
    assets = tmp_path / "podcasts"
    shutil.copytree(rss.ASSET_DIRECTORY, assets)
    monkeypatch.setattr(rss, "ASSET_DIRECTORY", assets)
    monkeypatch.delenv("NEURALCAST_PODCAST_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("NEURALCAST_PODCAST_WEBSITE_BASE_URL", raising=False)
    return assets


def test_rss_is_anonymous_published_only_and_uses_absolute_proxy_urls(
    episode, podcast_assets
):
    project, receipt, _, remote = episode
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        path = "/public/podcasts/neuralforge/feed.xml"
        empty = ET.fromstring(client.get(path).content)
        assert empty.findall("./channel/item") == []
        assert (
            client.get("/public/episodes/neuralforge/journey/chapters.json").status_code
            == 404
        )
        receipt["title"] = "Música & historias <nuevas>"
        receipt["summary"] = 'Rock & metal: "historias"'
        write_json(project.runtime / receipt["revision"] / "production.json", receipt)
        publication.publish(project, receipt["render_hash"])
        response = client.get(path, headers={"Host": "attacker.invalid"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/rss+xml; charset=utf-8"
        channel = ET.fromstring(response.content).find("channel")
        assert channel.findtext("title") == "NeuralForge Podcast"
        assert channel.findtext("language") == "es-AR"
        assert (
            channel.findtext("link")
            == "https://neuralcast.fm/episodes?station=neuralforge"
        )
        item = channel.find("item")
        assert item.findtext("title") == receipt["title"]
        assert item.findtext("description") == receipt["summary"]
        assert (
            item.findtext("link")
            == "https://neuralcast.fm/episodes/neuralforge/journey"
        )
        assert (
            parsedate_to_datetime(item.findtext("pubDate")).utcoffset().total_seconds()
            == 0
        )
        enclosure = item.find("enclosure")
        assert enclosure.attrib == {
            "url": "https://neuralcast.duckdns.org/admin-http/public/episodes/neuralforge/journey/audio",
            "length": str(remote.stat().st_size),
            "type": "audio/mpeg",
        }
        chapters = item.find(f"{{{rss.PODCAST}}}chapters")
        assert chapters.get("type") == "application/json+chapters"
        assert chapters.get("url").endswith("/journey/chapters.json")
        assert "attacker" not in response.text and "render_hash" not in response.text
        assert (
            ET.fromstring(
                client.get("/public/podcasts/neuralcast/feed.xml").content
            ).findall("./channel/item")
            == []
        )
        assert client.get("/public/podcasts/unknown/feed.xml").status_code == 404
        assert client.get("/admin/options").status_code in (401, 503)


def test_rss_identity_survives_renumbering_metadata_and_origin_changes(
    episode, podcast_assets, monkeypatch
):
    project, receipt, _, _ = episode
    publication.publish(project, receipt["render_hash"])
    before = ET.fromstring(rss.feed(project.station)).find("./channel/item")
    write_proposal(project)
    publication.set_episode_number(project, 7)
    manifest = (
        publication.episode_directory(project.station, project.id) / "manifest.json"
    )
    value = read_json(manifest)
    value["title"] = "A different title"
    write_json(manifest, value)
    monkeypatch.setenv(
        "NEURALCAST_PODCAST_PUBLIC_BASE_URL", "https://radio.example/prefix"
    )
    after = ET.fromstring(rss.feed(project.station)).find("./channel/item")
    assert before.findtext("guid") == after.findtext("guid")
    assert after.find("guid").get("isPermaLink") == "false"
    assert before.findtext("pubDate") == after.findtext("pubDate")
    assert after.findtext(f"{{{rss.ITUNES}}}episode") == "7"
    assert (
        after.find("enclosure").get("url").startswith("https://radio.example/prefix/")
    )


def test_rss_updates_etag_only_when_feed_changes_and_supports_head(
    episode, podcast_assets
):
    project, receipt, _, _ = episode
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        path = "/public/podcasts/neuralforge/feed.xml"
        before = client.get(path)
        head = client.head(path)
        assert not head.content
        assert head.headers["content-length"] == str(len(before.content))
        assert head.headers["etag"] == before.headers["etag"]
        assert (
            client.get(
                path, headers={"If-None-Match": "W/" + before.headers["etag"]}
            ).status_code
            == 304
        )
        publication.publish(project, receipt["render_hash"])
        after = client.get(path, headers={"If-None-Match": before.headers["etag"]})
        assert after.status_code == 200
        assert after.headers["etag"] != before.headers["etag"]
        assert (
            client.get(
                path, headers={"If-None-Match": '"other", ' + after.headers["etag"]}
            ).status_code
            == 304
        )
        assert client.head(path, headers={"If-None-Match": "*"}).status_code == 304


def test_cloud_chapters_reuse_exact_times_with_short_titles_art_and_guide_links(
    episode, podcast_assets
):
    project, receipt, _, remote = episode
    value = publication.publish(project, receipt["render_hash"])
    manifest = (
        publication.episode_directory(project.station, project.id) / "manifest.json"
    )
    before = manifest.read_bytes()
    audio_before = remote.read_bytes()
    overrides = {"neuralforge": {"journey": {"intro": "Una historia breve"}}}
    (podcast_assets / "chapter_titles.json").write_text(json.dumps(overrides))
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        path = "/public/episodes/neuralforge/journey/chapters.json"
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json+chapters"
        chapters = response.json()
        assert chapters["version"] == "1.2.0"
        assert [c["startTime"] for c in chapters["chapters"]] == [
            c["start"] for c in value["chapters"]
        ]
        assert [c["title"] for c in chapters["chapters"]] == [
            "Una historia breve",
            "Song — Artist",
        ]
        assert "img" not in chapters["chapters"][0]
        assert (
            chapters["chapters"][1]["img"]
            == "https://neuralcast.duckdns.org/admin-http/public/episodes/neuralforge/journey/art/song"
        )
        assert (
            chapters["chapters"][0]["url"]
            == "https://neuralcast.fm/episodes/neuralforge/journey"
        )
        assert "transcript" not in response.text and "private" not in response.text
        assert (
            client.get(
                path, headers={"If-None-Match": response.headers["etag"]}
            ).status_code
            == 304
        )
        assert client.head(path).headers["content-length"] == str(len(response.content))
        assert not client.head(path).content
        overrides["neuralforge"]["journey"]["intro"] = "Otro título"
        (podcast_assets / "chapter_titles.json").write_text(json.dumps(overrides))
        assert client.get(path).headers["etag"] != response.headers["etag"]
        assert (
            client.get("/public/episodes/neuralcast/journey/chapters.json").status_code
            == 404
        )
    assert manifest.read_bytes() == before and remote.read_bytes() == audio_before


@pytest.mark.parametrize(
    "title",
    [
        "  Introducción   breve ",
        "Una historia larga que necesita un título bastante más corto para el teléfono",
        "x" * 80,
        "The Haunting (Somewhere in Time)",
    ],
)
def test_chapter_titles_fit_app(title):
    result = rss.short_title(title)
    assert 0 < len(result) <= 40
    assert result == result.strip() and "  " not in result


@pytest.mark.parametrize("start", [-0.1, float("nan"), float("inf"), 0, 100])
def test_invalid_chapter_timing_is_not_served(episode, podcast_assets, start):
    project, receipt, _, _ = episode
    publication.publish(project, receipt["render_hash"])
    manifest = (
        publication.episode_directory(project.station, project.id) / "manifest.json"
    )
    value = read_json(manifest)
    value["chapters"][1]["start"] = start
    write_json(manifest, value)
    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        assert (
            client.get("/public/episodes/neuralforge/journey/chapters.json").status_code
            == 404
        )


def test_rss_omits_unavailable_audio_and_other_languages(episode, podcast_assets):
    project, receipt, _, remote = episode
    publication.publish(project, receipt["render_hash"])
    manifest = (
        publication.episode_directory(project.station, project.id) / "manifest.json"
    )
    value = read_json(manifest)
    value["language"] = "fr-FR"
    write_json(manifest, value)
    assert ET.fromstring(rss.feed(project.station)).findall("./channel/item") == []
    value["language"] = "es-AR"
    write_json(manifest, value)
    assert len(ET.fromstring(rss.feed(project.station)).findall("./channel/item")) == 1
    remote.write_bytes(b"changed")
    assert ET.fromstring(rss.feed(project.station)).findall("./channel/item") == []
    remote.unlink()
    assert ET.fromstring(rss.feed(project.station)).findall("./channel/item") == []


def test_show_covers_are_public_square_and_match_feed(episode, podcast_assets):
    from io import BytesIO
    from PIL import Image

    with TestClient(create_app(job_manager=Mock(), station_service=Mock())) as client:
        for station in ("neuralcast", "neuralforge"):
            feed = ET.fromstring(
                client.get(f"/public/podcasts/{station}/feed.xml").content
            )
            url = feed.find(f"./channel/{{{rss.ITUNES}}}image").get("href")
            path = url.removeprefix("https://neuralcast.duckdns.org/admin-http")
            response = client.get(path)
            assert (
                response.status_code == 200
                and response.headers["content-type"] == "image/png"
            )
            image = Image.open(BytesIO(response.content))
            assert image.width == image.height and image.width >= 1000
            assert client.head(path).headers["content-length"] == str(
                len(response.content)
            )
        assert client.get("/public/podcasts/unknown/cover.png").status_code == 404
