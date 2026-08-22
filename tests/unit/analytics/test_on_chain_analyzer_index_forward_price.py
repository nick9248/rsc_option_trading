"""
bugfix_spec.md Item 7 acceptance tests: index price vs. per-expiry forward
price separation on ``OnChainMetricsCalculator``.

Mirrors the spec's T7.1-T7.3 (section 7.5), adapted to this codebase's
post-refactor names (``OnChainMetricsCalculator``, not ``OnChainAnalyzer``;
``GexDexCalculator``/``VolatilitySurfaceCalculator`` already take
``spot_price`` positionally/by keyword, unchanged by this item).
"""

import math

import pytest

from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.on_chain_analyzer import OnChainMetricsCalculator
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator


def _instrument(name, volume, underlying_price):
    return {
        "instrument_name": name,
        "volume": volume,
        "underlying_price": underlying_price,
        "open_interest": 0,
        "mark_price": 0.0,
        "mark_iv": None,
    }


def test_index_and_forwards_are_separated():
    """T7.1: index_price is set explicitly, never picked up from the book's
    highest-volume instrument (which is what forward_price_by_expiration is
    for, per-expiry)."""
    book = [
        _instrument("BTC-26JUL26-64000-C", volume=10, underlying_price=64_200.0),
        _instrument("BTC-25JUN27-64000-C", volume=999, underlying_price=66_700.0),  # wins volume
    ]
    analyzer = OnChainMetricsCalculator(book, "BTC")
    analyzer.set_index_price(64_228.0)
    analyzer.parse_instruments()

    assert analyzer.index_price == pytest.approx(64_228.0)  # NOT 66_700 (the old bug)
    assert analyzer.forward_price_by_expiration["26JUL26"] == pytest.approx(64_200.0)
    assert analyzer.forward_price_by_expiration["25JUN27"] == pytest.approx(66_700.0)


def test_gex_is_anchored_on_the_index():
    """T7.2 (hand-computed): GEX's S^2 term must use the index, not
    whichever expiry's future happened to win the book's volume race."""
    book = [
        _instrument("BTC-26JUL26-64000-C", volume=10, underlying_price=64_200.0),
        _instrument("BTC-25JUN27-64000-C", volume=999, underlying_price=66_700.0),
    ]
    analyzer = OnChainMetricsCalculator(book, "BTC")
    analyzer.set_index_price(64_228.0)
    analyzer.parse_instruments()

    one_call = {
        "strike": 64_000.0, "option_type": "C",
        "gamma": 0.00002, "delta": 0.5, "open_interest": 100.0,
    }
    # index 64,228: 0.002 * 64228^2 * 0.01 = 82,504.72
    # future  66,700 (the OLD bug's pick): 0.002 * 66700^2 * 0.01 = 88,977.80 (+7.85%)
    calc = GexDexCalculator([one_call], spot_price=analyzer.index_price)
    result = calc.calculate()
    assert result.total_net_gex == pytest.approx(82_504.72, rel=1e-6)


def test_moneyness_uses_the_per_expiry_forward():
    """T7.3: ATM/moneyness bucketing must use THIS expiry's own forward,
    not the index or another expiry's forward -- 4.94% distance to the
    25JUN27 forward (66,700) buckets ATM (<=5%); the same strike is 8.99%
    from the index (64,228), which would bucket near_otm -- the old bug."""
    insts_25jun27 = [
        {"strike": 70_000.0, "option_type": "C", "open_interest": 10.0},
    ]
    forward = 66_700.0
    vsc = VolatilitySurfaceCalculator(insts_25jun27, forward, "25JUN27")
    buckets = vsc._calculate_pc_by_moneyness()
    assert buckets["atm"]["call_oi"] == 10.0
    assert buckets["near_otm"]["call_oi"] == 0.0


