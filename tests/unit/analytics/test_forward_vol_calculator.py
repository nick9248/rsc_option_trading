"""
Unit tests for MarketWideCalculator.calculate_forward_vol_curve
(institutional_metrics_spec.md section 8, Task C9).

Pure function over already-computed per-expiry (atm_iv_pct, dte_days) --
degenerate cases are enumerated FIRST, per the task brief's "gate
exhaustiveness" lesson (repeated fix rounds across this campaign came from
under-enumerated edge cases), before the acceptance-test formula checks.
"""

import math

import pytest

from coding.core.analytics.market_wide_calculator import MarketWideCalculator


def _curve(atm_by_expiry):
    return MarketWideCalculator.calculate_forward_vol_curve(atm_by_expiry)


class TestDegenerateCases:
    """Enumerated before any formula/flag test, per the task brief."""

    def test_empty_input_yields_no_buckets(self):
        assert _curve({}) == {"buckets": []}

    def test_single_expiry_yields_no_buckets(self):
        result = _curve({"25JUL26": (40.0, 7.0)})
        assert result["buckets"] == []

    def test_missing_atm_iv_on_one_side_excludes_that_expiry(self):
        """Two expiries, one with atm_iv=None -> only 1 usable expiry left
        -> no bucket (not a crash, not a fabricated 0)."""
        result = _curve({
            "25JUL26": (40.0, 7.0),
            "31JUL26": (None, 14.0),
        })
        assert result["buckets"] == []

    def test_missing_atm_iv_on_middle_expiry_bridges_the_gap(self):
        """Three expiries, the middle one has no ATM IV (thin chain) -- the
        two flanking expiries still form a bucket across the gap rather
        than the whole curve collapsing to zero buckets. Documented
        judgment call (task-C9-report.md)."""
        result = _curve({
            "25JUL26": (40.0, 7.0),
            "31JUL26": (None, 14.0),
            "07AUG26": (45.0, 30.0),
        })
        buckets = result["buckets"]
        assert len(buckets) == 1
        assert buckets[0]["from_expiry"] == "25JUL26"
        assert buckets[0]["to_expiry"] == "07AUG26"
        assert buckets[0]["t1_days"] == pytest.approx(7.0)
        assert buckets[0]["t2_days"] == pytest.approx(30.0)

    def test_identical_dte_pair_is_skipped_not_a_zero_division(self):
        """T2 == T1 for the only pair -> skip -> no buckets, no
        ZeroDivisionError."""
        result = _curve({
            "25JUL26": (40.0, 7.0),
            "26JUL26": (41.0, 7.0),
        })
        assert result["buckets"] == []

    def test_identical_dte_pair_skips_only_that_pair_not_the_whole_entry(self):
        """Three expiries where the first two tie on DTE: that specific
        pair is skipped, but the tied entry still pairs with its OTHER
        neighbour."""
        result = _curve({
            "25JUL26": (40.0, 7.0),
            "26JUL26": (41.0, 7.0),
            "07AUG26": (45.0, 30.0),
        })
        buckets = result["buckets"]
        assert len(buckets) == 1
        assert buckets[0]["from_expiry"] == "26JUL26"
        assert buckets[0]["to_expiry"] == "07AUG26"

    def test_negative_dte_excludes_that_expiry(self):
        result = _curve({
            "OLDEXP": (40.0, -1.0),
            "31JUL26": (45.0, 14.0),
        })
        assert result["buckets"] == []

    def test_zero_dte_excludes_that_expiry(self):
        result = _curve({
            "TODAYEXP": (40.0, 0.0),
            "31JUL26": (45.0, 14.0),
        })
        assert result["buckets"] == []

    def test_nonpositive_atm_iv_excludes_that_expiry(self):
        result = _curve({
            "BADIV": (0.0, 7.0),
            "31JUL26": (45.0, 14.0),
        })
        assert result["buckets"] == []

    def test_all_atm_iv_missing_yields_no_buckets(self):
        result = _curve({
            "25JUL26": (None, 7.0),
            "31JUL26": (None, 14.0),
        })
        assert result["buckets"] == []


