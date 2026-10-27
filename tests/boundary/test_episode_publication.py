"""Publication isolation, stable metadata, and anonymous seekable delivery."""

import shutil
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from neuralcast.admin_api.app import create_app
from neuralcast.pipelines.episode_producer import publication
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