class TestAnalyzeExpirationUsesForwardPrice:
    """analyze_expiration must anchor moneyness/max-pain-distance/
    support-resistance on this expiry's own forward, not the index --
    even when the index and forward differ materially."""

    def _book(self):
        return [
            _instrument("BTC-26JUL26-64000-C", volume=10, underlying_price=64_200.0),
            _instrument("BTC-25JUN27-70000-C", volume=999, underlying_price=66_700.0),
        ]

    def test_underlying_price_field_is_the_forward_not_the_index(self):
        analyzer = OnChainMetricsCalculator(self._book(), "BTC")
        analyzer.set_index_price(64_228.0)
        analyzer.parse_instruments()

        result = analyzer.analyze_expiration("25JUN27")
        assert result.underlying_price == pytest.approx(66_700.0)

        result_other = analyzer.analyze_expiration("26JUL26")
        assert result_other.underlying_price == pytest.approx(64_200.0)


class TestEdgeCases:
    """Section 7.4 edge cases."""

    def test_expiry_with_no_priced_instrument_falls_back_to_index(self, caplog):
        book = [
            {
                "instrument_name": "BTC-26JUL26-64000-C",
                "volume": 10, "underlying_price": None,
                "open_interest": 0, "mark_price": 0.0, "mark_iv": None,
            },
        ]
        analyzer = OnChainMetricsCalculator(book, "BTC")
        analyzer.set_index_price(64_228.0)
        analyzer.parse_instruments()

        assert analyzer.forward_price_by_expiration["26JUL26"] is None

        result = analyzer.analyze_expiration("26JUL26")
        assert result.underlying_price == pytest.approx(64_228.0)
        assert any(
            "No forward price" in r.message for r in caplog.records
        )

    def test_index_price_zero_does_not_crash_moneyness(self):
        """index price 0 (never set / fetch failed and fallback also 0):
        GEX would be 0, moneyness must not KeyError."""
        book = [_instrument("BTC-26JUL26-64000-C", volume=10, underlying_price=None)]
        analyzer = OnChainMetricsCalculator(book, "BTC")
        analyzer.parse_instruments()  # index_price left at default 0.0

        result = analyzer.analyze_expiration("26JUL26")
        assert result.underlying_price == 0.0
        # must not raise -- moneyness/support-resistance ran to completion
        assert result.moneyness is not None


class TestNearestExpiryMedianFallback:
    """The get_index_price-failure fallback: median underlying_price of the
    NEAREST expiry, never the old global highest-volume pick."""

    def test_uses_median_of_nearest_expiry_not_global_volume_winner(self):
        from datetime import datetime, timedelta, timezone

        near = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%d%b%y").upper()
        far = (datetime.now(timezone.utc) + timedelta(days=200)).strftime("%d%b%y").upper()

        book = [
            _instrument(f"BTC-{near}-64000-C", volume=1, underlying_price=64_000.0),
            _instrument(f"BTC-{near}-65000-C", volume=1, underlying_price=64_100.0),
            _instrument(f"BTC-{near}-66000-C", volume=1, underlying_price=64_200.0),
            # far expiry wins the (irrelevant, old-bug) volume race
            _instrument(f"BTC-{far}-70000-C", volume=999, underlying_price=70_000.0),
        ]
        analyzer = OnChainMetricsCalculator(book, "BTC")

        fallback = analyzer.nearest_expiry_median_underlying_price()
        assert fallback == pytest.approx(64_100.0)  # median of the NEAR expiry, not 70,000

    def test_no_priced_instruments_returns_none(self):
        """Wave H Task H-F, Fix 3: was 0.0 (indistinguishable from a real
        $0 price once persisted); now None, so callers can detect "no
        price at all" and refuse to persist a poisoned snapshot."""
        analyzer = OnChainMetricsCalculator([], "BTC")
        assert analyzer.nearest_expiry_median_underlying_price() is None


def test_underlying_price_property_is_deprecated_alias_for_index_price():
    analyzer = OnChainMetricsCalculator([], "BTC")
    analyzer.set_index_price(50_000.0)
    with pytest.warns(DeprecationWarning):
        assert analyzer.underlying_price == 50_000.0

    with pytest.raises(AttributeError):
        analyzer.underlying_price = 60_000.0
