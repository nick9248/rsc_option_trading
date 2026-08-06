"""
Unit tests for coding.core.analytics.levels_table_builder
(institutional_metrics_spec.md section 9(c), Task D3).

TDD per the task brief: the join/combination logic that must NOT live in
the GUI (CLAUDE.md Code Quality Checklist section 3) is proven here with
plain dataclasses, no Qt involved.
"""

from datetime import date

from coding.core.analytics.levels_table_builder import build_levels_table
from coding.core.analytics.results.analysis_result import ExpirationBundle
from coding.core.analytics.results.dealer_inventory_results import (
    DealerInventoryKeyLevels,
    DealerInventoryLevel,
    DealerInventoryResult,
    DealerInventoryStrikeRow,
)
from coding.core.analytics.results.exposure_profile_results import (
    ExposureProfileResult,
    ExposureStrikeRow,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    StrikeOiRow,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.fixed_strike_vol_results import (
    FixedStrikeVolResult,
    StrikeIvChangeRow,
)
from coding.core.analytics.results.flow_results import FlowResult, FlowTotals, StrikeFlowEntry
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
)


def _leg():
    return MoneynessLeg(
        itm_oi=0.0, otm_oi=0.0, total_oi=0.0, itm_notional=0.0,
        otm_notional=0.0, total_notional=0.0, itm_pct=0.0, otm_pct=0.0,
    )


def _analysis(strikes_oi, max_pain_strike=None) -> ExpirationAnalysisResult:
    return ExpirationAnalysisResult(
        expiration="27MAR26",
        underlying_price=95000.0,
        total_instruments=0,
        call_count=0,
        put_count=0,
        strike_rows=tuple(
            StrikeOiRow(strike=s, call_oi=c, put_oi=p, call_volume=0.0, put_volume=0.0)
            for s, c, p in strikes_oi
        ),
        max_pain=MaxPainResult(max_pain_strike=max_pain_strike, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(total_call_oi=0.0, total_put_oi=0.0, ratio=0.0, bias="Neutral"),
        volume_stats=VolumeStatsResult(total_call_volume=0.0, total_put_volume=0.0, total_volume=0.0, volume_ratio=0.0),
        moneyness=MoneynessResult(calls=_leg(), puts=_leg(), totals=_leg(), oi_skew="Balanced"),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(), short_term_resistance=None, short_term_support=None,
        ),
    )


def _gex_dex_row(strike, gamma_exposure_holder, net_gex, net_dex):
    return GexDexStrikeRow(
        strike=strike, call_gamma=0.0, put_gamma=0.0, call_delta=0.0, put_delta=0.0,
        call_oi=0.0, put_oi=0.0, net_gex=net_gex, net_dex=net_dex, net_gamma=0.0,
        cumulative_gex=0.0, cumulative_dex=0.0, gamma_exposure_holder=gamma_exposure_holder,
    )


def _gex_dex_result(rows, call_wall=None, put_support=None, hvl=None) -> GexDexResult:
    return GexDexResult(
        strike_rows=tuple(rows),
        cumulative_gex={},
        cumulative_dex={},
        key_levels=GexDexKeyLevels(
            call_resistance=GexDexLevel(strike=call_wall, net_gex=0.0) if call_wall is not None else None,
            put_support=GexDexLevel(strike=put_support, net_gex=0.0) if put_support is not None else None,
            hvl=hvl,
            gamma_flip=hvl,
        ),
        spot_price=95000.0,
        total_net_gex=0.0,
        total_net_dex=0.0,
        currency="BTC",
    )


def _dealer_inventory_result(rows, render_inferred, call_wall=None, put_support=None, hvl=None):
    return DealerInventoryResult(
        strike_rows=tuple(rows),
        key_levels=DealerInventoryKeyLevels(
            call_resistance=DealerInventoryLevel(strike=call_wall, inferred_gex=0.0) if call_wall is not None else None,
            put_support=DealerInventoryLevel(strike=put_support, inferred_gex=0.0) if put_support is not None else None,
            hvl=hvl,
        ),
        total_inferred_gex=0.0,
        total_inferred_dex=0.0,
        spot_price=95000.0,
        currency="BTC",
        t0_epoch_ms=0,
        coverage=1.0,
        violation_rate=0.0,
        n_signed_trades=0,
        render_inferred=render_inferred,
    )


