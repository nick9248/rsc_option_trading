"""
Unit tests for OnChainAnalysisBuilder (refactor_design_spec.md section T6).
"""

from datetime import datetime

from coding.core.analytics.results.analysis_result import (
    IvPercentileResult,
    MarketMetricsResult,
    OiChangesResult,
    OnChainAnalysisResult,
    TrendSnapshot,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessResult,
    PutCallRatioResult,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.flow_results import FlowResult, FlowTotals
from coding.core.analytics.results.market_wide_results import MarketWideResult
from coding.service.on_chain.analysis_builder import OnChainAnalysisBuilder


def _make_leg() -> "MoneynessLeg":
    from coding.core.analytics.results.expiry_results import MoneynessLeg

    return MoneynessLeg(
        itm_oi=10.0, otm_oi=10.0, total_oi=20.0,
        itm_notional=100.0, otm_notional=100.0, total_notional=200.0,
        itm_pct=50.0, otm_pct=50.0,
    )


def _make_analysis(expiration: str = "28MAR26", underlying_price: float = 90000.0) -> ExpirationAnalysisResult:
    leg = _make_leg()
    return ExpirationAnalysisResult(
        expiration=expiration,
        underlying_price=underlying_price,
        total_instruments=2,
        call_count=1,
        put_count=1,
        strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=90000.0, pain_by_strike={90000.0: 0.0}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=100.0, total_put_oi=100.0, ratio=1.0, bias="Neutral",
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=10.0, total_put_volume=10.0, total_volume=20.0, volume_ratio=1.0,
        ),
        moneyness=MoneynessResult(calls=leg, puts=leg, totals=leg, oi_skew="Neutral"),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(),
            short_term_resistance=None, short_term_support=None,
        ),
    )


def _make_flow_result() -> FlowResult:
    return FlowResult(
        flow_data={},
        expiration_totals=FlowTotals(
            call_buy_volume=1.0, call_sell_volume=1.0, put_buy_volume=1.0, put_sell_volume=1.0,
        ),
        bias_interpretation="Neutral",
        flow_trend="Stable",
        top_buy_strikes=(),
        top_sell_strikes=(),
        trade_count=25,
        spot_price=90000.0,
        window_start_ms=0,
        window_end_ms=86_400_000,
        lookback_hours=24.0,
        sufficient_data=True,
        low_confidence=False,
    )


