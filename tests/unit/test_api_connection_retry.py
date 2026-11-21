"""ApiConnection.fetch: a 429 (Too Many Requests) is retried after a growing wait, other errors at once; no real sleeping, no network."""

from unittest.mock import MagicMock

import pytest
import requests

from coding.core.api import connection as connection_module
from coding.core.api.connection import ApiConnection
from coding.core.api.exceptions import RequestError
from coding.core.endpoints.endpoint_definition import EndpointDefinition, HttpMethod


def make_endpoint():
    return EndpointDefinition(path="/public/ticker", description="ticker", method=HttpMethod.GET)


def response(status_code, payload=None):
    reply = MagicMock()
    reply.status_code = status_code
    reply.json.return_value = payload or {"result": 1}
    if status_code >= 400:
        reply.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status_code} error", response=reply)
    return reply


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(connection_module.time, "sleep", recorded.append)
    return recorded


def make_connection(replies):
    connection = ApiConnection("https://example.invalid/api", max_retries=3)
    connection.session = MagicMock()
    connection.session.get.side_effect = replies
    return connection


def test_a_429_waits_one_then_two_seconds_and_then_succeeds(waits):
    connection = make_connection([response(429), response(429), response(200, {"result": 7})])
    assert connection.fetch(make_endpoint()) == {"result": 7}
    assert waits == [1.0, 2.0]


def test_a_429_on_every_attempt_raises_after_waiting_between_the_attempts_only(waits):
    connection = make_connection([response(429)] * 3)
    with pytest.raises(RequestError) as error:
        connection.fetch(make_endpoint())
    assert error.value.status_code == 429
    assert waits == [1.0, 2.0]                       # no wait after the last attempt


def test_other_http_errors_are_retried_without_waiting(waits):
    connection = make_connection([response(503), response(200)])
    assert connection.fetch(make_endpoint()) == {"result": 1}
    assert waits == []
