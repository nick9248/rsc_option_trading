"""
Unit tests for the on-chain analysis result models
(refactor_design_spec.md section T2 / section 2).

Covers construction, frozen-ness (immutability), and ``to_dict()`` /
``to_flat_dict()`` round-trips against the exact legacy dict shapes
documented in the spec's Result-model definitions section.

Purely additive: these tests exercise only the new ``results`` package.
No production wiring is touched (that is T3+).
"""

import dataclasses

import pytest

from coding.core.analytics.results import (
    BlockTrade,
    BlockTradesResult,
    CrossAssetCorrelationResult,
    ExpirationAnalysisResult,
    ExpirationBundle,
    FlowResult,
    FlowTotals,
    FuturesBasisEntry,
    FuturesBasisResult,
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
    IvByStrikeRow,
    IvPercentileResult,
    LevelRef,
    MarketMetricsResult,
    MarketWideResult,
    MaxPainResult,
    MoneynessBucket,
    MoneynessLeg,
    MoneynessResult,
    OiChangeRow,
    OiChangesResult,
    OnChainAnalysisResult,
    PerpetualFundingResult,
    PutCallByMoneyness,
    PutCallRatioResult,
    RealizedVolatilityResult,
    SecondOrderGreeks,
    SkewResult,
    StrikeFlowEntry,
    StrikeOiRow,
    SupportResistanceResult,
    TermStructureEntry,
    TermStructureResult,
    TopStrikeEntry,
    TrendSnapshot,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
    VolSurfaceResult,
    VolumeStatsResult,
)


# ---------------------------------------------------------------------------
# Frozen-ness (every model must be immutable)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cls",
    [
        StrikeOiRow,
        MaxPainResult,
        PutCallRatioResult,
        VolumeStatsResult,
        MoneynessLeg,
        MoneynessResult,
        LevelRef,
        SupportResistanceResult,
        ExpirationAnalysisResult,
        GexDexStrikeRow,
        GexDexLevel,
        GexDexKeyLevels,
        GexDexResult,
        StrikeFlowEntry,
        FlowTotals,
        TopStrikeEntry,
        FlowResult,
        IvByStrikeRow,
        SkewResult,
        MoneynessBucket,
        PutCallByMoneyness,
        SecondOrderGreeks,
        VolSurfaceResult,
        TermStructureEntry,
        TermStructureResult,
        FuturesBasisEntry,
        FuturesBasisResult,
        RealizedVolatilityResult,
        VarianceRiskPremiumResult,
        VolatilityConeResult,
        PerpetualFundingResult,
        BlockTrade,
        BlockTradesResult,
        CrossAssetCorrelationResult,
        MarketWideResult,
        MarketMetricsResult,
        TrendSnapshot,
        OiChangeRow,
        OiChangesResult,
        IvPercentileResult,
        ExpirationBundle,
        OnChainAnalysisResult,
    ],
)
def test_model_is_frozen_dataclass(cls):
    """Every result model is a frozen dataclass (Decision D1)."""
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is True


def test_frozen_dataclass_rejects_mutation():
    """Attempting to set a field on a frozen instance raises FrozenInstanceError."""
    row = StrikeOiRow(strike=100.0, call_oi=1.0, put_oi=2.0, call_volume=3.0, put_volume=4.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.strike = 200.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# expiry_results — ExpirationAnalysisResult.to_dict()
# ---------------------------------------------------------------------------

def _make_expiration_analysis_result() -> ExpirationAnalysisResult:
    return ExpirationAnalysisResult(
        expiration="10MAR26",
        underlying_price=1900.0,
        total_instruments=3,
        call_count=1,
        put_count=2,
        strike_rows=(
            StrikeOiRow(strike=1800.0, call_oi=0.0, put_oi=300.0, call_volume=0.0, put_volume=50.0),
            StrikeOiRow(strike=2000.0, call_oi=500.0, put_oi=800.0, call_volume=100.0, put_volume=150.0),
        ),
        max_pain=MaxPainResult(
            max_pain_strike=2000.0,
            pain_by_strike={1800.0: 500.0, 2000.0: 0.0},
            min_pain_value=0.0,
        ),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=500.0, total_put_oi=1100.0, ratio=2.2, bias="Strong Bearish"
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=100.0, total_put_volume=200.0, total_volume=300.0, volume_ratio=2.0
        ),
        moneyness=MoneynessResult(
            calls=MoneynessLeg(
                itm_oi=0.0, otm_oi=500.0, total_oi=500.0,
                itm_notional=0.0, otm_notional=950000.0, total_notional=950000.0,
                itm_pct=0.0, otm_pct=100.0,
            ),
            puts=MoneynessLeg(
                itm_oi=800.0, otm_oi=300.0, total_oi=1100.0,
                itm_notional=1520000.0, otm_notional=570000.0, total_notional=2090000.0,
                itm_pct=72.73, otm_pct=27.27,
            ),
            totals=MoneynessLeg(
                itm_oi=800.0, otm_oi=800.0, total_oi=1600.0,
                itm_notional=1520000.0, otm_notional=1520000.0, total_notional=3040000.0,
                itm_pct=50.0, otm_pct=50.0,
            ),
            oi_skew="Balanced",
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=(LevelRef(strike=2000.0, open_interest=500.0),),
            support_levels=(LevelRef(strike=2000.0, open_interest=800.0),),
            short_term_resistance=LevelRef(strike=2000.0, open_interest=500.0),
            short_term_support=LevelRef(strike=1800.0, open_interest=300.0),
        ),
    )