class TestOnChainAnalysisBuilder:
    def test_build_field_by_field(self):
        builder = OnChainAnalysisBuilder(
            currency="BTC", underlying_price=90000.0,
            parsed_instruments={"28MAR26": [{"strike": 90000.0}]},
        )
        analysis = _make_analysis()
        flow = _make_flow_result()
        metrics = MarketMetricsResult(
            dvol=60.0, iv_percentile=50.0, iv_rank=40.0, current_funding=0.0001, funding_8h=0.0002,
        )
        trend = TrendSnapshot(
            max_pain_strike=89000.0, call_oi=90.0, put_oi=90.0,
            pc_ratio=1.0, total_volume=10.0, volume_ratio=1.0,
        )
        market_wide = MarketWideResult(
            spot_price=90000.0, currency="BTC", dvol=60.0, iv_percentile_365d=50.0,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
            failed_sections=(),
        )

        builder.set_market_metrics(metrics)
        builder.set_expiration_analysis("28MAR26", analysis)
        builder.set_flow("28MAR26", flow)
        builder.set_trend("28MAR26", trend)
        builder.set_flow_charts("28MAR26", {"distribution": "path/a.html"})
        builder.set_enriched_instruments("28MAR26", [{"strike": 90000.0, "delta": 0.5}])
        builder.set_recent_trades([{"instrument_name": "BTC-28MAR26-90000-C"}])
        builder.set_market_wide(market_wide)

        result = builder.build()

        assert isinstance(result, OnChainAnalysisResult)
        assert result.currency == "BTC"
        assert result.underlying_price == 90000.0
        assert isinstance(result.generated_at, datetime)
        assert result.market_metrics == metrics
        assert result.market_wide == market_wide
        assert result.recent_trades == ({"instrument_name": "BTC-28MAR26-90000-C"},)
        assert result.parsed_instruments == {"28MAR26": ({"strike": 90000.0},)}

        bundle = result.bundle("28MAR26")
        assert bundle is not None
        assert bundle.analysis == analysis
        assert bundle.flow == flow
        assert bundle.trend == trend
        assert bundle.flow_chart_paths == {"distribution": "path/a.html"}
        assert bundle.enriched_instruments == ({"strike": 90000.0, "delta": 0.5},)
        assert bundle.gex_dex is None
        assert bundle.vol_surface is None
        assert bundle.oi_changes is None
        assert bundle.iv_percentile is None

    def test_missing_sections_default_to_none_not_raise(self):
        """A partial run (a failed phase) must still produce a usable result."""
        builder = OnChainAnalysisBuilder(
            currency="BTC", underlying_price=90000.0,
            parsed_instruments={"28MAR26": []},
        )
        builder.set_expiration_analysis("28MAR26", _make_analysis())

        result = builder.build()

        assert result.market_metrics.dvol is None
        assert result.market_wide.aggregate_gex_dex is None
        assert result.atm_iv_by_expiration == {}
        assert result.recent_trades == ()
        bundle = result.bundle("28MAR26")
        assert bundle.gex_dex is None and bundle.flow is None and bundle.vol_surface is None

    def test_expiration_without_analysis_is_skipped(self):
        """Matches OnChainAnalyzer.generate_report()'s `if not analysis: continue` —
        an expiration that never got a set_expiration_analysis() call (e.g.
        analyze_expiration() returned empty) must not appear in the result."""
        builder = OnChainAnalysisBuilder(
            currency="BTC", underlying_price=90000.0,
            parsed_instruments={"28MAR26": [], "27JUN26": []},
        )
        builder.set_expiration_analysis("28MAR26", _make_analysis())
        # 27JUN26 gets a gex_dex set via some other phase but never an analysis
        builder.set_flow_charts("27JUN26", {})

        result = builder.build()

        assert result.expiration_names() == ("28MAR26",)
        assert result.bundle("27JUN26") is None

    def test_expirations_ordered_chronologically_by_string(self):
        builder = OnChainAnalysisBuilder(
            currency="BTC", underlying_price=90000.0,
            parsed_instruments={"27JUN26": [], "28MAR26": [], "10APR26": []},
        )
        for expiration in ("27JUN26", "28MAR26", "10APR26"):
            builder.set_expiration_analysis(expiration, _make_analysis(expiration))

        result = builder.build()

        assert result.expiration_names() == tuple(sorted(["27JUN26", "28MAR26", "10APR26"]))

    def test_atm_iv_by_expiration_derived_from_vol_surface(self):
        from coding.core.analytics.results.vol_surface_results import (
            PutCallByMoneyness,
            MoneynessBucket,
            SecondOrderGreeks,
            SkewResult,
            VolSurfaceResult,
        )

        builder = OnChainAnalysisBuilder(
            currency="BTC", underlying_price=90000.0,
            parsed_instruments={"28MAR26": []},
        )
        builder.set_expiration_analysis("28MAR26", _make_analysis())
        bucket = MoneynessBucket(call_oi=1.0, put_oi=1.0, range_label="ATM", ratio=1.0, bias="Neutral")
        vol_surface = VolSurfaceResult(
            expiration="28MAR26",
            skew_25d=SkewResult(skew=None, interpretation="N/A", put_25d_iv=None,
                                 call_25d_iv=None, put_25d_strike=None, call_25d_strike=None),
            atm_iv=55.5,
            vwap_iv=None,
            mark_iv_average=None,
            traded_instrument_count=0,
            iv_by_strike=(),
            pc_by_moneyness=PutCallByMoneyness(atm=bucket, near_otm=bucket, far_otm=bucket),
            second_order_greeks=SecondOrderGreeks(
                net_vanna=0.0, net_charm=0.0, vanna_signal="N/A", charm_signal="N/A",
                skipped_instruments=0,
            ),
            spot_price=90000.0,
        )
        builder.set_vol_surface("28MAR26", vol_surface)

        result = builder.build()

        assert result.atm_iv_by_expiration == {"28MAR26": 55.5}
