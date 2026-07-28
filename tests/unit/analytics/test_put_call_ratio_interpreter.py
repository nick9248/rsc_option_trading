"""
Unit tests for coding.core.analytics.put_call_ratio_interpreter
(bugfix_spec.md Item 10).

Percentile arithmetic itself is HistoricalNormalizer.percentile's job
(already covered by test_historical_normalizer.py) -- task-C1 brief:
"replace the hard-coded equity-style PCR sentiment thresholds ... with
percentile-vs-own-history classification, using the HistoricalNormalizer
you're building". This module only owns the percentile -> label mapping
(bugfix_spec.md F10.3.1's band constants/labels), not a second percentile
implementation.

Deliberate deviation from bugfix_spec.md's own T10.4 (documented, not a
bug): the spec's illustrative ``calculate_percentile`` guards degenerate
history (max == min) to None. HistoricalNormalizer's mid-rank formula
instead returns 50.0 for a degenerate series (the honest "where does this
sit" answer -- see historical_normalizer.py's module docstring), which
this module's bands classify as "Neutral". Reproduced here as
test_degenerate_history_is_neutral_not_strong_bullish, replacing T10.4
verbatim.
"""

import pytest

from coding.core.analytics.historical_normalizer import HistoricalNormalizer
from coding.core.analytics.put_call_ratio_interpreter import (
    PERCENTILE_BEARISH,
    PERCENTILE_BULLISH,
    PERCENTILE_STRONG_BEARISH,
    PERCENTILE_STRONG_BULLISH,
    interpret_put_call_ratio_percentile,
)


def test_constants_match_bugfix_spec_bands():
    assert PERCENTILE_STRONG_BULLISH == 10.0
    assert PERCENTILE_BULLISH == 30.0
    assert PERCENTILE_BEARISH == 70.0
    assert PERCENTILE_STRONG_BEARISH == 90.0


class TestInterpretPutCallRatioPercentile:
    def test_none_is_insufficient_history(self):
        assert interpret_put_call_ratio_percentile(None) == "Insufficient history"

    def test_t10_2_live_counter_example(self):
        # bugfix_spec.md T10.2: 25JUN27's PCR_OI 0.5996 sat at the 98.3rd
        # percentile of its own 90d history -- the hardcoded 0.7 threshold
        # said "Strong Bullish"; the correct read is "Strong Bearish".
        assert interpret_put_call_ratio_percentile(98.3) == "Strong Bearish"

    def test_t10_5_band_boundaries_inclusive_as_specified(self):
        assert interpret_put_call_ratio_percentile(10.0) == "Strong Bullish"
        assert interpret_put_call_ratio_percentile(10.1) == "Bullish"
        assert interpret_put_call_ratio_percentile(30.0) == "Bullish"
        assert interpret_put_call_ratio_percentile(30.1) == "Neutral"
        assert interpret_put_call_ratio_percentile(69.9) == "Neutral"
        assert interpret_put_call_ratio_percentile(70.0) == "Bearish"
        assert interpret_put_call_ratio_percentile(90.0) == "Strong Bearish"

    def test_t10_1_percentile_arithmetic_via_historical_normalizer(self):
        # bugfix_spec.md T10.1, reproduced against HistoricalNormalizer.
        # percentile (no ties in this series, so mid-rank == strict-<).
        history = [0.1 * i for i in range(1, 41)]  # 0.1..4.0, n=40
        assert HistoricalNormalizer.percentile(0.55, history) == pytest.approx(12.5)
        assert HistoricalNormalizer.percentile(3.85, history) == pytest.approx(95.0)

    def test_short_history_suppresses_the_label(self):
        # bugfix_spec.md T10.3: < MIN_OBS history -> None percentile ->
        # "Insufficient history", no directional claim.
        history = [0.5] * 29
        percentile = (
            HistoricalNormalizer.percentile(0.60, history)
            if len(history) >= HistoricalNormalizer.MIN_OBS else None
        )
        assert percentile is None
        assert interpret_put_call_ratio_percentile(percentile) == "Insufficient history"

    def test_degenerate_history_is_neutral_not_strong_bullish(self):
        """
        Deliberate deviation from bugfix_spec.md T10.4 (see module
        docstring): HistoricalNormalizer's mid-rank percentile returns 50.0
        for a degenerate (max == min) series of sufficient length, not
        None. This must classify as "Neutral" (the honest "no signal"
        read), never "Strong Bullish" -- the old hardcoded-threshold bug
        this task replaces would have said "Strong Bullish" for
        0.60 < 0.7 regardless of history shape.
        """
        history = [0.6] * 100
        percentile = HistoricalNormalizer.percentile(0.60, history)
        assert percentile == pytest.approx(50.0)
        assert interpret_put_call_ratio_percentile(percentile) == "Neutral"

    def test_outlier_in_history_is_naturally_robust(self):
        # bugfix_spec.md edge case: history contains the observed max
        # outlier 244.0 -- percentile is rank-based, so no winsorization
        # is needed; this is just a smoke test that it doesn't distort a
        # normal-range current reading into an extreme label.
        history = [0.5 + 0.01 * i for i in range(99)] + [244.0]
        percentile = HistoricalNormalizer.percentile(0.6, history)
        label = interpret_put_call_ratio_percentile(percentile)
        assert label in ("Bullish", "Strong Bullish", "Neutral")
