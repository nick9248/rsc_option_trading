"""
Unit tests for OnChainAnalysisService._build_normalized_metrics
(institutional_metrics_spec.md section 1: service wiring).

Verifies the five wired AVAILABLE metrics (net GEX, PCR-OI, total OI,
DVOL, funding) via DatabaseRepository.get_metric_history for the true
front-month expiration (per-expiry metrics) or currency-only (market-wide
metrics), that VRP is deliberately NOT wired (the live DVOL-based VRP and
the stored per-expiration ATM-IV-based vrp_absolute history are two
different formulas for the same metric name), and the C1 review fixes:
Critical #1 (total_oi via two separate whitelisted queries, summed in
Python; per-metric exception isolation so one metric's failure doesn't
discard the other four), Critical #2 (funding history rescaled to match
the live value's scale), Important #1 (true nearest-DTE front-month, not a
lexicographic string sort), Important #4 (staleness detection).
"""

import dataclasses
from datetime import datetime, timedelta, timezone
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


def _make_repo_mock():
    """
    A MagicMock repository that behaves like "no freshness data" by
    default -- get_metric_freshness must return None or a real datetime,
    never an auto-generated MagicMock (which would blow up
    datetime.now(mock.tzinfo) in _compute_historical_context_staleness).
    """
    repo = MagicMock()
    repo.get_metric_freshness.return_value = None
    return repo


def _empty_leg():
    return MoneynessLeg(
        itm_oi=0, otm_oi=0, total_oi=0,
        itm_notional=0, otm_notional=0, total_notional=0,
        itm_pct=0, otm_pct=0,
    )


