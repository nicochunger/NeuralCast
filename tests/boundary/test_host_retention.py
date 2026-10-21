"""Retention uses owner APIs even for paused channels, without playback or TTS."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

from neuralcast.pipelines.host_orchestrator import assets, retention


def test_retention_expires_paused_channel_without_touching_podcasts(
    tmp_path, monkeypatch
):
    prefix = "AI Stories/neuralforge/fr-CH"
    station = tmp_path / "station"
    snippets = tmp_path / "snippets"
    remote = station / "songs" / prefix / "2026-09-10" / "story.mp3"
    local = snippets / "neuralforge-fr/2026-09-10/story.txt"
    podcast = station / "songs/episodes/review/episode.mp3"
    for p in (remote, local, podcast):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"audio")
        os.utime(p, (1, 1))
    monkeypatch.setattr(assets, "station_dir_from_slug", lambda slug: station)
    monkeypatch.setattr(assets, "STORY_OUTPUT_DIR", snippets)
    channel = SimpleNamespace(
        key="neuralforge-fr",
        enabled=False,
        media_owner_station="neuralforge",
        remote_prefix=prefix,
    )
    client = SimpleNamespace(
        list_media_files=Mock(
            return_value=[
                {"path": f"{prefix}/2026-09-10/story.mp3", "mtime": 1, "id": 1},
                {"path": "episodes/review/episode.mp3", "mtime": 1, "id": 2},
            ]
        ),
        delete_media_file=Mock(side_effect=lambda station, media_id: remote.unlink()),
    )
    registry = SimpleNamespace(channels={channel.key: channel})
    retention.cleanup_host_assets(client, registry=registry, dry_run=True)
    assert all(p.exists() for p in (remote, local, podcast))
    client.delete_media_file.assert_not_called()

    retention.cleanup_host_assets(client, registry=registry)
    client.list_media_files.assert_called_with("neuralforge")
    client.delete_media_file.assert_called_once_with("neuralforge", 1)
    assert not remote.parent.exists()
    assert not local.exists()
    assert podcast.read_bytes() == b"audio"
