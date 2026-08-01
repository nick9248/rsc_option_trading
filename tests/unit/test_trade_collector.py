"""
Unit tests for TradeCollector's block-trade field wiring.

institutional_metrics_spec.md section 9 / Migration M2 (Task D1):
historical_trades gained block_trade_id, block_trade_leg_count, combo_id,
block_rfq_id, liquidation, contracts. TradeCollector._store_trades must
read these off the raw API trade dict and include them in both trade_data
and the INSERT column list so future-collected trades carry them.

Live-verified 2026-08-02 (see task-D1-report.md): a block-tagged trade from
get_last_trades_by_currency_and_time looks like:
    {"block_trade_id": "BLOCK-282155", "block_trade_leg_count": 2,
     "combo_id": "BTC-STRD-1AUG26-63000", "block_rfq_id": 50510,
     "contracts": 12.5, ...}
block_rfq_id is an int on the wire; the column is VARCHAR(64), so it must
be stringified going in (matching how combo_id/block_trade_id, already
strings, pass through unchanged). liquidation was not observed live in the
verification scan (it only appears on forced-liquidation trades) so it
must default to None/absent without raising.
"""

from unittest.mock import MagicMock

from coding.service.data_collection.trade_collector import TradeCollector


def _make_collector():
    api_service = MagicMock()
    repository = MagicMock()
    # execute_query returns a truthy row for every call by default,
    # simulating "not a duplicate" (INSERT ... RETURNING trade_id).
    repository.execute_query.return_value = [("dummy_trade_id",)]
    return TradeCollector(api_service=api_service, repository=repository), repository


class TestBlockTradeFieldWiring:
    def test_block_trade_fields_land_in_insert_params(self):
        collector, repository = _make_collector()
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

        stored = collector._store_trades([trade], "BTC")

        assert stored == 1
        assert repository.execute_query.call_count == 1
        query, params = repository.execute_query.call_args[0]

        assert "block_trade_id" in query
        assert "block_trade_leg_count" in query
        assert "combo_id" in query
        assert "block_rfq_id" in query
        assert "liquidation" in query
        assert "contracts" in query

        assert params["block_trade_id"] == "BLOCK-282155"
        assert params["block_trade_leg_count"] == 2
        assert params["combo_id"] == "BTC-STRD-1AUG26-63000"
        # wire value is an int; column is VARCHAR(64) -- must be stringified.
        assert params["block_rfq_id"] == "50510"
        assert params["contracts"] == 12.5
        # not present on the wire for a non-liquidation trade -- must not raise,
        # and must be explicitly None (not silently dropped from params).
        assert params["liquidation"] is None

    def test_regular_trade_without_block_fields_stores_nulls(self):
        """The overwhelming majority of trades carry none of these fields --
        the INSERT must still succeed with explicit NULLs, not KeyError."""
        collector, repository = _make_collector()
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

        stored = collector._store_trades([trade], "BTC")

        assert stored == 1
        _, params = repository.execute_query.call_args[0]
        assert params["block_trade_id"] is None
        assert params["block_trade_leg_count"] is None
        assert params["combo_id"] is None
        assert params["block_rfq_id"] is None
        assert params["liquidation"] is None
        assert params["contracts"] is None

    def test_liquidation_field_passed_through_when_present(self):
        collector, repository = _make_collector()
        trade = {
            "trade_id": "2",
            "trade_seq": 2,
            "timestamp": 1785546525278,
            "instrument_name": "BTC-1AUG26-63000-C",
            "price": 0.05,
            "amount": 5.0,
            "direction": "sell",
            "iv": 65.0,
            "mark_price": 0.051,
            "index_price": 90000,
            "liquidation": "M",
        }

        collector._store_trades([trade], "BTC")

        _, params = repository.execute_query.call_args[0]
        assert params["liquidation"] == "M"
