"""
Task Wave-J-A: ``BlackScholesCalculator.calculate_greeks_safe`` -- the
shared, rigorously-guarded Core entry point extracted from
``OnChainAnalysisService._compute_bs_gamma`` (Task G2-A) so
``ProspectiveCollector._enrich_with_greeks`` (Fix 1) and the report/GUI path
(Fix 2) share exactly one implementation of this guard logic.

Contract: returns the full Greeks dict, or ``None`` (never a fabricated
0.0) when spot_price/mark_iv/strike/name are missing, non-positive,
unparseable, outside a sane mark_iv range, or the option has already
passed its 08:00 UTC settlement. Every guard is an explicit numeric-range
check, never bare truthiness (``not -50000`` is ``False`` in Python).
"""

from datetime import datetime, timezone

import pytest

from coding.core.analytics.black_scholes_calculator import (
    BlackScholesCalculator,
    MAX_SANE_MARK_IV_PCT,
)

_ITEM = {"strike": 50000.0, "instrument_name": "BTC-31DEC30-50000-C"}


class TestCalculateGreeksSafeGuards:
    def test_none_spot_price_returns_none(self):
        bs = BlackScholesCalculator()
        result = bs.calculate_greeks_safe(_ITEM, 60.0, None, datetime.now(timezone.utc))
        assert result is None

    def test_negative_spot_price_returns_none(self):
        bs = BlackScholesCalculator()
        result = bs.calculate_greeks_safe(_ITEM, 60.0, -95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_none_mark_iv_returns_none(self):
        bs = BlackScholesCalculator()
        result = bs.calculate_greeks_safe(_ITEM, None, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_negative_mark_iv_returns_none_not_zero(self):
        """`not -60.0` is False -- a truthiness guard would let this through."""
        bs = BlackScholesCalculator()
        result = bs.calculate_greeks_safe(_ITEM, -60.0, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_absurd_mark_iv_returns_none(self):
        bs = BlackScholesCalculator()
        result = bs.calculate_greeks_safe(_ITEM, 1e10, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_mark_iv_at_ceiling_still_computes(self):
        bs = BlackScholesCalculator()
        result = bs.calculate_greeks_safe(
            _ITEM, MAX_SANE_MARK_IV_PCT, 95000.0, datetime.now(timezone.utc),
        )
        assert result is not None

    def test_negative_strike_returns_none_not_zero(self):
        """`not -50000` is False -- a truthiness guard would let this through."""
        bs = BlackScholesCalculator()
        item = {"strike": -50000.0, "instrument_name": "BTC-31DEC30-50000-C"}
        result = bs.calculate_greeks_safe(item, 60.0, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_missing_strike_returns_none(self):
        bs = BlackScholesCalculator()
        item = {"instrument_name": "BTC-31DEC30-50000-C"}
        result = bs.calculate_greeks_safe(item, 60.0, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_missing_instrument_name_returns_none(self):
        bs = BlackScholesCalculator()
        item = {"strike": 50000.0}
        result = bs.calculate_greeks_safe(item, 60.0, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_unparseable_instrument_name_returns_none(self):
        bs = BlackScholesCalculator()
        item = {"strike": 50000.0, "instrument_name": "not-a-real-name"}
        result = bs.calculate_greeks_safe(item, 60.0, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_already_settled_expiry_returns_none(self):
        bs = BlackScholesCalculator()
        item = {"strike": 50000.0, "instrument_name": "BTC-01JAN20-50000-C"}
        result = bs.calculate_greeks_safe(item, 60.0, 95000.0, datetime.now(timezone.utc))
        assert result is None

    def test_sane_inputs_compute_normally_and_match_calculate_greeks(self):
        bs = BlackScholesCalculator()
        now_utc = datetime.now(timezone.utc)
        result = bs.calculate_greeks_safe(_ITEM, 60.0, 95000.0, now_utc)
        assert result is not None
        assert result["gamma"] > 0

        parsed = bs.parse_instrument_name(_ITEM["instrument_name"])
        now_utc_naive = now_utc.replace(tzinfo=None)
        tte = bs.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
        expected = bs.calculate_greeks(
            spot_price=95000.0, strike_price=50000.0, time_to_expiry=tte,
            implied_volatility=0.60, option_type="call",
        )
        assert result == expected

    def test_naive_now_utc_is_accepted_directly(self):
        """A caller may pass an already-naive-UTC datetime (matches
        ProspectiveCollector's own convention) -- must not crash on the
        tzinfo-stripping step."""
        bs = BlackScholesCalculator()
        naive_now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = bs.calculate_greeks_safe(_ITEM, 60.0, 95000.0, naive_now)
        assert result is not None