def test_expiration_analysis_result_to_dict_matches_legacy_shape():
    result = _make_expiration_analysis_result()
    d = result.to_dict()

    assert d["expiration"] == "10MAR26"
    assert d["underlying_price"] == 1900.0
    assert d["total_instruments"] == 3
    assert d["call_count"] == 1
    assert d["put_count"] == 2

    assert d["strike_data"] == {
        1800.0: {"call_oi": 0.0, "put_oi": 300.0, "call_volume": 0.0, "put_volume": 50.0},
        2000.0: {"call_oi": 500.0, "put_oi": 800.0, "call_volume": 100.0, "put_volume": 150.0},
    }

    assert d["max_pain"] == {
        "max_pain_strike": 2000.0,
        "pain_by_strike": {1800.0: 500.0, 2000.0: 0.0},
        "min_pain_value": 0.0,
    }

    assert d["put_call_ratio"] == {
        "total_call_oi": 500.0,
        "total_put_oi": 1100.0,
        "ratio": 2.2,
        "bias": "Strong Bearish",
    }

    assert d["volume_stats"] == {
        "total_call_volume": 100.0,
        "total_put_volume": 200.0,
        "total_volume": 300.0,
        "volume_ratio": 2.0,
    }

    assert d["moneyness"]["calls"]["itm_oi"] == 0.0
    assert d["moneyness"]["puts"]["itm_pct"] == 72.73
    assert d["moneyness"]["totals"]["total_oi"] == 1600.0
    assert d["moneyness"]["oi_skew"] == "Balanced"

    assert d["support_resistance"] == {
        "resistance_levels": [{"strike": 2000.0, "call_oi": 500.0}],
        "support_levels": [{"strike": 2000.0, "put_oi": 800.0}],
        "short_term_resistance": {"strike": 2000.0, "call_oi": 500.0},
        "short_term_support": {"strike": 1800.0, "put_oi": 300.0},
    }


def test_expiration_analysis_result_to_dict_none_short_term_levels():
    """short_term_resistance/support are None when there is no such level (legacy semantics)."""
    result = dataclasses.replace(
        _make_expiration_analysis_result(),
        support_resistance=SupportResistanceResult(
            resistance_levels=(),
            support_levels=(),
            short_term_resistance=None,
            short_term_support=None,
        ),
    )
    d = result.to_dict()
    assert d["support_resistance"]["short_term_resistance"] is None
    assert d["support_resistance"]["short_term_support"] is None
    assert d["support_resistance"]["resistance_levels"] == []
    assert d["support_resistance"]["support_levels"] == []


# ---------------------------------------------------------------------------
# gex_dex_results — GexDexResult.to_dict()
# ---------------------------------------------------------------------------

def _make_gex_dex_result(expiration_count=None) -> GexDexResult:
    return GexDexResult(
        strike_rows=(
            GexDexStrikeRow(
                strike=95000.0, call_gamma=1.5, put_gamma=0.5, call_delta=0.6, put_delta=-0.4,
                call_oi=100.0, put_oi=50.0, net_gex=1_000_000.0, net_dex=0.2,
                net_gamma=1.0, cumulative_gex=1_000_000.0, cumulative_dex=0.2,
            ),
        ),
        cumulative_gex={95000.0: 1_000_000.0},
        cumulative_dex={95000.0: 0.2},
        key_levels=GexDexKeyLevels(
            call_resistance=GexDexLevel(strike=95000.0, net_gex=1_000_000.0),
            put_support=None,
            hvl=94000.0,
            gamma_flip=94000.0,
        ),
        spot_price=94500.0,
        total_net_gex=1_000_000.0,
        total_net_dex=0.2,
        currency="BTC",
        expiration_count=expiration_count,
    )