def _exposure_profile_result(rows) -> ExposureProfileResult:
    return ExposureProfileResult(
        strike_rows=tuple(rows),
        spot_price=95000.0,
        currency="BTC",
        total_vex_holder=0.0,
        total_cex_holder=0.0,
        total_vex_assumed_dealer=0.0,
        total_cex_assumed_dealer=0.0,
    )


def _flow_result(flow_data, sufficient_data=True) -> FlowResult:
    return FlowResult(
        flow_data=flow_data,
        expiration_totals=FlowTotals(0.0, 0.0, 0.0, 0.0),
        bias_interpretation="Neutral",
        flow_trend="Flat",
        top_buy_strikes=(),
        top_sell_strikes=(),
        trade_count=100,
        spot_price=95000.0,
        window_start_ms=0,
        window_end_ms=1,
        lookback_hours=24.0,
        sufficient_data=sufficient_data,
        low_confidence=False,
    )


def _fixed_strike_vol_result(rows, stale_prior=False) -> FixedStrikeVolResult:
    return FixedStrikeVolResult(
        expiration="27MAR26",
        today_date=date(2026, 7, 25),
        prior_date=date(2026, 7, 24),
        expected_prior_date=date(2026, 7, 24),
        stale_prior=stale_prior,
        spot_today=95000.0,
        spot_prior=94000.0,
        spot_move_pct=1.0,
        atm_iv_today=60.0,
        atm_iv_prior=59.0,
        d_atm=1.0,
        rows=tuple(rows),
        n_strikes_matched=len(rows),
        n_strikes_unmatched=0,
        regime="STICKY_STRIKE",
    )


def _bundle(analysis, gex_dex=None, dealer_inventory=None, exposure_profile=None, flow=None, fixed_strike_vol=None):
    return ExpirationBundle(
        expiration="27MAR26",
        analysis=analysis,
        gex_dex=gex_dex,
        flow=flow,
        vol_surface=None,
        oi_changes=None,
        iv_percentile=None,
        trend=None,
        flow_chart_paths={},
        enriched_instruments=(),
        dealer_inventory=dealer_inventory,
        exposure_profile=exposure_profile,
        fixed_strike_vol=fixed_strike_vol,
    )


class TestBasicJoin:
    def test_one_row_per_strike_in_canonical_analysis_order(self):
        analysis = _analysis([(90000.0, 10.0, 5.0), (95000.0, 20.0, 8.0), (100000.0, 3.0, 12.0)])
        bundle = _bundle(analysis)

        table = build_levels_table(bundle)

        assert [row.strike for row in table.rows] == [90000.0, 95000.0, 100000.0]
        assert table.expiration == "27MAR26"

    def test_call_put_oi_pulled_from_analysis_strike_rows(self):
        analysis = _analysis([(95000.0, 20.0, 8.0)])
        bundle = _bundle(analysis)

        table = build_levels_table(bundle)

        assert table.rows[0].call_oi == 20.0
        assert table.rows[0].put_oi == 8.0

    def test_gex_dex_absent_yields_none_not_zero(self):
        """
        Independent review (Task D3 round 1, Important #1): a total
        Greeks-fetch outage leaves ``bundle.gex_dex`` entirely None -- the
        row must say "we have no data" (None), not "flat exposure" (0.0).
        A 0.0 default is indistinguishable from a real zero reading and,
        rendered by the GUI's `sign_value >= 0` coloring, paints an outage
        as a uniform green "positive gamma" column.
        """
        analysis = _analysis([(95000.0, 20.0, 8.0)])
        bundle = _bundle(analysis, gex_dex=None)

        table = build_levels_table(bundle)

        row = table.rows[0]
        assert row.net_gex_holder is None
        assert row.net_gex_assumed is None
        assert row.net_dex is None
        assert table.gex_dex_available is False

    def test_gex_dex_present_but_row_missing_for_this_strike_yields_none(self):
        """
        The *partial*-outage path: gex_dex is present (some strikes have
        Greeks), but this specific strike's legs failed and it has no
        entry in ``gex_dex.strike_rows`` -- still None, not 0.0, and
        ``gex_dex_available`` stays True since the section itself is
        present (this flag is whole-section scope, not per-row).
        """
        analysis = _analysis([(90000.0, 0.0, 0.0), (95000.0, 0.0, 0.0)])
        gex_dex = _gex_dex_result([
            _gex_dex_row(90000.0, gamma_exposure_holder=100.0, net_gex=50.0, net_dex=-10.0),
            # 95000.0 has no row -- simulates a per-strike Greeks-fetch failure.
        ])
        bundle = _bundle(analysis, gex_dex=gex_dex)

        table = build_levels_table(bundle)

        assert table.gex_dex_available is True
        missing_row = table.rows[1]
        assert missing_row.strike == 95000.0
        assert missing_row.net_gex_holder is None
        assert missing_row.net_gex_assumed is None
        assert missing_row.net_dex is None


