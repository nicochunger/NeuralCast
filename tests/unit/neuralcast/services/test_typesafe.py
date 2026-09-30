"""TypeSafe HTTP boundary validation and bounded retries."""

from unittest.mock import Mock

import pytest
import requests

from neuralcast.services import typesafe


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "private-test-key")
    post = Mock()
    monkeypatch.setattr(typesafe.requests, "post", post)
    monkeypatch.setattr(typesafe.time, "sleep", lambda _: None)
    return post


def test_batches_questions_and_authenticates(transport):
    transport.return_value = Mock(status_code=200, json=lambda: {"answers": {}})
    questions = {"a": {"type": "choice"}, "b": {"type": "choice"}}
    typesafe.evaluate_questions({"text": "context"}, questions)
    kwargs = transport.call_args.kwargs
    assert kwargs["json"]["questions"] == questions
    assert kwargs["json"]["model"] == "jev-1.13.0"
    assert kwargs["headers"]["Authorization"] == "Bearer private-test-key"
    assert kwargs["timeout"] == (2, 5)


@pytest.mark.parametrize("status", [401, 402, 422])
def test_permanent_errors_do_not_retry_or_expose_response(transport, status):
    transport.return_value = Mock(status_code=status, text="secret")
    with pytest.raises(typesafe.DecisionUnavailable) as exc:
        typesafe.evaluate_questions({}, {})
    assert "secret" not in str(exc.value)
    transport.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [Mock(status_code=429), Mock(status_code=529), requests.Timeout("secret")],
)
def test_transient_failures_retry_once(transport, failure):
    transport.side_effect = [
        failure,
        Mock(status_code=200, json=lambda: {"answers": {}}),
    ]
    assert typesafe.evaluate_questions({}, {}) == {"answers": {}}
    assert transport.call_count == 2


@pytest.mark.parametrize("result", [[], None, {}, {"answers": []}])
def test_rejects_bad_response_shapes(transport, result):
    transport.return_value = Mock(status_code=200, json=lambda: result)
    with pytest.raises(typesafe.DecisionUnavailable):
        typesafe.evaluate_questions({}, {})


def test_missing_key_never_calls_http(transport, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(typesafe.DecisionUnavailable):
        typesafe.evaluate_questions({}, {})
    transport.assert_not_called()
