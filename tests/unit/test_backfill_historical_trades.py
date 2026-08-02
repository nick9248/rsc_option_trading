"""
Independent review fix (Task D1 round 3, Important #4): a THIRD writer
into historical_trades -- scripts/backfill_historical_trades.py's
HistoricalBackfillService._store_trades -- never carried the block-trade
columns either. It is a live operational script
(``python -m scripts.backfill_historical_trades --months 6 --currency
BTC``), and its ``ON CONFLICT (trade_id) DO NOTHING`` means whichever
writer inserts a given trade_id first wins the race permanently -- so any
trade this script backfills first would have permanently NULLed
block_trade_id, same failure class as TradeCollector (Important #1
original) and ProspectiveCollector (Important #1 round 2 fix).

Mirrors trade_collector.py's field extraction exactly, including the
block_rfq_id int-to-VARCHAR(64) stringification.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

from scripts.backfill_historical_trades import HistoricalBackfillService


def _make_service():
    api_service = MagicMock()
    repository = MagicMock()
    cursor = MagicMock()
    # cursor.fetchone() returning a truthy row simulates "not a duplicate"
    # (INSERT ... RETURNING trade_id actually inserted).
    cursor.fetchone.return_value = ("dummy_trade_id",)

    @contextmanager
    def _db_cursor():
        yield cursor

    repository._db_cursor = _db_cursor
    service = HistoricalBackfillService(api_service=api_service, repository=repository)
    return service, cursor


class TestBackfillBlockTradeFieldWiring:
    def test_block_trade_fields_land_in_insert_params(self):
        service, cursor = _make_service()
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

        stored = service._store_trades([trade], "BTC")

        assert stored == 1
        assert cursor.execute.call_count == 1
        query, params = cursor.execute.call_args[0]

        assert "block_trade_id" in query
        assert "block_trade_leg_count" in query
        assert "combo_id" in query
        assert "block_rfq_id" in query
        assert "liquidation" in query
        assert "contracts" in query

        assert "BLOCK-282155" in params
        assert "BTC-STRD-1AUG26-63000" in params
        # wire value is an int; column is VARCHAR(64) -- must be stringified.
        assert "50510" in params
        assert 12.5 in params

    def test_regular_trade_without_block_fields_stores_nulls(self):
        service, cursor = _make_service()
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
        stored = service._store_trades([trade], "BTC")

        assert stored == 1
        _, params = cursor.execute.call_args[0]
        assert params.count(None) >= 6
