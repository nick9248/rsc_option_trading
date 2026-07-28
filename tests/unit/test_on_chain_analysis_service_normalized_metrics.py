"""
Unit tests for OnChainAnalysisService._build_normalized_metrics
(institutional_metrics_spec.md section 1: service wiring).

Verifies the six AVAILABLE metrics are wired to HistoricalNormalizer via
DatabaseRepository.get_metric_history for the front-month expiration (per-
expiry metrics) or currency-only (market-wide metrics), and that VRP is
deliberately NOT wired here (see module docstring on the service method --
the live DVOL-based VRP and the stored per-expiration ATM-IV-based
vrp_absolute history are two different formulas for the same metric name).
"""

from unittest.mock import MagicMock

import pytest

from coding.core.analytics.historical_normalizer import NormalizedMetric
from coding.core.analytics.results.analysis_result import (
    MarketMetricsResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult, PutCallRatioResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


def _make_result(front_month="25DEC26"):
    from datetime import datetime, timezone

    from coding.core.analytics.results.analysis_result import ExpirationBundle
    from coding.core.analytics.results.expiry_results import (
        MaxPainResult,
        MoneynessLeg,
        MoneynessResult,
        SupportResistanceResult,
        VolumeStatsResult,
    )

    empty_leg = MoneynessLeg(
        itm_oi=0, otm_oi=0, total_oi=0,
        itm_notional=0, otm_notional=0, total_notional=0,
        itm_pct=0, otm_pct=0,
    )
    analysis = ExpirationAnalysisResult(
        expiration=front_month,
        underlying_price=90000.0,
        total_instruments=10,
        call_count=5,
        put_count=5,
        strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=90000.0, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=800.0, total_put_oi=120.0, ratio=0.15, bias="Strong Bullish",
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=0, total_put_volume=0, total_volume=0, volume_ratio=0,
        ),
        moneyness=MoneynessResult(
            calls=empty_leg, puts=empty_leg, totals=empty_leg, oi_skew="Neutral",
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(),
            short_term_resistance=None, short_term_support=None,
        ),
    )

    bundle = ExpirationBundle(
        expiration=front_month,
        analysis=analysis,
        gex_dex=None,
        flow=None,
        vol_surface=None,
        oi_changes=None,
        iv_percentile=None,
        trend=None,
        flow_chart_paths={},
        enriched_instruments=(),
    )
    return OnChainAnalysisResult(
        currency="BTC",
        underlying_price=90000.0,
        generated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        market_metrics=MarketMetricsResult(
            dvol=37.69, iv_percentile=None, iv_rank=None,
            current_funding=None, funding_8h=0.0007,
        ),
        expirations=(bundle,),
        market_wide=MagicMock(),
        parsed_instruments={},
        atm_iv_by_expiration={},
        recent_trades=(),
    )


class _FakeAnalyzer:
    def __init__(self, currency="BTC", market_metrics=None):
        self.currency = currency
        self.market_metrics = market_metrics or {}


def _history_side_effect(rows_by_key):
    def _side_effect(table, column, currency, lookback_hours, expiration=None, time_column=None):
        return rows_by_key.get((table, column, lookback_hours), [])
    return _side_effect


