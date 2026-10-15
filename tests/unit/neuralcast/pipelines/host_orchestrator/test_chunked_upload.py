"""Large episodes follow the existing Media Manager Flow upload protocol."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator.transport import AzuraCastClient


def test_flow_chunks_preserve_bytes_relative_path_and_no_playlist_assignment(tmp_path):
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"0123456789")
    client = AzuraCastClient("https://example.com", "test-key")
    client._request = Mock(
        side_effect=[
            SimpleNamespace(status_code=200),
            SimpleNamespace(status_code=200),
            SimpleNamespace(status_code=201, json=lambda: {"success": True}),
        ]
    )
    result = client.upload_media_chunked(
        "neuralforge", audio, "Episodes/test/episode.mp3", chunk_bytes=4
    )
    assert result == {"success": True}
    calls = client._request.call_args_list
    assert len(calls) == 3
    assert (
        b"".join(call.kwargs["files"]["file_data"][1] for call in calls)
        == audio.read_bytes()
    )
    assert len({call.kwargs["data"]["flowIdentifier"] for call in calls}) == 1
    for index, call in enumerate(calls):
        assert call.args == ("POST", "/api/station/neuralforge/files/upload")
        data = call.kwargs["data"]
        assert data["flowChunkNumber"] == index + 1
        assert data["flowTotalChunks"] == 3
        assert data["flowTotalSize"] == 10
        assert data["flowCurrentChunkSize"] == len(call.kwargs["files"]["file_data"][1])
        assert data["flowRelativePath"] == "Episodes/test/episode.mp3"
        assert "currentDirectory" not in data
        assert "searchPhrase" not in data


@pytest.mark.parametrize(
    "destination",
    ["../escape.mp3", "/absolute.mp3", "Episodes\\escape.mp3", "Episodes//escape.mp3"],
)
def test_flow_rejects_ambiguous_destinations(tmp_path, destination):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    client = AzuraCastClient("https://example.com", "test-key")
    client._request = Mock()
    with pytest.raises(ValueError):
        client.upload_media_chunked("neuralforge", audio, destination)
    client._request.assert_not_called()


def test_flow_does_not_retry_an_unconfirmed_final_chunk(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    client = AzuraCastClient("https://example.com", "test-key")
    client._request = Mock(return_value=SimpleNamespace(status_code=204))
    with pytest.raises(RuntimeError, match="did not confirm assembly"):
        client.upload_media_chunked("neuralforge", audio, "Episodes/audio.mp3")
    client._request.assert_called_once()