def test_gex_dex_result_to_dict_matches_legacy_shape():
    result = _make_gex_dex_result()
    d = result.to_dict()

    assert d["strike_data"] == {
        95000.0: {
            "call_gamma": 1.5, "put_gamma": 0.5, "call_delta": 0.6, "put_delta": -0.4,
            "call_oi": 100.0, "put_oi": 50.0, "net_gex": 1_000_000.0, "net_dex": 0.2,
            "net_gamma": 1.0, "cumulative_gex": 1_000_000.0, "cumulative_dex": 0.2,
        }
    }
    assert d["cumulative_gex"] == {95000.0: 1_000_000.0}
    assert d["cumulative_dex"] == {95000.0: 0.2}
    assert d["key_levels"] == {
        "call_resistance": {"strike": 95000.0, "net_gex": 1_000_000.0},
        "put_support": None,
        "hvl": 94000.0,
        "gamma_flip": 94000.0,
        "cumulative_gex_zero_strike": None,
        "zero_gamma_level": None,
        "zero_gamma_crossings": [],
        "net_gex_at_spot": None,
        "gamma_regime": None,
        "legs_skipped": 0,
    }
    assert d["spot_price"] == 94500.0
    assert d["total_net_gex"] == 1_000_000.0
    assert d["total_net_dex"] == 0.2
    assert "expiration_count" not in d


def test_gex_dex_result_to_dict_includes_expiration_count_when_aggregate():
    """Legacy per-expiry calculate() omits expiration_count; only
    aggregate_across_expirations() sets it. to_dict() must reproduce both shapes."""
    result = _make_gex_dex_result(expiration_count=5)
    d = result.to_dict()
    assert d["expiration_count"] == 5


# ---------------------------------------------------------------------------
# flow_results — FlowResult.to_dict()
# ---------------------------------------------------------------------------

def _make_flow_result() -> FlowResult:
    return FlowResult(
        flow_data={
            95000.0: {
                "C": StrikeFlowEntry(
                    buy_count=3.0, sell_count=1.0, buy_volume=10.0, sell_volume=2.0,
                    buy_notional=950_000.0, sell_notional=190_000.0, net_flow=8.0,
                    buy_sell_ratio=5.0,
                ),
                "P": StrikeFlowEntry(
                    buy_count=0.0, sell_count=2.0, buy_volume=0.0, sell_volume=5.0,
                    buy_notional=0.0, sell_notional=475_000.0, net_flow=-5.0,
                    buy_sell_ratio=None,
                ),
            }
        },
        expiration_totals=FlowTotals(
            call_buy_volume=10.0, call_sell_volume=2.0, put_buy_volume=0.0, put_sell_volume=5.0
        ),
        bias_interpretation="Heavy Buying",
        flow_trend="Steady Buy Pressure",
        top_buy_strikes=(
            TopStrikeEntry(strike=95000.0, option_type="C", net_flow=8.0, volume=10.0, notional=950_000.0),
        ),
        top_sell_strikes=(
            TopStrikeEntry(strike=95000.0, option_type="P", net_flow=-5.0, volume=5.0, notional=475_000.0),
        ),
        trade_count=6,
        spot_price=95000.0,
        window_start_ms=1_000,
        window_end_ms=1_000_000,
        lookback_hours=24.0,
        sufficient_data=False,
        low_confidence=False,
    )


def test_flow_result_to_dict_flow_data_matches_legacy_shape():
    result = _make_flow_result()
    d = result.to_dict()

    assert d["flow_data"] == {
        95000.0: {
            "C": {
                "buy_count": 3.0, "sell_count": 1.0, "buy_volume": 10.0, "sell_volume": 2.0,
                "buy_notional": 950_000.0, "sell_notional": 190_000.0, "net_flow": 8.0,
                "buy_sell_ratio": 5.0,
            },
            "P": {
                "buy_count": 0.0, "sell_count": 2.0, "buy_volume": 0.0, "sell_volume": 5.0,
                "buy_notional": 0.0, "sell_notional": 475_000.0, "net_flow": -5.0,
                "buy_sell_ratio": None,
            },
        }
    }