class TestBuildNormalizedMetrics:
    def test_no_repository_returns_empty(self):
        service = _make_service(repository=None)
        analyzer = _FakeAnalyzer()
        result = _make_result()
        assert service._build_normalized_metrics(analyzer, result) == {}

    def test_no_expirations_returns_empty(self):
        repo = MagicMock()
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer()
        result = _make_result()
        empty_result = result.__class__(
            currency=result.currency, underlying_price=result.underlying_price,
            generated_at=result.generated_at, market_metrics=result.market_metrics,
            expirations=(), market_wide=result.market_wide,
            parsed_instruments={}, atm_iv_by_expiration={}, recent_trades=(),
        )
        assert service._build_normalized_metrics(analyzer, empty_result) == {}

    def test_pcr_oi_and_total_oi_wired_for_front_month(self):
        repo = MagicMock()
        history_30 = [0.1 * i for i in range(1, 31)]  # 30 points
        history_90 = [0.1 * i for i in range(1, 91)]  # 90 points
        repo.get_metric_history.side_effect = _history_side_effect({
            ("onchain_analysis_snapshots", "put_call_ratio_oi", 720): history_30,
            ("onchain_analysis_snapshots", "put_call_ratio_oi", 2160): history_90,
            ("onchain_analysis_snapshots", "(total_call_oi + total_put_oi)", 720): history_30,
            ("onchain_analysis_snapshots", "(total_call_oi + total_put_oi)", 2160): history_90,
        })
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()

        metrics = service._build_normalized_metrics(analyzer, result)

        assert "pcr_oi" in metrics
        assert metrics["pcr_oi"].value == pytest.approx(0.15)
        assert metrics["pcr_oi"].sufficient is True

        assert "total_oi" in metrics
        assert metrics["total_oi"].value == pytest.approx(920.0)  # 800 + 120

        # net_gex and vrp are not available in this fixture (gex_dex=None,
        # VRP is deliberately never wired) -- must not appear.
        assert "net_gex" not in metrics
        assert "vrp" not in metrics

    def test_infinite_pcr_ratio_skips_pcr_oi_but_keeps_total_oi(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()
        # Rebuild with an infinite ratio (call_oi == 0)
        bad_pcr = PutCallRatioResult(
            total_call_oi=0.0, total_put_oi=120.0, ratio=float("inf"), bias="N/A",
        )
        analysis = result.bundle("25DEC26").analysis
        import dataclasses
        new_analysis = dataclasses.replace(analysis, put_call_ratio=bad_pcr)
        new_bundle = dataclasses.replace(result.bundle("25DEC26"), analysis=new_analysis)
        new_result = dataclasses.replace(result, expirations=(new_bundle,))

        metrics = service._build_normalized_metrics(analyzer, new_result)

        assert "pcr_oi" not in metrics
        assert "total_oi" in metrics
        assert metrics["total_oi"].value == pytest.approx(120.0)

    def test_dvol_and_funding_wired_market_wide_no_expiration_filter(self):
        repo = MagicMock()
        history_30 = [30.0 + i for i in range(30)]
        history_90 = [30.0 + i for i in range(90)]
        repo.get_metric_history.side_effect = _history_side_effect({
            ("volatility_index_history", "dvol", 720): history_30,
            ("volatility_index_history", "dvol", 2160): history_90,
            ("funding_rate_history", "funding_rate", 720): history_30,
            ("funding_rate_history", "funding_rate", 2160): history_90,
        })
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69, "funding_8h": 0.0007})
        result = _make_result()

        metrics = service._build_normalized_metrics(analyzer, result)

        assert metrics["dvol"].value == pytest.approx(37.69)
        assert metrics["funding"].value == pytest.approx(0.0007)

        # Market-wide calls must never pass an expiration filter.
        for call in repo.get_metric_history.call_args_list:
            kwargs = call.kwargs
            if kwargs.get("table") in ("volatility_index_history", "funding_rate_history"):
                assert kwargs.get("expiration") is None

    def test_missing_dvol_and_funding_are_simply_absent(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()
        # Force market_metrics without dvol/funding_8h
        no_mm_result_market_metrics = MarketMetricsResult(
            dvol=None, iv_percentile=None, iv_rank=None,
            current_funding=None, funding_8h=None,
        )
        import dataclasses
        new_result = dataclasses.replace(result, market_metrics=no_mm_result_market_metrics)

        metrics = service._build_normalized_metrics(analyzer, new_result)
        assert "dvol" not in metrics
        assert "funding" not in metrics

    def test_repository_exception_is_caught_and_returns_partial_or_empty(self):
        repo = MagicMock()
        repo.get_metric_history.side_effect = RuntimeError("boom")
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69})
        result = _make_result()

        # Must not raise.
        metrics = service._build_normalized_metrics(analyzer, result)
        assert isinstance(metrics, dict)