class TestGexDexJoin:
    def test_holder_and_assumed_gex_pulled_by_strike(self):
        analysis = _analysis([(90000.0, 0.0, 0.0), (95000.0, 0.0, 0.0)])
        gex_dex = _gex_dex_result([
            _gex_dex_row(90000.0, gamma_exposure_holder=100.0, net_gex=50.0, net_dex=-10.0),
            _gex_dex_row(95000.0, gamma_exposure_holder=200.0, net_gex=-70.0, net_dex=30.0),
        ])
        bundle = _bundle(analysis, gex_dex=gex_dex)

        table = build_levels_table(bundle)

        row_90k = table.rows[0]
        row_95k = table.rows[1]
        assert row_90k.net_gex_holder == 100.0
        assert row_90k.net_gex_assumed == 50.0
        assert row_90k.net_dex == -10.0
        assert row_95k.net_gex_holder == 200.0
        assert row_95k.net_gex_assumed == -70.0
        assert row_95k.net_dex == 30.0

    def test_call_wall_put_support_hvl_markers_set_on_matching_strike_only(self):
        analysis = _analysis([(90000.0, 0.0, 0.0), (95000.0, 0.0, 0.0), (100000.0, 0.0, 0.0)])
        gex_dex = _gex_dex_result(
            [
                _gex_dex_row(90000.0, 0.0, 0.0, 0.0),
                _gex_dex_row(95000.0, 0.0, 0.0, 0.0),
                _gex_dex_row(100000.0, 0.0, 0.0, 0.0),
            ],
            call_wall=100000.0, put_support=90000.0, hvl=95000.0,
        )
        bundle = _bundle(analysis, gex_dex=gex_dex)

        table = build_levels_table(bundle)

        assert table.rows[0].is_put_support_assumed is True
        assert table.rows[0].is_call_wall_assumed is False
        assert table.rows[0].is_hvl_assumed is False
        assert table.rows[1].is_hvl_assumed is True
        assert table.rows[2].is_call_wall_assumed is True