class TestAcceptanceFormulas:
    """institutional_metrics_spec.md section 8(d) worked examples."""

    def test_t8_1_standard_forward(self):
        result = _curve({
            "FRONT": (40.0, 7.0),
            "BACK": (45.0, 30.0),
        })
        buckets = result["buckets"]
        assert len(buckets) == 1
        bucket = buckets[0]
        assert bucket["fwd_var"] == pytest.approx(0.21543478260869567, rel=1e-12)
        assert bucket["fwd_vol_pct"] == pytest.approx(46.414952, abs=1e-4)
        assert bucket["negative_variance"] is False
        assert "NEGATIVE_VARIANCE" not in bucket["flags"]

    def test_t8_2_negative_variance_flagged(self):
        result = _curve({
            "FRONT": (80.0, 7.0),
            "BACK": (40.0, 10.0),
        })
        bucket = result["buckets"][0]
        assert bucket["fwd_var"] == pytest.approx(-0.96, rel=1e-12)
        assert bucket["fwd_vol_pct"] is None
        assert bucket["negative_variance"] is True
        assert "NEGATIVE_VARIANCE" in bucket["flags"]

    def test_t8_3_unit_invariance_days_vs_years(self):
        """Same sigmas as T8.1, T expressed in years instead of days --
        fwd_vol_pct must match to within float noise (the /365 cancels)."""
        days_result = _curve({
            "FRONT": (40.0, 7.0),
            "BACK": (45.0, 30.0),
        })
        years_result = _curve({
            "FRONT": (40.0, 7.0 / 365.0),
            "BACK": (45.0, 30.0 / 365.0),
        })
        days_vol = days_result["buckets"][0]["fwd_vol_pct"]
        years_vol = years_result["buckets"][0]["fwd_vol_pct"]
        assert years_vol == pytest.approx(days_vol, abs=1e-10)


class TestEventPremiumFlag:
    def test_only_two_expiries_event_premium_is_none(self):
        """Spec: 'Only 2 expiries -> forward vol computed, event premium
        None (no neighbours to compare against).'"""
        result = _curve({
            "FRONT": (40.0, 7.0),
            "BACK": (45.0, 30.0),
        })
        bucket = result["buckets"][0]
        assert bucket["event_premium"] is None
        assert "EVENT_PREMIUM" not in bucket["flags"]

    def test_anomalous_middle_bucket_flagged_event_premium(self):
        """Four expiries -> 3 buckets. The middle bucket's forward vol is
        pushed far above both neighbours -> event premium > 5 vol pts."""
        result = _curve({
            "E1": (18.0, 1.0),
            "E2": (18.5, 7.0),
            "E3": (60.0, 8.0),   # huge IV jump concentrated in one day -> spikes the E2->E3 forward vol
            "E4": (36.0, 30.0),
        })
        buckets = result["buckets"]
        assert len(buckets) == 3
        middle = buckets[1]
        assert middle["from_expiry"] == "E2"
        assert middle["to_expiry"] == "E3"
        assert middle["event_premium"] is not None
        assert middle["event_premium"] > 5.0
        assert "EVENT_PREMIUM" in middle["flags"]
        # neighbours are not themselves flagged
        assert "EVENT_PREMIUM" not in buckets[0]["flags"]
        assert "EVENT_PREMIUM" not in buckets[2]["flags"]

    def test_edge_bucket_event_premium_uses_single_available_neighbour(self):
        """First bucket in a 3-bucket chain has only ONE neighbour (the
        next bucket) -- median of a single value is that value itself."""
        result = _curve({
            "E1": (18.0, 1.0),
            "E2": (18.5, 7.0),
            "E3": (19.0, 14.0),
            "E4": (20.0, 30.0),
        })
        buckets = result["buckets"]
        first = buckets[0]
        # first bucket's only neighbour is buckets[1]
        expected = first["fwd_vol_pct"] - buckets[1]["fwd_vol_pct"]
        assert first["event_premium"] == pytest.approx(expected)

    def test_event_premium_skips_neighbour_with_negative_variance(self):
        """A neighbouring bucket with negative variance (fwd_vol_pct=None)
        must not be included in the median -- and must not crash."""
        result = _curve({
            # E1->E2: normal
            "E1": (18.0, 1.0),
            # E2->E3: engineered negative variance (high front, low back over short window)
            "E2": (18.5, 7.0),
            "E3": (5.0, 8.0),
            # E3->E4: normal
            "E4": (20.0, 30.0),
        })
        buckets = result["buckets"]
        assert len(buckets) == 3
        assert buckets[1]["negative_variance"] is True
        assert buckets[1]["fwd_vol_pct"] is None
        # bucket 0's only neighbour (bucket 1) is unusable -> None, not a crash
        assert buckets[0]["event_premium"] is None
        # bucket 2's only neighbour (bucket 1) is unusable -> None too
        assert buckets[2]["event_premium"] is None

    def test_flags_default_to_empty_list_not_none(self):
        result = _curve({
            "FRONT": (40.0, 7.0),
            "BACK": (41.0, 8.0),
        })
        bucket = result["buckets"][0]
        assert bucket["flags"] == []
