"""The web server receives only channel-specific listener cards."""

from unittest.mock import Mock

from fastapi.testclient import TestClient

from neuralcast.admin_api.app import create_app


def test_companion_endpoint_auth_channel_validation_and_null(monkeypatch):
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_TOKEN", "test-token")
    loader = Mock(return_value=None)
    with TestClient(create_app(companion_loader=loader)) as client:
        url = "/admin/host-channels/neuralforge-fr/companion"
        assert client.get(url).status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        assert (
            client.get(
                "/admin/host-channels/unknown/companion", headers=headers
            ).status_code
            == 404
        )
        loader.assert_not_called()
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"card": None}
        assert loader.call_args.args[0] == "neuralforge-fr"
        loader.return_value = {"id": "media", "channel": "neuralforge-fr"}
        assert client.get(url, headers=headers).json() == {"card": loader.return_value}