def test_flow_result_to_dict_top_strikes_use_directional_key_names():
    """top_buy_strikes uses buy_volume/buy_notional; top_sell_strikes uses
    sell_volume/sell_notional — matching the pre-refactor analyzer output
    (TopStrikeEntry.volume/notional are generic; to_dict() must translate)."""
    result = _make_flow_result()
    d = result.to_dict()

    assert d["top_buy_strikes"] == [
        {"strike": 95000.0, "option_type": "C", "net_flow": 8.0, "buy_volume": 10.0, "buy_notional": 950_000.0}
    ]
    assert d["top_sell_strikes"] == [
        {"strike": 95000.0, "option_type": "P", "net_flow": -5.0, "sell_volume": 5.0, "sell_notional": 475_000.0}
    ]
    assert d["trade_count"] == 6
    assert d["spot_price"] == 95000.0
    assert d["expiration_totals"] == {
        "call_buy_volume": 10.0, "call_sell_volume": 2.0, "put_buy_volume": 0.0, "put_sell_volume": 5.0,
    }


# ---------------------------------------------------------------------------
# vol_surface_results — VolSurfaceResult.to_dict()
# ---------------------------------------------------------------------------

def _make_vol_surface_result() -> VolSurfaceResult:
    return VolSurfaceResult(
        expiration="10MAR26",
        spot_price=1900.0,
        iv_by_strike=(
            IvByStrikeRow(strike=1900.0, option_type="C", mark_iv=80.0, delta=0.5, moneyness_pct=0.0),
            IvByStrikeRow(strike=1900.0, option_type="P", mark_iv=82.0, delta=-0.5, moneyness_pct=0.0),
        ),
        skew_25d=SkewResult(
            put_25d_iv=85.0, call_25d_iv=78.0, put_25d_strike=1800.0, call_25d_strike=2000.0,
            skew=7.0, interpretation="Puts More Expensive - Hedging Demand",
        ),
        pc_by_moneyness=PutCallByMoneyness(
            atm=MoneynessBucket(call_oi=100.0, put_oi=150.0, range_label="±5%", ratio=1.5, bias="Slightly Bearish"),
            near_otm=MoneynessBucket(call_oi=50.0, put_oi=20.0, range_label="5-15%", ratio=0.4, bias="Bullish"),
            far_otm=MoneynessBucket(call_oi=0.0, put_oi=0.0, range_label="15%+", ratio=0.0, bias="N/A"),
        ),
        second_order_greeks=SecondOrderGreeks(
            net_vanna=0.001234, net_charm=-0.005678,
            vanna_signal="IV drop → dealers buy underlying (bullish)",
            charm_signal="Time decay pushing delta negative (bearish drift)",
            skipped_instruments=2,
        ),
        atm_iv=81.5,
        vwap_iv=82.0,
        mark_iv_average=81.0,
        traded_instrument_count=2,
    )


def test_vol_surface_result_merged_iv_by_strike_groups_call_and_put():
    """Carried A3-review finding: the calculator/model layer owns the
    per-strike {call_iv, put_iv} merge, not the formatter."""
    result = _make_vol_surface_result()
    merged = result.merged_iv_by_strike()
    assert merged == {1900.0: {"call_iv": 80.0, "put_iv": 82.0}}


def test_vol_surface_result_to_dict_matches_legacy_reader_keys():
    """volatility_reconstruction_service reads skew_25d, second_order_greeks,
    pc_by_moneyness, atm_iv — these must match the legacy nested shape exactly.
    to_dict() must also reproduce iv_by_strike merged (legacy shape) and must
    NOT leak the typed-only fields (vwap_iv, mark_iv_average, expiration,
    spot_price, skipped_instruments) that the legacy calculate() dict never had."""
    result = _make_vol_surface_result()
    d = result.to_dict()

    assert d["iv_by_strike"] == [{"strike": 1900.0, "call_iv": 80.0, "put_iv": 82.0}]
    assert "vwap_iv" not in d
    assert "mark_iv_average" not in d
    assert "expiration" not in d
    assert "spot_price" not in d
    assert "skipped_instruments" not in d["second_order_greeks"]

    assert d["skew_25d"] == {
        "put_25d_iv": 85.0, "call_25d_iv": 78.0, "put_25d_strike": 1800.0, "call_25d_strike": 2000.0,
        "skew": 7.0, "interpretation": "Puts More Expensive - Hedging Demand",
    }
    assert d["atm_iv"] == 81.5
    assert d["second_order_greeks"]["net_vanna"] == 0.001234
    assert d["second_order_greeks"]["net_charm"] == -0.005678
    assert d["second_order_greeks"]["vanna_signal"] == "IV drop → dealers buy underlying (bullish)"

    pc = d["pc_by_moneyness"]
    assert pc["atm"] == {"call_oi": 100.0, "put_oi": 150.0, "range": "±5%", "ratio": 1.5, "bias": "Slightly Bearish"}
    assert pc["near_otm"]["range"] == "5-15%"
    assert pc["far_otm"]["ratio"] == 0.0
    assert pc["far_otm"]["bias"] == "N/A"


