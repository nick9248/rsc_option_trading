"""
Unit tests for OnChainAnalysisService._build_forward_vol_curve
(institutional_metrics_spec.md section 8: service wiring for the FORWARD
VOL report section, Task C9).

Mirrors test_on_chain_analysis_service_skew_term_structure.py's pattern: a
fake analyzer carrying ``_skew_by_expiry`` (populated during the
vol-surface phase). Unlike skew term structure, forward vol needs NO
repository -- it is a pure live-chain calculation with no percentile
history lookups -- so these tests never set up a mocked DB.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository=None):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


class _FakeAnalyzer:
    def __init__(self, currency="BTC", skew_by_expiry=None):
        self.currency = currency
        self._skew_by_expiry = skew_by_expiry or {}


def _future_expiration(days_ahead: float) -> str:
    """A "%d%b%y"-formatted expiration string ``days_ahead`` days from
    now (settling at 08:00 UTC), matching Deribit's convention."""
    dt = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    return dt.strftime("%d%b%y").upper()


def _skew(atm=None):
    return {
        "rr_25d": None, "bf_25d": None, "call_25d_iv": None, "put_25d_iv": None,
        "call_25d_strike": None, "put_25d_strike": None,
        "atm_iv_interp": atm, "n_quotes_used": 10, "method": "linear_delta",
    }


class TestBuildForwardVolCurve:
    def test_no_skew_data_returns_none(self):
        service = _make_service()
        analyzer = _FakeAnalyzer(skew_by_expiry={})
        assert service._build_forward_vol_curve(analyzer, "BTC") is None

    def test_analyzer_missing_skew_attribute_returns_none_not_raises(self):
        """Isolation: an analyzer double (or a real analyzer whose
        vol-surface phase never ran) without a ``_skew_by_expiry``
        attribute at all must not raise -- this method has no
        repository gate ahead of the attribute read, unlike
        _build_skew_term_structure."""
        service = _make_service()

        class _BareAnalyzer:
            currency = "BTC"

        assert service._build_forward_vol_curve(_BareAnalyzer(), "BTC") is None

    def test_runs_without_a_repository(self):
        """Forward vol has no DB dependency -- must not require
        self.repository, unlike _build_skew_term_structure."""
        service = _make_service(repository=None)
        near = _future_expiration(7)
        far = _future_expiration(30)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            near: _skew(atm=40.0),
            far: _skew(atm=45.0),
        })

        result = service._build_forward_vol_curve(analyzer, "BTC")

        assert result is not None
        assert len(result.buckets) == 1
        bucket = result.buckets[0]
        assert bucket.from_expiry == near
        assert bucket.to_expiry == far
        # _future_expiration anchors to 08:00 UTC settlement on the target
        # date, not exactly "N days from now" -- t1_days/t2_days are close
        # to but not exactly 7.0/30.0. Recompute the expected forward vol
        # from the ACTUAL dte the service produced (formula correctness is
        # already covered by test_forward_vol_calculator.py's T8.1); this
        # test only checks the service wiring reused the right values.
        t1, t2 = bucket.t1_days, bucket.t2_days
        expected_fwd_var = (0.45 ** 2 * t2 - 0.40 ** 2 * t1) / (t2 - t1)
        assert bucket.fwd_var == pytest.approx(expected_fwd_var, rel=1e-9)
        assert bucket.fwd_vol_pct == pytest.approx(expected_fwd_var ** 0.5 * 100, rel=1e-9)

    def test_single_expiry_returns_none(self):
        service = _make_service()
        exp = _future_expiration(7)
        analyzer = _FakeAnalyzer(skew_by_expiry={exp: _skew(atm=40.0)})
        assert service._build_forward_vol_curve(analyzer, "BTC") is None

    def test_missing_atm_iv_expiry_excluded_result_none_if_too_few_left(self):
        service = _make_service()
        near = _future_expiration(7)
        far = _future_expiration(30)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            near: _skew(atm=None),
            far: _skew(atm=45.0),
        })
        assert service._build_forward_vol_curve(analyzer, "BTC") is None

    def test_unparseable_expiration_is_skipped_not_raised(self):
        service = _make_service()
        good_near = _future_expiration(7)
        good_far = _future_expiration(30)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            "NOT-A-DATE": _skew(atm=50.0),
            good_near: _skew(atm=40.0),
            good_far: _skew(atm=45.0),
        })

        result = service._build_forward_vol_curve(analyzer, "BTC")

        assert result is not None
        assert len(result.buckets) == 1
        assert result.buckets[0].from_expiry == good_near
        assert result.buckets[0].to_expiry == good_far

    def test_shared_expiry_dte_matches_skew_term_structure_convention(self):
        """Timezone lesson (task brief): forward vol's DTE for a shared
        expiry must come from the SAME clock convention as
        _build_skew_term_structure's own DTE for that expiry --
        MarketWideCalculator._calculate_days_to_expiry, so the two
        sections can never desync on the same expiration string."""
        from coding.core.analytics.market_wide_calculator import MarketWideCalculator

        repo = MagicMock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        near = _future_expiration(7)
        far = _future_expiration(30)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            near: _skew(atm=40.0),
            far: _skew(atm=45.0),
        })

        skew_result = service._build_skew_term_structure(analyzer, "BTC")
        forward_vol_result = service._build_forward_vol_curve(analyzer, "BTC")

        skew_dte_by_expiry = {e.expiration: e.dte for e in skew_result.entries}
        bucket = forward_vol_result.buckets[0]
        assert bucket.t1_days == pytest.approx(skew_dte_by_expiry[near], abs=1e-6)
        assert bucket.t2_days == pytest.approx(skew_dte_by_expiry[far], abs=1e-6)

    def test_negative_variance_bucket_flows_through_to_result(self):
        service = _make_service()
        near = _future_expiration(7)
        far = _future_expiration(10)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            near: _skew(atm=80.0),
            far: _skew(atm=40.0),
        })

        result = service._build_forward_vol_curve(analyzer, "BTC")

        assert result.has_negative_variance is True
        bucket = result.buckets[0]
        assert bucket.negative_variance is True
        assert bucket.fwd_vol_pct is None
        # See test_runs_without_a_repository: t1_days/t2_days are close to
        # but not exactly 7.0/10.0 (08:00 UTC settlement anchor), so recompute
        # the expected variance from the actual dte rather than assuming -0.96
        # exactly (that exact figure is T8.2, covered in the pure-calculator
        # unit tests with hand-picked T values).
        t1, t2 = bucket.t1_days, bucket.t2_days
        expected_fwd_var = (0.40 ** 2 * t2 - 0.80 ** 2 * t1) / (t2 - t1)
        assert bucket.fwd_var == pytest.approx(expected_fwd_var, rel=1e-9)
        assert expected_fwd_var < 0  # sanity: still negative for this scenario