def _make_analysis(expiration, ratio=0.15, total_call_oi=800.0, total_put_oi=120.0):
    return ExpirationAnalysisResult(
        expiration=expiration,
        underlying_price=90000.0,
        total_instruments=10, call_count=5, put_count=5,
        strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=90000.0, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=total_call_oi, total_put_oi=total_put_oi,
            ratio=ratio, bias="Strong Bullish",
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


def _make_result(expirations=("25DEC26",), **analysis_kwargs):
    bundles = tuple(
        ExpirationBundle(
            expiration=exp, analysis=_make_analysis(exp, **analysis_kwargs),
            gex_dex=None, flow=None, vol_surface=None, oi_changes=None,
            iv_percentile=None, trend=None, flow_chart_paths={}, enriched_instruments=(),
        )
        for exp in expirations
    )
    return OnChainAnalysisResult(
        currency="BTC",
        underlying_price=90000.0,
        generated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        market_metrics=MarketMetricsResult(
            dvol=37.69, iv_percentile=None, iv_rank=None,
            current_funding=None, funding_8h=0.0007,
        ),
        expirations=bundles,
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
        metrics, front_month, stale_since = service._build_normalized_metrics(analyzer, result)
        assert metrics == {}
        assert front_month is None
        assert stale_since is None

    def test_no_expirations_returns_empty(self):
        repo = _make_repo_mock()
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer()
        result = _make_result(expirations=())
        metrics, front_month, stale_since = service._build_normalized_metrics(analyzer, result)
        assert metrics == {}
        assert front_month is None

    def test_pcr_oi_and_total_oi_wired_for_front_month(self):
        """
        C1 review Critical #1: total_oi is fetched via two SEPARATE
        whitelisted queries (total_call_oi, total_put_oi), never a
        composite SQL expression.
        """
        repo = _make_repo_mock()
        history_30 = [0.1 * i for i in range(1, 31)]  # 30 points
        history_90 = [0.1 * i for i in range(1, 91)]  # 90 points
        repo.get_metric_history.side_effect = _history_side_effect({
            ("onchain_analysis_snapshots", "put_call_ratio_oi", 720): history_30,
            ("onchain_analysis_snapshots", "put_call_ratio_oi", 2160): history_90,
            ("onchain_analysis_snapshots", "total_call_oi", 720): history_30,
            ("onchain_analysis_snapshots", "total_call_oi", 2160): history_90,
            ("onchain_analysis_snapshots", "total_put_oi", 720): history_30,
            ("onchain_analysis_snapshots", "total_put_oi", 2160): history_90,
        })
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()

        metrics, front_month, _ = service._build_normalized_metrics(analyzer, result)

        assert front_month == "25DEC26"
        assert "pcr_oi" in metrics
        assert metrics["pcr_oi"].value == pytest.approx(0.15)
        assert metrics["pcr_oi"].sufficient is True

        assert "total_oi" in metrics
        assert metrics["total_oi"].value == pytest.approx(920.0)  # 800 + 120
        # history_30d is call_oi + put_oi summed element-wise -> 2x history_30
        assert metrics["total_oi"].n_30d == 30

        # net_gex and vrp are not available in this fixture (gex_dex=None,
        # VRP is deliberately never wired) -- must not appear.
        assert "net_gex" not in metrics
        assert "vrp" not in metrics

    def test_composite_column_never_sent_to_repository(self):
        """
        C1 review Critical #1: the never-whitelisted composite expression
        must never be constructed at all now -- assert no call to
        get_metric_history ever passes it as ``column``.
        """
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = [0.1] * 30
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()

        service._build_normalized_metrics(analyzer, result)

        for call in repo.get_metric_history.call_args_list:
            assert call.kwargs.get("column") != "(total_call_oi + total_put_oi)"

    def test_infinite_pcr_ratio_skips_pcr_oi_but_keeps_total_oi(self):
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result(ratio=float("inf"), total_call_oi=0.0, total_put_oi=120.0)

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        assert "pcr_oi" not in metrics
        assert "total_oi" in metrics
        assert metrics["total_oi"].value == pytest.approx(120.0)

    def test_dvol_and_funding_wired_market_wide_no_expiration_filter(self):
        repo = _make_repo_mock()
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

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        assert metrics["dvol"].value == pytest.approx(37.69)
        # C1 review Critical #2: value is NOT rescaled (matches the report
        # header's existing funding_8h * 100 display convention) -- only
        # the fetched history is rescaled.
        assert metrics["funding"].value == pytest.approx(0.0007)

        # Market-wide calls must never pass an expiration filter.
        for call in repo.get_metric_history.call_args_list:
            kwargs = call.kwargs
            if kwargs.get("table") in ("volatility_index_history", "funding_rate_history"):
                assert kwargs.get("expiration") is None

    def test_funding_history_rescaled_to_match_live_value_scale(self):
        """
        C1 review Critical #2 (confirmed 100x scale mismatch): the raw
        stored funding_rate_history.funding_rate column is 100x smaller
        than market_metrics["funding_8h"]'s scale (prospective_collector.py
        divides by 100 before persisting). The service must rescale the
        FETCHED history back up by 100x so percentile/z are computed on
        matching scales, without changing ``value`` itself.

        history at the (divided) stored scale: 0.1..3.0 in steps of 0.1
        (30 points). Corrected (x100): 10..300. funding_8h = 155 sits
        exactly at the 50th percentile of the corrected series (values
        1..15 x10=10..150 are below -> 14 of 29 comparison points below,
        one equal at 155 itself is absent from history so this is a clean
        rank check).
        """
        repo = _make_repo_mock()
        stored_scale_history = [0.1 * i for i in range(1, 31)]  # 0.1..3.0
        repo.get_metric_history.side_effect = _history_side_effect({
            ("funding_rate_history", "funding_rate", 720): stored_scale_history,
            ("funding_rate_history", "funding_rate", 2160): stored_scale_history,
        })
        service = _make_service(repository=repo)
        # funding_8h chosen to land cleanly inside the corrected (x100)
        # range 10..300 -- e.g. 155 sits between corrected values 150 and
        # 160 (14 of 30 strictly below -> 46.67th percentile).
        analyzer = _FakeAnalyzer(market_metrics={"funding_8h": 155.0})
        result = _make_result()

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        assert metrics["funding"].value == pytest.approx(155.0)
        assert metrics["funding"].sufficient is True
        # 15 of 30 corrected values (10..150) are strictly below 155 ->
        # 100 * 15 / 30 = 50.0 exactly.
        assert metrics["funding"].percentile_30d == pytest.approx(50.0)

    def test_missing_dvol_and_funding_are_simply_absent(self):
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()
        no_mm_result_market_metrics = MarketMetricsResult(
            dvol=None, iv_percentile=None, iv_rank=None,
            current_funding=None, funding_8h=None,
        )
        new_result = dataclasses.replace(result, market_metrics=no_mm_result_market_metrics)

        metrics, _, _ = service._build_normalized_metrics(analyzer, new_result)
        assert "dvol" not in metrics
        assert "funding" not in metrics

    def test_generic_repository_exception_is_caught_and_does_not_raise(self):
        repo = _make_repo_mock()
        repo.get_metric_history.side_effect = RuntimeError("boom")
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69})
        result = _make_result()

        # Must not raise.
        metrics, front_month, stale_since = service._build_normalized_metrics(analyzer, result)
        assert isinstance(metrics, dict)
        assert metrics == {}

    def test_whitelist_violation_on_one_metric_does_not_discard_the_others(self):
        """
        C1 review Critical #1 (the actual bug): a ValueError (whitelist
        violation) raised while building ONE metric must not discard
        metrics already built for OTHER metrics. Simulates the historical
        bug directly: net_gex's history fetch raises ValueError, but
        pcr_oi/total_oi/dvol/funding must still be present.
        """
        repo = _make_repo_mock()
        good_history_30 = [0.1 * i for i in range(1, 31)]
        good_history_90 = [0.1 * i for i in range(1, 91)]

        def _side_effect(table, column, currency, lookback_hours, expiration=None, time_column=None):
            if table == "onchain_analysis_snapshots" and column == "total_net_gex":
                raise ValueError("simulated whitelist violation")
            key = (table, column, lookback_hours)
            return {
                ("onchain_analysis_snapshots", "put_call_ratio_oi", 720): good_history_30,
                ("onchain_analysis_snapshots", "put_call_ratio_oi", 2160): good_history_90,
                ("onchain_analysis_snapshots", "total_call_oi", 720): good_history_30,
                ("onchain_analysis_snapshots", "total_call_oi", 2160): good_history_90,
                ("onchain_analysis_snapshots", "total_put_oi", 720): good_history_30,
                ("onchain_analysis_snapshots", "total_put_oi", 2160): good_history_90,
                ("volatility_index_history", "dvol", 720): good_history_30,
                ("volatility_index_history", "dvol", 2160): good_history_90,
                ("funding_rate_history", "funding_rate", 720): good_history_30,
                ("funding_rate_history", "funding_rate", 2160): good_history_90,
            }.get(key, [])

        repo.get_metric_history.side_effect = _side_effect

        # Give this expiration a gex_dex so net_gex WOULD be attempted.
        from coding.core.analytics.results.gex_dex_results import GexDexKeyLevels, GexDexResult

        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69, "funding_8h": 0.0007})
        result = _make_result()
        gex_dex = GexDexResult(
            strike_rows=(), cumulative_gex={}, cumulative_dex={},
            key_levels=GexDexKeyLevels(
                call_resistance=None, put_support=None, hvl=None, gamma_flip=None,
                net_gex_at_spot=None,
            ),
            spot_price=90000.0, total_net_gex=5_000_000.0, total_net_dex=0.0, currency="BTC",
        )
        bundle_with_gex = dataclasses.replace(result.bundle("25DEC26"), gex_dex=gex_dex)
        result = dataclasses.replace(result, expirations=(bundle_with_gex,))

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        assert "net_gex" not in metrics  # the metric that hit the ValueError
        assert "pcr_oi" in metrics
        assert "total_oi" in metrics
        assert "dvol" in metrics
        assert "funding" in metrics