class TestInferredGex:
    def test_render_inferred_false_yields_none_and_unavailable(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        dealer_inventory = _dealer_inventory_result(
            rows=[DealerInventoryStrikeRow(strike=95000.0, dealer_net_c=1.0, dealer_net_p=1.0,
                                            inferred_gex=999.0, inferred_dex=1.0)],
            render_inferred=False,
        )
        bundle = _bundle(analysis, dealer_inventory=dealer_inventory)

        table = build_levels_table(bundle)

        assert table.inferred_available is False
        assert table.rows[0].net_gex_inferred is None
        assert table.rows[0].is_call_wall_inferred is False

    def test_render_inferred_true_joins_by_strike(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        dealer_inventory = _dealer_inventory_result(
            rows=[DealerInventoryStrikeRow(strike=95000.0, dealer_net_c=1.0, dealer_net_p=1.0,
                                            inferred_gex=-42.0, inferred_dex=1.0)],
            render_inferred=True,
            call_wall=95000.0,
        )
        bundle = _bundle(analysis, dealer_inventory=dealer_inventory)

        table = build_levels_table(bundle)

        assert table.inferred_available is True
        assert table.rows[0].net_gex_inferred == -42.0
        assert table.rows[0].is_call_wall_inferred is True


class TestVexCex:
    def test_vex_cex_pulled_from_exposure_profile_holder_view(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        exposure_profile = _exposure_profile_result([
            ExposureStrikeRow(strike=95000.0, call_oi=0.0, put_oi=0.0, call_vanna=0.0, put_vanna=0.0,
                               call_charm=0.0, put_charm=0.0, vex_holder=123.0, cex_holder=-45.0,
                               vex_assumed_dealer=1.0, cex_assumed_dealer=1.0),
        ])
        bundle = _bundle(analysis, exposure_profile=exposure_profile)

        table = build_levels_table(bundle)

        assert table.rows[0].vex == 123.0
        assert table.rows[0].cex == -45.0

    def test_exposure_profile_absent_yields_none_not_zero(self):
        """
        Independent review (Task D3 round 1, Important #1): same
        whole-section-outage argument as gex_dex above -- a total
        exposure-profile computation failure must read as "no data"
        (None), not "flat exposure" (0.0).
        """
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        bundle = _bundle(analysis, exposure_profile=None)

        table = build_levels_table(bundle)

        assert table.rows[0].vex is None
        assert table.rows[0].cex is None
        assert table.exposure_available is False

    def test_exposure_profile_present_but_row_missing_for_this_strike_yields_none(self):
        """
        Partial-outage path for exposure profile: mirrors
        ExposureProfileCalculator's own documented behavior of dropping a
        strike's legs on missing/non-finite mark_iv while that strike
        still has real OI upstream (still shows up in
        analysis.strike_rows, just absent from exposure_profile.strike_rows).
        """
        analysis = _analysis([(90000.0, 0.0, 0.0), (95000.0, 0.0, 0.0)])
        exposure_profile = _exposure_profile_result([
            ExposureStrikeRow(strike=90000.0, call_oi=0.0, put_oi=0.0, call_vanna=0.0, put_vanna=0.0,
                               call_charm=0.0, put_charm=0.0, vex_holder=10.0, cex_holder=5.0,
                               vex_assumed_dealer=1.0, cex_assumed_dealer=1.0),
            # 95000.0 has no row -- simulates a skipped-instruments strike.
        ])
        bundle = _bundle(analysis, exposure_profile=exposure_profile)

        table = build_levels_table(bundle)

        assert table.exposure_available is True
        missing_row = table.rows[1]
        assert missing_row.strike == 95000.0
        assert missing_row.vex is None
        assert missing_row.cex is None


class TestNetTakerFlow:
    def test_sums_call_and_put_net_flow_at_the_same_strike(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        flow = _flow_result({
            95000.0: {
                "C": StrikeFlowEntry(buy_count=1, sell_count=0, buy_volume=10, sell_volume=0,
                                     buy_notional=1, sell_notional=0, net_flow=10.0, buy_sell_ratio=None),
                "P": StrikeFlowEntry(buy_count=0, sell_count=1, buy_volume=0, sell_volume=5,
                                     buy_notional=0, sell_notional=1, net_flow=-5.0, buy_sell_ratio=None),
            }
        })
        bundle = _bundle(analysis, flow=flow)

        table = build_levels_table(bundle)

        assert table.net_taker_flow_available is True
        assert table.rows[0].net_taker_flow == 5.0

    def test_only_one_leg_present_uses_that_leg_unsummed_with_zero(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        flow = _flow_result({
            95000.0: {
                "C": StrikeFlowEntry(buy_count=1, sell_count=0, buy_volume=10, sell_volume=0,
                                     buy_notional=1, sell_notional=0, net_flow=10.0, buy_sell_ratio=None),
            }
        })
        bundle = _bundle(analysis, flow=flow)

        table = build_levels_table(bundle)

        assert table.rows[0].net_taker_flow == 10.0

    def test_strike_absent_from_flow_data_is_none(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        flow = _flow_result({})
        bundle = _bundle(analysis, flow=flow)

        table = build_levels_table(bundle)

        assert table.rows[0].net_taker_flow is None

    def test_insufficient_flow_data_gates_every_row_to_none(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        flow = _flow_result({
            95000.0: {
                "C": StrikeFlowEntry(buy_count=1, sell_count=0, buy_volume=10, sell_volume=0,
                                     buy_notional=1, sell_notional=0, net_flow=10.0, buy_sell_ratio=None),
            }
        }, sufficient_data=False)
        bundle = _bundle(analysis, flow=flow)

        table = build_levels_table(bundle)

        assert table.net_taker_flow_available is False
        assert table.rows[0].net_taker_flow is None

    def test_flow_absent_entirely_is_none(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        bundle = _bundle(analysis, flow=None)

        table = build_levels_table(bundle)

        assert table.net_taker_flow_available is False
        assert table.rows[0].net_taker_flow is None


class TestDeltaOneDayIv:
    def test_averages_matched_call_and_put_d_iv_at_same_strike(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        fixed_strike_vol = _fixed_strike_vol_result([
            StrikeIvChangeRow(strike=95000.0, option_type="C", iv_today=60.0, iv_prior=58.0,
                               d_iv=2.0, d_vs_atm=1.0, moneyness_pct=0.0),
            StrikeIvChangeRow(strike=95000.0, option_type="P", iv_today=61.0, iv_prior=60.0,
                               d_iv=1.0, d_vs_atm=0.0, moneyness_pct=0.0),
        ])
        bundle = _bundle(analysis, fixed_strike_vol=fixed_strike_vol)

        table = build_levels_table(bundle)

        assert table.delta_1d_iv_available is True
        assert table.rows[0].delta_1d_iv == 1.5

    def test_only_one_leg_matched_is_used_unaveraged(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        fixed_strike_vol = _fixed_strike_vol_result([
            StrikeIvChangeRow(strike=95000.0, option_type="C", iv_today=60.0, iv_prior=58.0,
                               d_iv=2.0, d_vs_atm=1.0, moneyness_pct=0.0),
        ])
        bundle = _bundle(analysis, fixed_strike_vol=fixed_strike_vol)

        table = build_levels_table(bundle)

        assert table.rows[0].delta_1d_iv == 2.0

    def test_stale_prior_gates_every_row_to_none(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        fixed_strike_vol = _fixed_strike_vol_result([
            StrikeIvChangeRow(strike=95000.0, option_type="C", iv_today=60.0, iv_prior=58.0,
                               d_iv=2.0, d_vs_atm=1.0, moneyness_pct=0.0),
        ], stale_prior=True)
        bundle = _bundle(analysis, fixed_strike_vol=fixed_strike_vol)

        table = build_levels_table(bundle)

        assert table.delta_1d_iv_available is False
        assert table.rows[0].delta_1d_iv is None

    def test_fixed_strike_vol_absent_entirely_is_none(self):
        analysis = _analysis([(95000.0, 0.0, 0.0)])
        bundle = _bundle(analysis, fixed_strike_vol=None)

        table = build_levels_table(bundle)

        assert table.delta_1d_iv_available is False
        assert table.rows[0].delta_1d_iv is None


class TestMaxPain:
    def test_max_pain_marker_set_only_on_matching_strike(self):
        analysis = _analysis(
            [(90000.0, 0.0, 0.0), (95000.0, 0.0, 0.0)], max_pain_strike=95000.0,
        )
        bundle = _bundle(analysis)

        table = build_levels_table(bundle)

        assert table.rows[0].is_max_pain is False
        assert table.rows[1].is_max_pain is True

    def test_no_max_pain_strike_marks_nothing(self):
        analysis = _analysis([(90000.0, 0.0, 0.0)], max_pain_strike=None)
        bundle = _bundle(analysis)

        table = build_levels_table(bundle)

        assert table.rows[0].is_max_pain is False
