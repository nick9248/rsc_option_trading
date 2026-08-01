"""
Independent review fix (Task D1 round 2): ProspectiveCollector._store_trade
is a SECOND writer into historical_trades (TradeCollector._store_trades is
the first) -- both run in the same daemon process and race on the same
unique constraint. Before this fix, ProspectiveCollector's INSERT used the
old 15-column list, so any trade this collector wins the race on
permanently NULLs block_trade_id (unbackfillable by construction --
exactly the failure mode institutional_metrics_spec.md section 9 /
Migration M2 exists to prevent).

Mirrors trade_collector.py's field extraction (block_rfq_id stringified;
missing/absent fields default to None, never KeyError).
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_collector():
    from coding.service.data_collection.prospective_collector import ProspectiveCollector

    with patch("coding.service.data_collection.prospective_collector.DeribitApiService"), \
         patch("coding.service.data_collection.prospective_collector.DatabaseRepository"):
        collector = ProspectiveCollector()

    repo = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    repo._get_connection.return_value = conn
    conn.cursor.return_value = cursor
    collector.repo = repo
    return collector, cursor


class TestProspectiveCollectorBlockTradeFieldWiring:
    def test_block_trade_fields_land_in_insert_params(self):
        collector, cursor = _make_collector()
        trade = {
            "trade_id": "439435597",
            "trade_seq": 212,
            "timestamp": 1785546525278,
            "instrument_name": "BTC-1AUG26-63000-C",
            "price": 0.0008,
            "amount": 12.5,
            "direction": "buy",
            "iv": 13.35,
            "mark_price": 0.00093647,
            "index_price": 62892.69,
            "block_trade_id": "BLOCK-282155",
            "block_trade_leg_count": 2,
            "combo_id": "BTC-STRD-1AUG26-63000",
            "block_rfq_id": 50510,
            "contracts": 12.5,
        }

        collector._store_trade(trade, "BTC", hour=MagicMock())

        assert cursor.execute.call_count == 1
        query, params = cursor.execute.call_args[0]

        assert "block_trade_id" in query
        assert "block_trade_leg_count" in query
        assert "combo_id" in query
        assert "block_rfq_id" in query
        assert "liquidation" in query
        assert "contracts" in query

        assert "BLOCK-282155" in params
        assert 2 in params
        assert "BTC-STRD-1AUG26-63000" in params
        # wire value is an int; column is VARCHAR(64) -- must be stringified,
        # matching trade_collector.py's behavior exactly.
        assert "50510" in params
        assert 12.5 in params

    def test_regular_trade_without_block_fields_stores_nulls(self):
        collector, cursor = _make_collector()
        trade = {
            "trade_id": "1",
            "trade_seq": 1,
            "timestamp": 1785546525278,
            "instrument_name": "BTC-1AUG26-63000-C",
            "price": 0.05,
            "amount": 5.0,
            "direction": "buy",
            "iv": 65.0,
            "mark_price": 0.051,
            "index_price": 90000,
        }

        # must not raise (no KeyError on the new fields)
        collector._store_trade(trade, "BTC", hour=MagicMock())

        assert cursor.execute.call_count == 1
        query, params = cursor.execute.call_args[0]
        assert "block_trade_id" in query
        # the 6 new columns should all be None in the params tuple
        assert params.count(None) >= 6
