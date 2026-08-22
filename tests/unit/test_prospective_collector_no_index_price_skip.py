"""
Regression test: when both the primary index-price fetch AND the
nearest-expiry median fallback fail to produce a real price,
ProspectiveCollector._run_onchain_analysis must cleanly skip this hour's
on-chain analysis (no persisted snapshot), not crash with a generic
TypeError from GexDexCalculator's spot_price ** 2.

Follow-up to Wave H Task H-F (VRPCalculator/on_chain_analyzer.py fabrication
fixes): OnChainMetricsCalculator.nearest_expiry_median_underlying_price()
now returns None instead of a fabricated 0.0 when no instrument has a
priced underlying_price. This is the daemon caller Task H-F flagged as
off-limits (owned by the naive-datetime clock task) and asked to be
patched separately -- verifying the exact follow-up patch here.
"""
from unittest.mock import MagicMock

from coding.service.data_collection.prospective_collector import ProspectiveCollector


def _make_collector():
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.api.get_index_price.side_effect = Exception("index price fetch failed")
    collector.repo = MagicMock()
    collector._forward_harness = MagicMock()
    return collector


def test_no_index_price_and_no_fallback_price_skips_cleanly():
    """No instruments at all -> nearest_expiry_median_underlying_price()
    returns None -> the method must return (not raise), and must not
    persist anything."""
    collector = _make_collector()

    result = collector._run_onchain_analysis(
        currency="BTC", hour=None, instruments=[]
    )

    assert result is None
    collector.repo.save_onchain_snapshot.assert_not_called()


def test_no_index_price_but_fallback_succeeds_still_analyzes():
    """Sanity check on the other branch: when the fallback DOES find a
    price, the method must proceed (not treat every fetch failure as a
    skip) -- guards against an overly broad `except` swallowing the
    working case."""
    collector = _make_collector()
    instruments = [
        {
            "instrument_name": "BTC-27MAR26-70000-C",
            "open_interest": 100.0,
            "volume": 10.0,
            "volume_usd": 700_000.0,
            "mark_price": 0.05,
            "mark_iv": 60.0,
            "underlying_price": 70_000.0,
            "greeks": {"delta": 0.5, "gamma": 0.0001, "vega": 100.0, "theta": -50.0},
        },
        {
            "instrument_name": "BTC-27MAR26-70000-P",
            "open_interest": 100.0,
            "volume": 10.0,
            "volume_usd": 700_000.0,
            "mark_price": 0.05,
            "mark_iv": 60.0,
            "underlying_price": 70_000.0,
            "greeks": {"delta": -0.5, "gamma": 0.0001, "vega": 100.0, "theta": -50.0},
        },
    ]

    from datetime import datetime
    collector._run_onchain_analysis(
        currency="BTC", hour=datetime(2026, 8, 18, 12, 0, 0), instruments=instruments
    )

    collector.repo.save_onchain_snapshot.assert_called()
