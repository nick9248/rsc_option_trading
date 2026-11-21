"""ChainSourceService: stored snapshot when fresh enough, live otherwise; the age check and the reason of a fallback."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.service.on_chain.chain_source import (
    CHAIN_SOURCE_LIVE, CHAIN_SOURCE_STORED, ChainSourceService)

NOW = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
LIVE_ROWS = [{"instrument_name": "BTC-27NOV26-58000-C", "mark_iv": 59.0}]


def stored_row(**overrides):
    return {"instrument_name": "BTC-27NOV26-58000-C", "expiration": "27NOV26", "strike": 58000.0, "option_type": "C",
            "open_interest": 12.0, "mark_iv": 59.0, "underlying_price": 57900.0, "mark_price": 0.05, "volume": 3.0,
            "volume_usd": 170000.0, "bid_price": 0.049, "ask_price": 0.051, **overrides}


def make(snapshot, index=(57850.0, NOW - timedelta(minutes=29)), mode=CHAIN_SOURCE_STORED, with_repository=True):
    api = MagicMock()
    api.get_book_summary.return_value = LIVE_ROWS
    repository = MagicMock()
    repository.get_option_chain_snapshot_at_or_before.return_value = snapshot
    repository.get_index_price_at_or_before.return_value = index
    return ChainSourceService(api, repository if with_repository else None, mode=mode), api, repository


def test_a_fresh_stored_snapshot_is_used_with_its_index_price_and_no_api_call():
    captured = NOW - timedelta(minutes=29)
    service, api, repository = make((captured, [stored_row()]))

    chain = service.load("BTC", NOW)

    assert chain.source == CHAIN_SOURCE_STORED and chain.age_minutes == pytest.approx(29.0)
    assert chain.index_price == 57850.0 and chain.captured_at == captured
    assert chain.instruments[0]["bid_price"] == 0.049 and chain.instruments[0]["volume"] == 3.0
    assert "expiration" not in chain.instruments[0]                        # book summary shape only
    assert "29 min old" in chain.description
    api.get_book_summary.assert_not_called()
    repository.get_index_price_at_or_before.assert_called_once_with("BTC", captured)   # the index of the chain's own instant


def test_a_snapshot_older_than_the_limit_falls_back_to_live_and_says_why():
    service, api, _ = make((NOW - timedelta(minutes=83), [stored_row()]))

    chain = service.load("BTC", NOW)

    assert chain.source == CHAIN_SOURCE_LIVE and chain.instruments == LIVE_ROWS and chain.index_price is None
    assert "83 min old, limit 45 min" in chain.description
    api.get_book_summary.assert_called_once_with(currency="BTC", kind="option")


def test_exactly_at_the_limit_is_still_used():
    service, _, _ = make((NOW - timedelta(minutes=45), [stored_row()]))
    assert service.load("BTC", NOW).source == CHAIN_SOURCE_STORED


def test_no_stored_snapshot_falls_back_to_live():
    service, _, _ = make(None)
    chain = service.load("BTC", NOW)
    assert chain.source == CHAIN_SOURCE_LIVE and "no stored snapshot" in chain.description


def test_a_missing_index_price_falls_back_to_live_instead_of_inventing_one():
    service, _, _ = make((NOW - timedelta(minutes=10), [stored_row()]), index=None)
    chain = service.load("BTC", NOW)
    assert chain.source == CHAIN_SOURCE_LIVE and "no index price" in chain.description


def test_live_mode_never_reads_the_database():
    service, api, repository = make((NOW, [stored_row()]), mode=CHAIN_SOURCE_LIVE)
    chain = service.load("BTC", NOW)
    assert chain.source == CHAIN_SOURCE_LIVE and "chosen" in chain.description
    repository.get_option_chain_snapshot_at_or_before.assert_not_called()


def test_without_a_database_stored_mode_uses_live():
    service, _, _ = make(None, with_repository=False)
    assert service.load("BTC", NOW).source == CHAIN_SOURCE_LIVE


def test_an_index_price_far_older_than_the_snapshot_is_not_used():
    captured = NOW - timedelta(minutes=10)
    service, _, _ = make((captured, [stored_row()]), index=(57000.0, captured - timedelta(hours=3)))
    chain = service.load("BTC", NOW)
    assert chain.source == CHAIN_SOURCE_LIVE and "index price" in chain.description and "180 min older" in chain.description


def test_a_database_error_falls_back_to_live_with_the_error_as_reason():
    service, _, repository = make(None)
    repository.get_option_chain_snapshot_at_or_before.side_effect = RuntimeError("connection refused")
    chain = service.load("BTC", NOW)
    assert chain.source == CHAIN_SOURCE_LIVE and "connection refused" in chain.description


def test_a_snapshot_stamped_in_the_future_is_not_trusted():
    service, _, _ = make((NOW + timedelta(minutes=30), [stored_row()]))
    chain = service.load("BTC", NOW)
    assert chain.source == CHAIN_SOURCE_LIVE and "in the future" in chain.description


def test_live_without_an_api_is_a_clear_error():
    with pytest.raises(RuntimeError, match="Deribit API"):
        ChainSourceService(None, None).load("BTC", NOW)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        ChainSourceService(MagicMock(), MagicMock(), mode="cache")
