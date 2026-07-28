"""
Unit tests for OnChainAnalysisService._apply_pcr_percentile_classification
(bugfix_spec.md Item 10 service wiring).
"""

import dataclasses
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.results.analysis_result import (
    ExpirationBundle,
    MarketMetricsResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


def _empty_leg():
    return MoneynessLeg(
        itm_oi=0, otm_oi=0, total_oi=0,
        itm_notional=0, otm_notional=0, total_notional=0,
        itm_pct=0, otm_pct=0,
    )


def _make_analysis(expiration, ratio, total_call_oi=800.0, total_put_oi=120.0):
    return ExpirationAnalysisResult(
        expiration=expiration,
        underlying_price=90000.0,
        total_instruments=10, call_count=5, put_count=5,
        strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=90000.0, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=total_call_oi, total_put_oi=total_put_oi,
            ratio=ratio, bias="Strong Bullish",  # old hardcoded label, must be overwritten
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=0, total_put_volume=0, total_volume=0, volume_ratio=0,
        ),
        moneyness=MoneynessResult(
            calls=_empty_leg(), puts=_empty_leg(), totals=_empty_leg(), oi_skew="Neutral",
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(),
            short_term_resistance=None, short_term_support=None,
        ),
    )


def _make_result(bundles):
    return OnChainAnalysisResult(
        currency="BTC",
        underlying_price=90000.0,
        generated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        market_metrics=MarketMetricsResult(
            dvol=None, iv_percentile=None, iv_rank=None,
            current_funding=None, funding_8h=None,
        ),
        expirations=tuple(bundles),
        market_wide=MagicMock(),
        parsed_instruments={},
        atm_iv_by_expiration={},
        recent_trades=(),
    )


def _bundle(expiration, ratio, **kwargs):
    return ExpirationBundle(
        expiration=expiration,
        analysis=_make_analysis(expiration, ratio, **kwargs),
        gex_dex=None, flow=None, vol_surface=None, oi_changes=None,
        iv_percentile=None, trend=None, flow_chart_paths={}, enriched_instruments=(),
    )


class _FakeAnalyzer:
    currency = "BTC"


class TestApplyPcrPercentileClassification:
    def test_no_repository_returns_result_unchanged(self):
        service = _make_service(repository=None)
        result = _make_result([_bundle("25DEC26", 0.15)])
        out = service._apply_pcr_percentile_classification(_FakeAnalyzer(), result)
        assert out is result

    def test_t10_2_live_counter_example_reclassified(self):
        """
        bugfix_spec.md T10.2: 0.5996 at its own 98.3rd 90d percentile must
        classify Strong Bearish, replacing the old hardcoded-threshold
        "Strong Bullish" (0.5996 < 0.7).
        """
        repo = MagicMock()
        # 705-point history where 0.5996 sits at the 98.3rd percentile:
        # 693 of 705 values below it (693/705*100 = 98.297...).
        history = [0.1] * 693 + [0.9] * 12
        repo.get_metric_history.return_value = history
        service = _make_service(repository=repo)
        result = _make_result([_bundle("25JUN27", 0.5996)])

        out = service._apply_pcr_percentile_classification(_FakeAnalyzer(), result)

        pcr = out.bundle("25JUN27").analysis.put_call_ratio
        assert pcr.bias == "Strong Bearish"
        assert pcr.percentile_90d == pytest.approx(98.29787234042553, abs=1e-6)
        assert pcr.history_n_90d == 705

    def test_insufficient_history_yields_insufficient_label(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = [0.2] * 12
        service = _make_service(repository=repo)
        result = _make_result([_bundle("14AUG26", 0.2064)])

        out = service._apply_pcr_percentile_classification(_FakeAnalyzer(), result)

        pcr = out.bundle("14AUG26").analysis.put_call_ratio
        assert pcr.bias == "Insufficient history"
        assert pcr.percentile_90d is None
        assert pcr.history_n_90d == 12

    def test_infinite_ratio_is_not_classified(self):
        repo = MagicMock()
        repo.get_metric_history.return_value = [0.2] * 40
        service = _make_service(repository=repo)
        result = _make_result([_bundle("25SEP26", float("inf"), total_call_oi=0.0)])

        out = service._apply_pcr_percentile_classification(_FakeAnalyzer(), result)

        pcr = out.bundle("25SEP26").analysis.put_call_ratio
        assert pcr.bias == "N/A"
        assert pcr.percentile_90d is None

    def test_repository_failure_falls_back_to_insufficient_not_stale_label(self):
        repo = MagicMock()
        repo.get_metric_history.side_effect = RuntimeError("boom")
        service = _make_service(repository=repo)
        result = _make_result([_bundle("25DEC26", 0.5)])

        out = service._apply_pcr_percentile_classification(_FakeAnalyzer(), result)

        pcr = out.bundle("25DEC26").analysis.put_call_ratio
        # Must not raise, and must not silently keep the old hardcoded
        # "Strong Bullish" label -- that would be the exact bug this task
        # replaces, just re-introduced via the error path.
        assert pcr.bias == "Insufficient history"

    def test_multiple_expirations_each_use_their_own_history(self):
        repo = MagicMock()

        def _side_effect(table, column, currency, lookback_hours, expiration=None, time_column=None):
            if expiration == "25DEC26":
                return [0.9] * 40  # 0.15 will be low percentile here
            return [0.1] * 40  # 0.6 will be high percentile here
        repo.get_metric_history.side_effect = _side_effect

        service = _make_service(repository=repo)
        result = _make_result([
            _bundle("25DEC26", 0.15),
            _bundle("7AUG26", 0.6),
        ])

        out = service._apply_pcr_percentile_classification(_FakeAnalyzer(), result)

        dec_pcr = out.bundle("25DEC26").analysis.put_call_ratio
        aug_pcr = out.bundle("7AUG26").analysis.put_call_ratio
        assert dec_pcr.bias in ("Strong Bullish", "Bullish")
        assert aug_pcr.bias in ("Strong Bearish", "Bearish")
