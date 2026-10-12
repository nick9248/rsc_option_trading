"""Tests for AccountCollector: what is stored, from where, and how failures are contained."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from coding.service.data_collection.account_collector import AccountCollector

HOUR = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
NOW_MS = 1_791_800_000_000


def _collector(configured=True):
    client = MagicMock()
    client.is_configured = configured
    repository = MagicMock()
    repository.get_latest_user_trade_ms.return_value = None
    return AccountCollector(client=client, repository=repository), client, repository


def test_everything_is_stored_for_the_currency():
    collector, client, repository = _collector()
    client.get_account_summary.return_value = {"equity": 1.5}
    client.get_positions.return_value = [{"instrument_name": "ETH-1JAN27-3000-C"}]
    client.get_user_trades.return_value = [{"trade_id": "t"}]
    client.get_orders.return_value = [{"order_id": "o"}]
    client.get_settlements.return_value = [{"type": "delivery"}]
    repository.save_account_snapshot.return_value = 1
    repository.save_position_snapshots.return_value = 1
    repository.save_user_trades.return_value = 1
    repository.save_orders.return_value = 1
    repository.save_settlements.return_value = 1

    written = collector.collect("ETH", HOUR, NOW_MS)

    assert written == {"account": 1, "positions": 1, "trades": 1, "orders": 1, "settlements": 1}
    repository.save_account_snapshot.assert_called_once_with("ETH", HOUR, {"equity": 1.5})
    repository.save_position_snapshots.assert_called_once_with("ETH", HOUR, [{"instrument_name": "ETH-1JAN27-3000-C"}])
    repository.save_user_trades.assert_called_once_with("ETH", [{"trade_id": "t"}])


def test_fills_are_fetched_from_the_newest_stored_one_or_from_the_beginning():
    collector, client, repository = _collector()
    collector.collect("BTC", HOUR, NOW_MS)
    client.get_user_trades.assert_called_with("BTC", 0, NOW_MS)  # first run: all history

    repository.get_latest_user_trade_ms.return_value = 1_700_000_000_000
    collector.collect("BTC", HOUR, NOW_MS)
    client.get_user_trades.assert_called_with("BTC", 1_700_000_000_000, NOW_MS)


def test_nothing_is_called_without_credentials():
    collector, client, repository = _collector(configured=False)

    written = collector.collect("BTC", HOUR, NOW_MS)

    assert written == {"account": 0, "positions": 0, "trades": 0, "orders": 0, "settlements": 0}
    client.get_account_summary.assert_not_called()
    repository.save_account_snapshot.assert_not_called()


def test_a_failing_step_does_not_stop_the_other_steps():
    collector, client, repository = _collector()
    client.get_account_summary.side_effect = RuntimeError("Deribit down")
    repository.save_orders.return_value = 3

    written = collector.collect("BTC", HOUR, NOW_MS)

    assert written["account"] == 0
    assert written["orders"] == 3
    repository.save_position_snapshots.assert_called_once()
    repository.save_settlements.assert_called_once()