# ---------------------------------------------------------------------------
# market_wide_results — properties + MarketWideResult.to_flat_dict()
# ---------------------------------------------------------------------------

def test_realized_volatility_result_properties():
    rv = RealizedVolatilityResult(rv_by_window={10: 0.40, 20: 0.45, 30: 0.50})
    assert rv.rv_10d == 0.40
    assert rv.rv_20d == 0.45
    assert rv.rv_30d == 0.50


def test_realized_volatility_result_missing_window_defaults_zero():
    rv = RealizedVolatilityResult(rv_by_window={10: 0.40})
    assert rv.rv_20d == 0.0
    assert rv.rv_30d == 0.0


def test_volatility_cone_result_properties():
    cone = VolatilityConeResult(percentile_by_window={10: 25.0, 20: 50.0, 30: 75.0})
    assert cone.cone_10d_pctile == 25.0
    assert cone.cone_20d_pctile == 50.0
    assert cone.cone_30d_pctile == 75.0


def _make_market_wide_result(**overrides) -> MarketWideResult:
    defaults = dict(
        spot_price=95000.0,
        currency="BTC",
        dvol=65.0,
        iv_percentile_365d=50.0,
        aggregate_gex_dex=None,
        term_structure=TermStructureResult(
            entries=(TermStructureEntry(expiration="28FEB26", dte=30, atm_iv=70.0),),
            shape="CONTANGO", spread=5.0, spread_signed=5.0, iv_by_dte={30: 70.0},
        ),
        futures_basis=FuturesBasisResult(
            entries=(FuturesBasisEntry(
                instrument_name="BTC-28FEB26", dte=30, mark_price=96000.0,
                index_price=95000.0, annualized_premium_pct=12.5,
            ),),
            futures_basis={"28FEB26": 12.5},
        ),
        realized_volatility=RealizedVolatilityResult(rv_by_window={10: 0.4, 20: 0.45, 30: 0.5}),
        variance_risk_premium=VarianceRiskPremiumResult(vrp=15.0, signal="RICH", dvol=65.0, rv_30d=0.5),
        volatility_cone=VolatilityConeResult(percentile_by_window={10: 25.0, 20: 50.0, 30: 75.0}),
        perpetual_funding=PerpetualFundingResult(
            perp_open_interest=1_000_000.0, funding_rate=0.0001, funding_8h=0.0001,
            funding_trend="Rising", history_points=10,
        ),
        block_trades=BlockTradesResult(
            trades=(BlockTrade(
                timestamp=1234567890, instrument_name="BTC-28FEB26-100000-C",
                amount=5.0, direction="buy", notional=500_000.0, implied_volatility=70.0,
            ),),
            notional_threshold=100_000.0, total_detected=1,
        ),
        cross_asset_correlation=CrossAssetCorrelationResult(
            other_currency="ETH", price_correlation=0.85, dvol_correlation=0.6, sample_size=30,
        ),
        failed_sections=(),
    )
    defaults.update(overrides)
    return MarketWideResult(**defaults)


