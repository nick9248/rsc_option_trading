"""
Unit tests for coding.core.analytics.thresholds.

code_quality_review.md M4: on_chain_analyzer.calculate_put_call_ratio and
volatility_surface_calculator._calculate_pc_by_moneyness independently
interpreted the identical 0.7/1.0/1.3 P/C-ratio boundaries with two
different label vocabularies. interpret_put_call_ratio() unifies both call
sites onto one shared function with named constants.
"""

import pytest

from coding.core.analytics.thresholds import (
    PC_RATIO_BEARISH_THRESHOLD,
    PC_RATIO_NEUTRAL_THRESHOLD,
    PC_RATIO_STRONG_BULLISH_THRESHOLD,
    interpret_put_call_ratio,
)


def test_constants_match_documented_boundaries():
    assert PC_RATIO_STRONG_BULLISH_THRESHOLD == 0.7
    assert PC_RATIO_NEUTRAL_THRESHOLD == 1.0
    assert PC_RATIO_BEARISH_THRESHOLD == 1.3


class TestInterpretPutCallRatio:
    def test_below_strong_bullish_threshold(self):
        assert interpret_put_call_ratio(0.5) == "Strong Bullish"

    def test_between_strong_bullish_and_neutral(self):
        assert interpret_put_call_ratio(0.85) == "Bullish"

    def test_exact_neutral_boundary(self):
        assert interpret_put_call_ratio(1.0) == "Neutral"

    def test_between_neutral_and_bearish(self):
        assert interpret_put_call_ratio(1.1) == "Bearish"

    def test_at_or_above_bearish_threshold(self):
        assert interpret_put_call_ratio(1.5) == "Strong Bearish"

    def test_zero_ratio_is_strong_bullish(self):
        assert interpret_put_call_ratio(0.0) == "Strong Bullish"

    def test_infinite_ratio_is_na(self):
        """call_oi == 0, put_oi > 0 -- undefined ratio, not a directional claim."""
        assert interpret_put_call_ratio(float("inf")) == "N/A"

    def test_none_ratio_is_na(self):
        assert interpret_put_call_ratio(None) == "N/A"

    @pytest.mark.parametrize(
        "ratio,expected",
        [
            (0.69, "Strong Bullish"),
            (0.70, "Bullish"),
            (0.99, "Bullish"),
            (1.00, "Neutral"),
            (1.01, "Bearish"),
            (1.29, "Bearish"),
            (1.30, "Strong Bearish"),
        ],
    )
    def test_boundary_values(self, ratio, expected):
        assert interpret_put_call_ratio(ratio) == expected
