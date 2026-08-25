"""
Unit tests for OnChainAnalysisService._build_skew_term_structure
(institutional_metrics_spec.md section 3(c): service wiring for the SKEW
TERM STRUCTURE report section, Task C4).

Mirrors test_on_chain_analysis_service_normalized_metrics.py's pattern: a
fake analyzer carrying ``_skew_by_expiry`` (populated during the
vol-surface phase, mirroring ``_atm_ivs``) and a mocked repository.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


class _FakeAnalyzer:
    def __init__(self, currency="BTC", skew_by_expiry=None):
        self.currency = currency
        self._skew_by_expiry = skew_by_expiry or {}


def _future_expiration(days_ahead: int) -> str:
    """A "%d%b%y"-formatted expiration string ``days_ahead`` days from
    now, uppercased to match Deribit's convention (e.g. "25AUG26")."""
    dt = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    return dt.strftime("%d%b%y").upper()


def _skew(rr=None, bf=None, atm=None, n_quotes=10):
    return {
        "rr_25d": rr, "bf_25d": bf, "call_25d_iv": None, "put_25d_iv": None,
        "call_25d_strike": None, "put_25d_strike": None,
        "atm_iv_interp": atm, "n_quotes_used": n_quotes, "method": "linear_delta",
    }


class TestBuildSkewTermStructure:
    def test_no_repository_returns_none(self):
        service = _make_service(repository=None)
        analyzer = _FakeAnalyzer(skew_by_expiry={_future_expiration(5): _skew(rr=-3.0, bf=1.0)})
        assert service._build_skew_term_structure(analyzer, "BTC") is None

    def test_no_skew_data_returns_none(self):
        repo = MagicMock()
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(skew_by_expiry={})
        assert service._build_skew_term_structure(analyzer, "BTC") is None
        repo.get_metric_history.assert_not_called()

    def test_insufficient_history_yields_none_percentile_not_fabricated(self):
        """Decision D10: volatility_skew_history starts empty -- the
        expected initial state is percentile=None, not a fabricated 0/50."""
        repo = MagicMock()
        repo.get_metric_history.return_value = []  # < MIN_OBS (30)
        service = _make_service(repository=repo)
        exp = _future_expiration(5)
        analyzer = _FakeAnalyzer(skew_by_expiry={exp: _skew(rr=-3.80, bf=0.90, atm=18.51, n_quotes=14)})

        result = service._build_skew_term_structure(analyzer, "BTC")

        assert result is not None
        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.expiration == exp
        assert entry.rr_25d == pytest.approx(-3.80)
        assert entry.rr_percentile_30d is None
        assert entry.rr_regime_30d is None
        assert entry.rr_n_30d == 0
        assert entry.bf_25d == pytest.approx(0.90)
        assert entry.bf_percentile_30d is None
        assert entry.bf_n_30d == 0
        assert entry.n_quotes_used == 14
        assert entry.atm_iv_interp == pytest.approx(18.51)

    def test_sufficient_history_yields_percentile_and_regime(self):
        repo = MagicMock()
        # 35 observations, all below -3.80 -> percentile should be high.
        history = [-10.0 + 0.1 * i for i in range(35)]
        repo.get_metric_history.return_value = history
        service = _make_service(repository=repo)
        exp = _future_expiration(5)
        analyzer = _FakeAnalyzer(skew_by_expiry={exp: _skew(rr=-3.80, bf=0.90, atm=18.51)})

        result = service._build_skew_term_structure(analyzer, "BTC")

        entry = result.entries[0]
        assert entry.rr_n_30d == 35
        assert entry.rr_percentile_30d is not None
        assert entry.rr_regime_30d is not None

    def test_none_rr_bf_skips_history_query_entirely(self):
        """T3.2/T3.3: chain does not bracket 25-delta -> rr_25d/bf_25d are
        None. No history query should even be issued for a None value --
        there is nothing to percentile-rank."""
        repo = MagicMock()
        service = _make_service(repository=repo)
        exp = _future_expiration(5)
        analyzer = _FakeAnalyzer(skew_by_expiry={exp: _skew(rr=None, bf=None, atm=None, n_quotes=4)})

        result = service._build_skew_term_structure(analyzer, "BTC")

        entry = result.entries[0]
        assert entry.rr_25d is None
        assert entry.rr_percentile_30d is None
        assert entry.bf_25d is None
        assert entry.bf_percentile_30d is None
        repo.get_metric_history.assert_not_called()

    def test_entries_sorted_by_dte_and_slope_is_back_minus_front(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        near = _future_expiration(1)
        far = _future_expiration(150)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            far: _skew(rr=-4.34, bf=2.85),
            near: _skew(rr=-3.80, bf=0.90),
        })

        result = service._build_skew_term_structure(analyzer, "BTC")

        assert [e.expiration for e in result.entries] == [near, far]
        assert result.rr_slope == pytest.approx(-4.34 - (-3.80))

    def test_single_entry_has_no_slope(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        exp = _future_expiration(5)
        analyzer = _FakeAnalyzer(skew_by_expiry={exp: _skew(rr=-3.80, bf=0.90)})

        result = service._build_skew_term_structure(analyzer, "BTC")
        assert result.rr_slope is None

    def test_unparseable_expiration_is_skipped_not_raised(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(skew_by_expiry={"NOT-A-DATE": _skew(rr=-1.0, bf=0.5)})

        result = service._build_skew_term_structure(analyzer, "BTC")
        assert result is None

    def test_one_expiration_failure_does_not_block_others(self):
        """A repository exception for one expiration's history fetch must
        not discard the other expirations' rows (same isolation principle
        as _build_normalized_metrics)."""
        repo = MagicMock()
        good_exp = _future_expiration(5)
        bad_exp = _future_expiration(10)

        def _side_effect(table, column, currency, lookback_hours, expiration=None, time_column=None):
            if expiration == bad_exp:
                raise RuntimeError("db hiccup")
            return []

        repo.get_metric_history.side_effect = _side_effect
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(skew_by_expiry={
            good_exp: _skew(rr=-3.80, bf=0.90),
            bad_exp: _skew(rr=-4.00, bf=1.00),
        })

        result = service._build_skew_term_structure(analyzer, "BTC")

        assert result is not None
        assert [e.expiration for e in result.entries] == [good_exp]
