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


def test_a_page_of_fills_that_cannot_be_paged_past_fails_instead_of_dropping_fills(client):
    stuck = {"trades": [{"trade_id": "a", "timestamp": 5}], "has_more": True}
    fake = _FakeDeribit(stuck, stuck)
    with patch.object(deribit_private_client.requests, "get", fake):
        with pytest.raises(RuntimeError, match="cannot page past"):
            client.get_user_trades("ETH", 0, 99)


def test_orders_are_paged_by_offset_deduplicated_and_joined_with_the_open_orders(client, monkeypatch):
    monkeypatch.setattr(deribit_private_client, "_PAGE_SIZE", 2)

    def order(order_id, updated, state="filled"):
        return {"order_id": order_id, "last_update_timestamp": updated, "order_state": state}

    fake = _FakeDeribit(
        [order("1", 10), order("2", 20)],
        [order("2", 20)],                          # "2" repeats: an order arrived between the pages
        [order("2", 25, "open"), order("4", 50, "open")],  # open orders: "2" is newer than in the history
    )
    with patch.object(deribit_private_client.requests, "get", fake):
        orders = client.get_orders("ETH")

    by_id = {o["order_id"]: o for o in orders}
    assert sorted(by_id) == ["1", "2", "4"]
    assert by_id["2"]["order_state"] == "open"
    assert [call[0].rsplit("/", 1)[1] for call in fake.private_calls()] == [
        "get_order_history_by_currency", "get_order_history_by_currency", "get_open_orders_by_currency"]
    assert [call[1].get("offset") for call in fake.private_calls()[:2]] == [0, 2]


def test_network_errors_do_not_leak_the_client_secret(client):
    error = deribit_private_client.requests.ConnectionError(
        "HTTPSConnectionPool: url: /api/v2/public/auth?client_secret=secret")
    with patch.object(deribit_private_client.requests, "get", side_effect=error):
        with pytest.raises(RuntimeError) as raised:
            client.get_account_summary("BTC")

    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_a_non_json_reply_is_reported_with_its_http_status(client):
    html = MagicMock(status_code=502)
    html.json.side_effect = ValueError("not json")
    with patch.object(deribit_private_client.requests, "get", return_value=html):
        with pytest.raises(RuntimeError, match="non-JSON response \\(HTTP 502\\)"):
            client.get_account_summary("BTC")


def test_an_invalidated_token_is_replaced_once_and_the_call_repeated(client):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("public/auth"):
            return _response({"result": {"access_token": f"token-{len(calls)}", "expires_in": 900}})
        if headers["Authorization"] == "Bearer token-1":
            return _response({"error": {"code": 13009, "message": "unauthorized"}})
        return _response({"result": {"equity": 2}})

    with patch.object(deribit_private_client.requests, "get", fake_get):
        assert client.get_account_summary("BTC") == {"equity": 2}

    assert len([url for url in calls if url.endswith("public/auth")]) == 2


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