class TestPickFrontMonthExpiration:
    def test_picks_nearest_dte_not_lexicographic_first(self):
        """
        C1 review Important #1: "14AUG26" sorts before "26JUL26"
        lexicographically (string comparison), but 26JUL26 is the true
        earlier (front-month) expiration by calendar date.
        """
        service = OnChainAnalysisService.__new__(OnChainAnalysisService)
        result = service._pick_front_month_expiration(("14AUG26", "26JUL26", "25DEC26"))
        assert result == "26JUL26"

    def test_unparseable_names_are_skipped(self):
        service = OnChainAnalysisService.__new__(OnChainAnalysisService)
        result = service._pick_front_month_expiration(("not-a-date", "26JUL26"))
        assert result == "26JUL26"

    def test_all_unparseable_returns_none(self):
        service = OnChainAnalysisService.__new__(OnChainAnalysisService)
        result = service._pick_front_month_expiration(("not-a-date", "also-not"))
        assert result is None

    def test_end_to_end_front_month_selection(self):
        """
        C1 review Important #1: reproduces the exact scenario from the
        task's own golden fixture -- string-sort picked "14AUG26" (later)
        over the true front month "26JUL26" (earlier).
        """
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = []
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result(expirations=("14AUG26", "26JUL26", "25DEC26"))

        _, front_month, _ = service._build_normalized_metrics(analyzer, result)

        assert front_month == "26JUL26"


class TestHistoricalContextStaleness:
    def test_fresh_data_returns_none(self):
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = [0.1 * i for i in range(1, 31)]
        repo.get_metric_freshness.return_value = datetime.now(timezone.utc)
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69})
        result = _make_result()

        _, _, stale_since = service._build_normalized_metrics(analyzer, result)
        assert stale_since is None

    def test_stale_data_beyond_3h_returns_timestamp(self):
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = [0.1 * i for i in range(1, 31)]
        stale_ts = datetime.now(timezone.utc) - timedelta(hours=5)
        repo.get_metric_freshness.return_value = stale_ts
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69})
        result = _make_result()

        _, _, stale_since = service._build_normalized_metrics(analyzer, result)
        assert stale_since == stale_ts

    def test_freshness_lookup_failure_does_not_raise(self):
        repo = _make_repo_mock()
        repo.get_metric_history.return_value = [0.1 * i for i in range(1, 31)]
        repo.get_metric_freshness.side_effect = RuntimeError("boom")
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={"dvol": 37.69})
        result = _make_result()

        _, _, stale_since = service._build_normalized_metrics(analyzer, result)
        assert stale_since is None

    def test_no_metrics_built_means_no_staleness_lookup(self):
        """
        put_call_ratio is always populated on ExpirationAnalysisResult
        (never None), so total_oi is always buildable whenever an
        expiration exists -- the only way to reach "zero specs built" is
        no expirations at all (already covered by
        TestBuildNormalizedMetrics.test_no_expirations_returns_empty).
        This test adds the explicit assertion that path never queries
        freshness either.
        """
        repo = _make_repo_mock()
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result(expirations=())

        metrics, _, stale_since = service._build_normalized_metrics(analyzer, result)
        assert metrics == {}
        assert stale_since is None
        repo.get_metric_freshness.assert_not_called()
