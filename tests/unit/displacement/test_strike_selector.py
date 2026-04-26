from datetime import date
import pytest
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.strike_selector import StrikeSelector


def _make_option(instrument, strike, dte, delta, bid_iv, ask_iv, mark_iv, oi, mark_price, underlying):
    return {
        "instrument_name": instrument,
        "option_type": "call",
        "strike": strike,
        "dte": dte,
        "delta": delta,
        "bid_iv": bid_iv,
        "ask_iv": ask_iv,
        "mark_iv": mark_iv,
        "open_interest": oi,
        "mark_price": mark_price,
        "underlying_price": underlying,
    }


CHAIN = [
    _make_option("BTC-25SEP26-70000-C", 70000.0, 153, 0.14, 0.85, 0.92, 0.87, 500, 0.0135, 78000.0),
    _make_option("BTC-25SEP26-75000-C", 75000.0, 153, 0.12, 0.86, 0.93, 0.89, 200, 0.0095, 78000.0),
    _make_option("BTC-25JUN26-70000-C", 70000.0, 61, 0.13, 0.88, 0.97, 0.92, 350, 0.0090, 78000.0),
    # Below min OI — should be filtered
    _make_option("BTC-25SEP26-65000-C", 65000.0, 153, 0.18, 0.84, 0.91, 0.87, 10, 0.0200, 78000.0),
    # DTE too short — should be filtered
    _make_option("BTC-28APR26-70000-C", 70000.0, 3, 0.15, 0.90, 0.98, 0.94, 800, 0.0080, 78000.0),
    # Delta too high — should be filtered
    _make_option("BTC-25SEP26-55000-C", 55000.0, 153, 0.45, 0.82, 0.88, 0.85, 600, 0.0350, 78000.0),
    # Spread too wide — should be filtered (> 8% relative)
    _make_option("BTC-25SEP26-80000-C", 80000.0, 153, 0.11, 0.70, 0.90, 0.80, 400, 0.0070, 78000.0),
]


class TestStrikeSelector:
    def setup_method(self):
        self.cfg = DisplacementConfig()
        self.selector = StrikeSelector(self.cfg)

    def test_returns_best_contract(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["instrument_name"] in ("BTC-25SEP26-70000-C", "BTC-25SEP26-75000-C", "BTC-25JUN26-70000-C")

    def test_filters_low_oi(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["instrument_name"] != "BTC-25SEP26-65000-C"

    def test_filters_short_dte(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["dte"] >= self.cfg.min_dte

    def test_filters_delta_too_high(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["delta"] <= self.cfg.max_delta

    def test_filters_wide_spread(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["instrument_name"] != "BTC-25SEP26-80000-C"

    def test_returns_none_when_no_qualifying_contracts(self):
        tiny_chain = [
            _make_option("BTC-28APR26-70000-C", 70000.0, 3, 0.15, 0.90, 0.98, 0.94, 800, 0.008, 78000.0),
        ]
        result = self.selector.select("BTC", tiny_chain, 78000.0)
        assert result is None

    def test_includes_profit_targets(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert "target_50pct_price" in result
        assert "target_100pct_price" in result
        assert "target_200pct_price" in result
        assert result["target_100pct_price"] > result["target_50pct_price"]

    def test_prefers_delta_closest_to_preferred(self):
        # Two contracts both qualify — prefer closest to preferred_delta=0.15
        chain = [
            _make_option("BTC-25SEP26-70000-C", 70000.0, 153, 0.14, 0.85, 0.92, 0.87, 500, 0.0135, 78000.0),
            _make_option("BTC-25SEP26-72000-C", 72000.0, 153, 0.13, 0.85, 0.92, 0.87, 500, 0.0110, 78000.0),
        ]
        result = self.selector.select("BTC", chain, 78000.0)
        # delta 0.14 is closer to preferred 0.15 than delta 0.13
        assert result["instrument_name"] == "BTC-25SEP26-70000-C"

    def test_filters_missing_bid_iv(self):
        # Contract with no bid quote (bid_iv=0) should be rejected as illiquid
        illiquid_chain = [
            _make_option("BTC-25SEP26-70000-C", 70000.0, 153, 0.14, 0.0, 0.92, 0.87, 500, 0.0135, 78000.0),  # bid_iv=0
            _make_option("BTC-25SEP26-75000-C", 75000.0, 153, 0.12, 0.86, 0.93, 0.89, 200, 0.0095, 78000.0),  # good
        ]
        result = self.selector.select("BTC", illiquid_chain, 78000.0)
        assert result is not None
        assert result["instrument_name"] == "BTC-25SEP26-75000-C"
