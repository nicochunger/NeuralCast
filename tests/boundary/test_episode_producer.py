"""Podcast preparation, read-only legacy guides, and upload recovery."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.episode_producer import service, presentation
from neuralcast.pipelines.episode_producer.models import Proposal
from neuralcast.pipelines.episode_producer.storage import (
    Project,
    digest,
    write_json,
    read_json,
)


@pytest.fixture
def production(tmp_path, monkeypatch):
    station = tmp_path / "NeuralForge"
    (station / "playlists").mkdir(parents=True)
    (station / "songs" / "Folk Metal").mkdir(parents=True)
    (station / "playlists" / "Folk Metal.csv").write_text(
        "Artist,Title,Album,Year,Validated\nArtist,Song,Album,2000,True\n"
    )
    (station / "songs" / "Folk Metal" / "Artist - Song.mp3").write_bytes(
        b"library recording"
    )
    project = Project(
        "neuralforge",
        "test-episode",
        station_root=station,
        runtime_root=tmp_path / "runtime",
    )
    proposal = Proposal(
        episode_id=project.id,
        channel="neuralforge-es",
        title="A journey",
        premise="A musical connection",
        target_seconds=600,
        commentary_budget_seconds=100,
        outline=["Opening", "Music", "Closing"],
    )
    write_json(project.documents / "proposal.json", proposal.model_dump())
    proposal_hash = digest(proposal.model_dump())
    plan = {
        "proposal_hash": proposal_hash,
        "segments": [
            {
                "kind": "commentary",
                "id": "intro",
                "role": "opening",
                "brief": "Introduce the theme",
            },
            {
                "kind": "music",
                "id": "song",
                "playlist": "Folk Metal",
                "artist": "Artist",
                "title": "Song",
                "rationale": "Illustrates the theme",
            },
            {
                "kind": "commentary",
                "id": "outro",
                "role": "closing",
                "brief": "Resolve the theme",
            },
        ],
    }
    write_json(project.documents / "plan.json", plan)
    monkeypatch.setattr(service, "extract_artwork", lambda *args: False)

    def narration(*args):
        return {
            "path": "fake.mp3",
            "transcript": "A story.",
            "sources": [],
            "companion": {
                "title": "Story",
                "summary": "A musical connection.",
                "key_points": [],
            },
        }

    monkeypatch.setattr(service, "prepare_narration", Mock(side_effect=narration))

    def render(parts, directory, *args):
        output = directory / "episode.mp3"
        output.write_bytes(b"rendered episode")
        chapters = [
            {
                **{key: value for key, value in part.items() if key != "path"},
                "start": index * 100,
                "end": (index + 1) * 100,
            }
            for index, part in enumerate(parts)
        ]
        return output, chapters, 300

    monkeypatch.setattr(service, "render", Mock(side_effect=render))
    monkeypatch.setattr(presentation, "published_root", lambda: tmp_path / "published")
    client = Mock()
    client.upload_media.side_effect = lambda station, path, **kw: {
        "id": 42,
        "song_id": "song-hash",
        "path": kw["remote_path"],
    }
    return project, client, proposal_hash


def test_prepare_requires_proposal_approval_and_uploads_without_queue(production):
    project, client, proposal_hash = production
    with pytest.raises(FileNotFoundError):
        service.prepare(project, client)
    client.upload_media.assert_not_called()
    project.approve_proposal(proposal_hash)
    receipt = service.prepare(project, client)
    assert receipt["uploaded"]["media_id"] == "42"
    assert receipt.get("queue") is None
    client.send_telnet_command.assert_not_called()
    assert not presentation.published_root().exists()
    assert service.prepare(project, client) == receipt
    assert service.render.call_count == 1
    assert client.upload_media.call_count == 1


def test_removed_queue_command_is_rejected_before_operational_work():
    from neuralcast.cli.episode_producer import main

    with pytest.raises(SystemExit) as exc:
        main(["approve-and-queue", "--episode", "test-episode"])
    assert exc.value.code == 2


def legacy_guide(receipt, directory):
    """Seed the old on-disk format without a production writer or queue action."""
    uploaded = receipt["uploaded"]
    write_json(
        presentation.published_root()
        / receipt["channel"]
        / f"{uploaded['media_id']}.json",
        {
            "channel": receipt["channel"],
            "media_id": uploaded["media_id"],
            "song_id": uploaded["song_id"],
            "title": receipt["title"],
            "summary": receipt["summary"],
            "duration": receipt["duration"],
            "revision": receipt["revision"],
            "directory": str(directory.resolve()),
            "chapters": receipt["chapters"],
        },
    )


def test_upload_failure_resolves_existing_path_without_reupload(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    client.upload_media.side_effect = RuntimeError("response lost")
    with pytest.raises(RuntimeError):
        service.prepare(project, client)
    receipt_dir = next(project.runtime.glob("*/production.json"))
    remote = read_json(receipt_dir)["upload_attempt"]["path"]
    client.list_media_files.return_value = [
        {"id": 42, "song_id": "song-hash", "path": remote}
    ]
    result = service.prepare(project, client)
    assert result["uploaded"]["media_id"] == "42"
    assert client.upload_media.call_count == 1
    client.send_telnet_command.assert_not_called()


def test_missing_unresolved_upload_stops_without_another_upload(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    client.upload_media.side_effect = RuntimeError("timeout")
    with pytest.raises(RuntimeError):
        service.prepare(project, client)
    client.list_media_files.return_value = []
    with pytest.raises(RuntimeError, match="unresolved"):
        service.prepare(project, client)
    assert client.upload_media.call_count == 1


def test_uploaded_podcast_is_not_a_live_guide_and_legacy_guides_remain_readable(
    production,
):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    receipt = service.prepare(project, client)
    observations = [{"song_id": "song-hash", "played_at": 1000, "current": True}]
    assert (
        presentation.find_episode_companion("neuralforge-es", observations, 1100)
        is None
    )
    legacy_guide(receipt, project.runtime / receipt["revision"])
    episode = presentation.find_episode_companion("neuralforge-es", observations, 1100)
    assert episode["episode"]["chapters"][1]["artist"] == "Artist"
    assert "sha256" not in episode["episode"]["chapters"][1]
    assert "path" not in episode["episode"]["chapters"][1]
    normal = {"expected_start": 1200}
    assert presentation.choose_companion(normal, episode)["archetype"] == "episode"
    assert (
        presentation.find_episode_companion("neuralforge-fr", observations, 1100)
        is None
    )
    assert (
        presentation.find_episode_companion("neuralforge-es", observations, 2500)
        is None
    )
    history = [{**observations[0], "current": False}]
    ended = presentation.find_episode_companion("neuralforge-es", history, 1400)
    assert presentation.choose_companion({"expected_start": 1350}, ended) == {
        "expected_start": 1350
    }


def test_companion_api_still_reads_legacy_episode_playback_data(
    production, monkeypatch
):
    from fastapi.testclient import TestClient
    from neuralcast.admin_api import app as api

    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    receipt = service.prepare(project, client)
    legacy_guide(receipt, project.runtime / receipt["revision"])
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_TOKEN", "test-token")
    monkeypatch.setattr(api.time, "time", lambda: 1100)
    observations = [{"song_id": "song-hash", "played_at": 1000, "current": True}]
    with TestClient(
        api.create_app(companion_loader=lambda *args: {"expected_start": 1200})
    ) as http:
        url = "/admin/host-channels/neuralforge-es/companion"
        assert http.get(url).status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        response = http.get(
            url, params={"playback": json.dumps(observations)}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["card"]["archetype"] == "episode"
        assert response.headers["cache-control"] == "no-store"
        assert (
            http.get(url, params={"playback": "{}"}, headers=headers).status_code == 422
        )
        assert (
            http.get(
                "/admin/host-channels/neuralforge-es/episodes/42/art/song",
                headers=headers,
            ).status_code
            == 404
        )


def test_artwork_lookup_is_approved_channel_scoped_and_bounded(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    receipt = service.prepare(project, client)
    directory = project.runtime / receipt["revision"]
    (directory / "song.jpg").write_bytes(b"art")
    receipt["chapters"][1]["artwork"] = "song.jpg"
    # Preview uploads do not expose artwork through listener endpoints.
    assert presentation.episode_artwork("neuralforge-es", "42", "song") is None
    legacy_guide(receipt, directory)
    assert (
        presentation.episode_artwork("neuralforge-es", "42", "song")
        == directory / "song.jpg"
    )
    assert presentation.episode_artwork("neuralforge-fr", "42", "song") is None
    receipt["chapters"][1]["artwork"] = "../../outside.jpg"
    legacy_guide(receipt, directory)
    assert presentation.episode_artwork("neuralforge-es", "42", "song") is None


def test_reprepare_preserves_historical_receipts_of_a_changed_render(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    receipt = service.prepare(project, client)
    directory = project.runtime / receipt["revision"]
    receipt["queue"] = {"status": "queued", "request_id": "historical"}
    write_json(directory / "production.json", receipt)
    path = directory / "episode.mp3"
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="must not be reset"):
        service.prepare(project, client)
    stored = read_json(path.with_name("production.json"))
    assert stored["queue"] == {"status": "queued", "request_id": "historical"}
    assert service.render.call_count == 1
    client.send_telnet_command.assert_not_called()


def test_document_edit_during_render_stops_before_upload(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    original_render = service.render.side_effect

    def changed(*args):
        result = original_render(*args)
        proposal = project.proposal().model_dump()
        proposal["premise"] = "A different direction"
        write_json(project.documents / "proposal.json", proposal)
        return result

    service.render.side_effect = changed
    with pytest.raises(ValueError, match="documents changed"):
        service.prepare(project, client)
    client.upload_media.assert_not_called()
    client.send_telnet_command.assert_not_called()


def test_large_finished_render_uses_chunk_upload_without_queue(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    original = service.render.side_effect

    def large_render(*args):
        path, chapters, duration = original(*args)
        with path.open("ab") as handle:
            handle.truncate(9 * 1024 * 1024)
        return path, chapters, duration

    service.render.side_effect = large_render
    client.upload_media_chunked.side_effect = lambda station, path, **kwargs: {
        "id": 42,
        "song_id": "song-hash",
        "path": kwargs["remote_path"],
    }
    receipt = service.prepare(project, client)
    assert receipt["uploaded"]["media_id"] == "42"
    assert receipt["upload_attempt"]["method"] == "flow"
    client.upload_media.assert_not_called()
    client.send_telnet_command.assert_not_called()


def test_definitive_upload_rejection_is_recorded_and_retry_archives_it(production):
    import requests
    from types import SimpleNamespace

    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)
    rejected = requests.HTTPError(
        "body too large", response=SimpleNamespace(status_code=413)
    )
    client.upload_media.side_effect = rejected
    with pytest.raises(requests.HTTPError):
        service.prepare(project, client)
    path = next(project.runtime.glob("*/production.json"))
    stored = read_json(path)
    assert stored["upload_attempt"]["status"] == "rejected"
    assert stored.get("queue") is None
    client.upload_media.side_effect = lambda station, source, **kwargs: {
        "id": 42,
        "song_id": "song-hash",
        "path": kwargs["remote_path"],
    }
    result = service.prepare(project, client)
    assert result["upload_history"][0]["http_status"] == 413
    assert result["uploaded"]["media_id"] == "42"
    client.send_telnet_command.assert_not_called()


def test_upload_resolves_server_normalized_path_without_resending(production):
    project, client, proposal_hash = production
    project.approve_proposal(proposal_hash)

    def upload(station, source, **kwargs):
        client.list_media_files.return_value = [
            {"id": 42, "song_id": "song-hash", "path": kwargs["remote_path"].lower()}
        ]
        return {"success": True}

    client.upload_media.side_effect = upload
    receipt = service.prepare(project, client)
    assert receipt["uploaded"]["path"].startswith("episodes/")
    assert receipt["uploaded"]["media_id"] == "42"
    service.prepare(project, client)
    assert client.upload_media.call_count == 1
    client.send_telnet_command.assert_not_called()