def test_market_wide_result_to_flat_dict_full():
    mw = _make_market_wide_result()
    flat = mw.to_flat_dict()

    assert flat["shape"] == "CONTANGO"
    assert flat["spread"] == 5.0
    assert flat["spread_signed"] == 5.0
    assert flat["iv_by_dte"] == {30: 70.0}
    assert flat["futures_basis"] == {"28FEB26": 12.5}
    assert flat["rv_10d"] == 0.4
    assert flat["rv_20d"] == 0.45
    assert flat["rv_30d"] == 0.5
    assert flat["vrp"] == 15.0
    assert flat["signal"] == "RICH"
    assert flat["cone_10d_pctile"] == 25.0
    assert flat["cone_20d_pctile"] == 50.0
    assert flat["cone_30d_pctile"] == 75.0
    assert flat["perp_oi"] == 1_000_000.0
    assert flat["perp_funding_trend"] == "Rising"
    assert flat["funding_rate"] == 0.0001
    assert flat["funding_8h"] == 0.0001
    assert flat["block_trades"] == [
        {
            "timestamp": 1234567890, "instrument": "BTC-28FEB26-100000-C", "size": 5.0,
            "amount": 5.0, "direction": "buy", "notional": 500_000.0, "iv": 70.0,
        }
    ]
    assert flat["btc_eth_price_corr"] == 0.85
    assert flat["btc_eth_dvol_corr"] == 0.6
    assert flat["spot_price"] == 95000.0
    assert flat["dvol"] == 65.0
    assert flat["iv_percentile_365d"] == 50.0


def test_market_wide_result_to_flat_dict_omits_keys_for_none_sections():
    """When a sub-result failed/was skipped (None), its flat keys are absent —
    downstream readers already do mw.get(key) or default (see synthesis.py)."""
    mw = _make_market_wide_result(
        term_structure=None,
        futures_basis=None,
        realized_volatility=None,
        variance_risk_premium=None,
        volatility_cone=None,
        perpetual_funding=None,
        block_trades=None,
        cross_asset_correlation=None,
        failed_sections=("term_structure", "futures_basis"),
    )
    flat = mw.to_flat_dict()

    for key in (
        "shape", "spread", "spread_signed", "iv_by_dte", "futures_basis",
        "rv_10d", "rv_20d", "rv_30d", "vrp", "signal",
        "cone_10d_pctile", "cone_20d_pctile", "cone_30d_pctile",
        "perp_oi", "perp_funding_trend", "funding_rate", "funding_8h",
        "block_trades", "btc_eth_price_corr", "btc_eth_dvol_corr",
    ):
        assert key not in flat

    # Always-present top-level fields remain.
    assert flat["spot_price"] == 95000.0
    assert flat["dvol"] == 65.0
    assert flat["iv_percentile_365d"] == 50.0


# ---------------------------------------------------------------------------
# analysis_result — OnChainAnalysisResult.bundle() / expiration_names()
# ---------------------------------------------------------------------------

def _make_expiration_bundle(expiration: str) -> ExpirationBundle:
    return ExpirationBundle(
        expiration=expiration,
        analysis=_make_expiration_analysis_result(),
        gex_dex=None,
        flow=None,
        vol_surface=None,
        oi_changes=None,
        iv_percentile=None,
        trend=None,
        flow_chart_paths={},
        enriched_instruments=(),
    )


def test_on_chain_analysis_result_bundle_found():
    import datetime as dt

    bundle_a = _make_expiration_bundle("10MAR26")
    bundle_b = _make_expiration_bundle("28MAR26")
    result = OnChainAnalysisResult(
        currency="BTC",
        underlying_price=95000.0,
        generated_at=dt.datetime(2026, 7, 25, 12, 0, 0),
        market_metrics=MarketMetricsResult(
            dvol=65.0, iv_percentile=50.0, iv_rank=40.0, current_funding=0.0001, funding_8h=0.0001
        ),
        expirations=(bundle_a, bundle_b),
        market_wide=_make_market_wide_result(),
        parsed_instruments={},
        atm_iv_by_expiration={"10MAR26": 80.0},
        recent_trades=(),
    )

    assert result.bundle("10MAR26") is bundle_a
    assert result.bundle("28MAR26") is bundle_b
    assert result.bundle("does-not-exist") is None
    assert result.expiration_names() == ("10MAR26", "28MAR26")


def test_oi_changes_result_construction():
    rows = (
        OiChangeRow(strike=95000.0, option_type="C", previous_oi=100.0, current_oi=150.0, change=50.0, change_pct=50.0),
    )
    result = OiChangesResult(rows=rows, total_significant=1, has_previous_snapshot=True)
    assert result.rows == rows
    assert result.total_significant == 1
    assert result.has_previous_snapshot is True


def test_iv_percentile_result_construction():
    result = IvPercentileResult(atm_strike=95000.0, current_iv=80.0, percentile=65.0, history_days=180)
    assert result.percentile == 65.0
