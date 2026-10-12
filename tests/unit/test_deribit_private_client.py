"""Tests for DeribitPrivateClient: read-only guard, token handling and paging."""
from unittest.mock import MagicMock, patch

import pytest

from coding.service.deribit import deribit_private_client
from coding.service.deribit.deribit_private_client import DeribitPrivateClient


def _response(body):
    response = MagicMock()
    response.json.return_value = body
    return response


class _FakeDeribit:
    """Stands in for requests.get: answers public/auth and queued private calls."""

    def __init__(self, *private_results):
        self.private_results = list(private_results)
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        if url.endswith("public/auth"):
            return _response({"result": {"access_token": "token-1", "expires_in": 900}})
        return _response({"result": self.private_results.pop(0)})

    def private_calls(self):
        return [call for call in self.calls if "/private/" in call[0]]


@pytest.fixture
def client():
    return DeribitPrivateClient(client_id="id", client_secret="secret")


def test_not_configured_without_credentials(monkeypatch):
    monkeypatch.delenv("client_id", raising=False)
    monkeypatch.delenv("deribit_key", raising=False)
    assert DeribitPrivateClient(client_id=None, client_secret=None).is_configured is False


def test_configured_from_environment(monkeypatch):
    monkeypatch.setenv("client_id", "abc")
    monkeypatch.setenv("deribit_key", "def")
    assert DeribitPrivateClient().is_configured is True


@pytest.mark.parametrize("method", [
    "private/buy", "private/sell", "private/edit", "private/cancel", "private/cancel_all",
    "private/withdraw", "private/close_position", "public/get_instruments", "private/set_heartbeat",
])
def test_only_private_get_methods_are_allowed(client, method):
    with patch.object(deribit_private_client.requests, "get") as http_get:
        with pytest.raises(ValueError, match="read-only"):
            client._call(method, {})
    http_get.assert_not_called()


def test_unconfigured_client_makes_no_request(monkeypatch):
    monkeypatch.delenv("client_id", raising=False)
    monkeypatch.delenv("deribit_key", raising=False)
    client = DeribitPrivateClient(client_id=None, client_secret=None)
    with patch.object(deribit_private_client.requests, "get") as http_get:
        with pytest.raises(RuntimeError, match="not configured"):
            client.get_account_summary("BTC")
    http_get.assert_not_called()


def test_token_is_fetched_once_and_sent_as_bearer(client):
    fake = _FakeDeribit({"equity": 1}, [])
    with patch.object(deribit_private_client.requests, "get", fake):
        client.get_account_summary("BTC")
        client.get_positions("BTC")

    auth_calls = [call for call in fake.calls if call[0].endswith("public/auth")]
    assert len(auth_calls) == 1
    assert auth_calls[0][1]["grant_type"] == "client_credentials"
    assert all(call[2] == {"Authorization": "Bearer token-1"} for call in fake.private_calls())


def test_expired_token_is_refreshed(client):
    fake = _FakeDeribit({"equity": 1}, {"equity": 2})
    with patch.object(deribit_private_client.requests, "get", fake):
        client.get_account_summary("BTC")
        client._token_expires_at = 0  # expired
        client.get_account_summary("BTC")

    assert len([call for call in fake.calls if call[0].endswith("public/auth")]) == 2


def test_api_error_is_raised_with_the_deribit_message(client):
    def failing(url, params=None, headers=None, timeout=None):
        if url.endswith("public/auth"):
            return _response({"result": {"access_token": "t", "expires_in": 900}})
        return _response({"error": {"code": -32602, "message": "Invalid params"}})

    with patch.object(deribit_private_client.requests, "get", failing):
        with pytest.raises(RuntimeError, match="Invalid params"):
            client.get_positions("BTC")


def test_trades_are_paged_by_timestamp_without_duplicates(client):
    page_1 = {"trades": [{"trade_id": "a", "timestamp": 1}, {"trade_id": "b", "timestamp": 2}], "has_more": True}
    page_2 = {"trades": [{"trade_id": "b", "timestamp": 2}, {"trade_id": "c", "timestamp": 3}], "has_more": False}
    fake = _FakeDeribit(page_1, page_2)
    with patch.object(deribit_private_client.requests, "get", fake):
        trades = client.get_user_trades("ETH", 0, 99)

    assert [trade["trade_id"] for trade in trades] == ["a", "b", "c"]
    second_call_params = fake.private_calls()[1][1]
    assert second_call_params["start_timestamp"] == 2  # resumes at the newest timestamp seen
    assert second_call_params["historical"] == "true"


def test_trade_paging_stops_when_a_page_adds_nothing_new(client):
    stuck = {"trades": [{"trade_id": "a", "timestamp": 5}], "has_more": True}
    fake = _FakeDeribit(stuck, stuck)
    with patch.object(deribit_private_client.requests, "get", fake):
        trades = client.get_user_trades("ETH", 0, 99)

    assert len(trades) == 1
    assert len(fake.private_calls()) == 2


def test_orders_are_paged_by_offset(client, monkeypatch):
    monkeypatch.setattr(deribit_private_client, "_PAGE_SIZE", 2)
    fake = _FakeDeribit([{"order_id": "1"}, {"order_id": "2"}], [{"order_id": "3"}])
    with patch.object(deribit_private_client.requests, "get", fake):
        orders = client.get_orders("ETH")

    assert [order["order_id"] for order in orders] == ["1", "2", "3"]
    assert [call[1]["offset"] for call in fake.private_calls()] == [0, 2]


def test_settlements_follow_the_continuation_token_and_stop_at_none(client):
    fake = _FakeDeribit(
        {"settlements": [{"id": 1}], "continuation": "next-page"},
        {"settlements": [{"id": 2}], "continuation": "none"},
    )
    with patch.object(deribit_private_client.requests, "get", fake):
        settlements = client.get_settlements("ETH")

    assert settlements == [{"id": 1}, {"id": 2}]
    assert len(fake.private_calls()) == 2
    assert fake.private_calls()[1][1]["continuation"] == "next-page"
