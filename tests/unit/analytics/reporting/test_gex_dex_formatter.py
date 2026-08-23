"""
Unit tests for coding.core.analytics.reporting.gex_dex_formatter
(refactor_design_spec.md section T3).
"""

from coding.core.analytics.reporting.gex_dex_formatter import (
    format_aggregate_gex_dex_section,
    format_gamma_rolloff_section,
    format_gex_dex_section,
)
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
)
from coding.core.analytics.results.market_wide_results import (
    GammaRolloffResult,
    GammaRolloffRow,
)


def _make_result(**overrides) -> GexDexResult:
    defaults = dict(
        strike_rows=(
            GexDexStrikeRow(
                strike=95000.0, call_gamma=1.0, put_gamma=0.5, call_delta=0.6, put_delta=-0.4,
                call_oi=100.0, put_oi=50.0, net_gex=1_000_000.0, net_dex=0.2,
                net_gamma=0.5, cumulative_gex=500_000.0, cumulative_dex=-0.2,
            ),
            GexDexStrikeRow(
                strike=90000.0, call_gamma=0.2, put_gamma=1.5, call_delta=0.3, put_delta=-0.7,
                call_oi=20.0, put_oi=150.0, net_gex=-500_000.0, net_dex=-0.4,
                net_gamma=-1.3, cumulative_gex=-500_000.0, cumulative_dex=-0.4,
            ),
        ),
        cumulative_gex={90000.0: -500_000.0, 95000.0: 500_000.0},
        cumulative_dex={90000.0: -0.4, 95000.0: -0.2},
        key_levels=GexDexKeyLevels(
            call_resistance=GexDexLevel(strike=95000.0, net_gex=1_000_000.0),
            put_support=GexDexLevel(strike=90000.0, net_gex=-500_000.0),
            hvl=92000.0,
            gamma_flip=92000.0,
            cumulative_gex_zero_strike=92000.0,
            zero_gamma_level=91500.0,
            zero_gamma_crossings=(91500.0,),
            net_gex_at_spot=250_000.0,
            gamma_regime="POSITIVE",
        ),
        spot_price=93000.0,
        total_net_gex=500_000.0,
        total_net_dex=-0.2,
        currency="BTC",
        expiration_count=None,
    )
    defaults.update(overrides)
    return GexDexResult(**defaults)


def test_gex_dex_section_key_levels_and_totals():
    text = format_gex_dex_section(_make_result(), "BTC")
    assert "GEX/DEX ANALYSIS (Gamma & Delta Exposure)" in text
    assert "Spot Price: $93,000.00" in text
    assert "Call Resistance: $95,000 (Net GEX: +1,000,000.00 USD)" in text
    assert "Put Support: $90,000 (Net GEX: -500,000.00 USD)" in text
    assert "Cumulative GEX Zero Strike: $92,000" in text
    assert "NOT a re-priced gamma flip" in text

    # bugfix_spec.md Item 8: holder-side raw exposures, no actor named.
    # Wave-I-C Fix 4: the label states the ACTUAL assumption (every open
    # contract is held long) rather than claiming there is none -- summing
    # call-side and put-side exposure that way is itself a positioning
    # assumption, just a different one from the dealer-view heuristic below.
    assert "EXPOSURES -- HOLDER SIDE (assumes every open contract is held long)" in text
    assert "no positioning assumption" not in text
    # gamma_exposure_holder_total = row1(1.0+0.5) + row2(0.2+1.5) = 1.5 + 1.7 = 3.2
    assert "Gamma Exposure: +3.20 USD per 1% move" in text
    assert "Delta Exposure: -0.2000 BTC" in text  # = total_net_dex (holder), unchanged
    assert "Option holders are net short delta" in text

    # Assumed dealer view: total_net_gex unchanged; dealer delta follows
    # dealer_delta_exposure_total's own fallback (Wave-H-A): summed
    # -net_dex per strike_row -- row1 (-0.2) + row2 (-(-0.4)=+0.4) = +0.2.
    # Matches -total_net_dex (-(-0.2) = +0.2) exactly, per
    # GexDexCalculator's canonical negated-holder-sum convention. NOT
    # call_delta - put_delta (+2.0, cb1770a's regression -- reverted).
    assert "ASSUMED DEALER VIEW  (assumption: dealers long calls / short puts for" in text
    assert "Dealer Gamma:   +500,000.00 USD per 1% move" in text
    assert "POSITIVE: dealers long gamma, stabilizing (buy dips/sell rallies)" in text
    assert "Dealer Delta:   +0.2000 BTC" in text
    # bugfix_spec.md Item 8 fix-review (Critical #2, then round-2 Important
    # finding): mechanics only, present tense, no directional bull/bear call
    # and no spot-direction claim (that's gamma's story, told two lines up).
    # This sentence's sign matches the +0.2000 value (net long delta ->
    # hedge by selling) -- test_gex_dex_calculator.py::
    # TestDealerDeltaIsNegatedHolderSum covers fixtures where the sign
    # differs from the holder-side reading.
    assert "Dealers net long delta; hedging back to neutral means selling the underlying" in text


def test_gex_dex_section_dealer_delta_uses_negated_holder_sum_with_audit_numbers():
    """
    Wave-H-A (reverting Task G2-D fix 2 / commit cb1770a): reproduces the
    original audit's own worked numbers exactly -- call_delta*OI =
    +137.53, put_delta*OI = -78.53 (net_dex = holder delta = +59.0). The
    CORRECT negated-holder-sum convention (-net_dex = -59.0) prints
    "Dealers net short delta; hedging back to neutral means buying the
    underlying". cb1770a's regression (call_delta - put_delta = +216.06,
    algebraically guaranteed non-negative for any real book) printed the
    opposite, unconditionally -- this is the exact scenario the original
    audit found broken, now confirmed fixed by reverting to negation.
    """
    result = _make_result(
        strike_rows=(
            GexDexStrikeRow(
                strike=65_000.0, call_gamma=0.0, put_gamma=0.0,
                call_delta=137.53, put_delta=-78.53,
                call_oi=1.0, put_oi=1.0, net_gex=0.0, net_dex=59.0,
                net_gamma=0.0, cumulative_gex=0.0, cumulative_dex=59.0,
            ),
        ),
        cumulative_gex={65_000.0: 0.0},
        cumulative_dex={65_000.0: 59.0},
        total_net_gex=0.0,
        total_net_dex=59.0,
    )
    text = format_gex_dex_section(result, "BTC")

    assert "Dealer Delta:   -59.0000 BTC" in text
    assert "Dealers net short delta; hedging back to neutral means buying the underlying" in text
    assert "Dealers net long delta; hedging back to neutral means selling the underlying" not in text


def test_gex_dex_section_holder_block_names_no_actor():
    """T8.4: the report names an actor only in the dealer block."""
    text = format_gex_dex_section(_make_result(), "BTC")
    holder_block, dealer_block = text.split("ASSUMED DEALER VIEW")
    assert "Dealers" not in holder_block and "dealers" not in holder_block
    assert "assumption: dealers long calls / short puts" in dealer_block


def test_gex_dex_section_net_gex_footnote():
    text = format_gex_dex_section(_make_result(), "BTC")
    assert "Net GEX = assumed-dealer gamma exposure, USD per 1% spot move." in text


def test_gex_dex_section_gamma_profile_block():
    """bugfix_spec.md Item 2 / F2.3.3: the new re-priced gamma profile block."""
    text = format_gex_dex_section(_make_result(), "BTC")
    assert "GAMMA PROFILE (re-priced, sticky-strike):" in text
    assert "Net GEX at spot $93,000: +250,000.00 USD" in text
    assert "POSITIVE - dealers long gamma" in text
    assert "Zero Gamma Level:         $91,500  (-1.6% from spot)" in text
    assert "Other crossings:          none" in text


def test_gex_dex_section_gamma_profile_no_crossing():
    result = _make_result(
        key_levels=GexDexKeyLevels(
            call_resistance=GexDexLevel(strike=95000.0, net_gex=1_000_000.0),
            put_support=GexDexLevel(strike=90000.0, net_gex=-500_000.0),
            hvl=92000.0,
            gamma_flip=92000.0,
            cumulative_gex_zero_strike=92000.0,
            zero_gamma_level=None,
            zero_gamma_crossings=(),
            net_gex_at_spot=250_000.0,
            gamma_regime="POSITIVE",
        )
    )
    text = format_gex_dex_section(result, "BTC")
    assert "none within ±50% of spot (net GEX positive across the whole range)" in text


def test_gex_dex_section_no_completeness_line_when_complete():
    """Task G2-A (Wave G fresh audit, bug 2): the default fixture has no
    completeness gap -- no DATA COMPLETENESS line should render."""
    text = format_gex_dex_section(_make_result(), "BTC")
    assert "DATA COMPLETENESS" not in text


def test_gex_dex_section_completeness_line_when_gap_present():
    """
    Task G2-A (Wave G fresh audit, bug 2): a non-zero
    instruments_missing_gamma/oi_missing_gamma must be disclosed directly
    in the GEX/DEX section, not just gated on the header EVIDENCE line.
    """
    result = _make_result(instruments_missing_gamma=5, oi_missing_gamma=42.5)
    text = format_gex_dex_section(result, "BTC")
    assert "DATA COMPLETENESS" in text
    assert "5 instrument(s)" in text
    assert "42.5" in text


def test_aggregate_gex_dex_section_completeness_line_when_gap_present():
    result = _make_result(instruments_missing_gamma=3, oi_missing_gamma=10.0, expiration_count=4)
    text = format_aggregate_gex_dex_section(result, 93000.0, "BTC")
    assert "DATA COMPLETENESS" in text
    assert "3 instrument(s)" in text


def test_gex_dex_section_completeness_line_shows_100pct_when_no_strike_rows():
    """
    Wave G re-review, Important #2: an expiration where EVERY instrument
    failed its ticker fetch has no strike rows at all (nothing to divide
    a percentage by) but a real, positive oi_missing_gamma -- must show
    100%, not silently omit the percentage or crash.
    """
    result = _make_result(
        strike_rows=(), cumulative_gex={}, cumulative_dex={},
        key_levels=GexDexKeyLevels(call_resistance=None, put_support=None, hvl=None, gamma_flip=None),
        total_net_gex=0.0, total_net_dex=0.0,
        instruments_missing_gamma=2, oi_missing_gamma=800.0,
    )
    text = format_gex_dex_section(result, "BTC")
    assert "DATA COMPLETENESS" in text
    assert "2 instrument(s)" in text
    assert "100.0% of total OI" in text


def test_gex_dex_section_no_levels_found():
    result = _make_result(
        key_levels=GexDexKeyLevels(call_resistance=None, put_support=None, hvl=None, gamma_flip=None)
    )
    text = format_gex_dex_section(result, "BTC")
    assert "Call Resistance: None found" in text
    assert "Put Support: None found" in text
    assert "Cumulative GEX Zero Strike: Not detected" in text
    assert "Zero Gamma Level:         not available (insufficient data to re-price)" in text


def test_gex_dex_section_strike_table_ascending_with_notes():
    text = format_gex_dex_section(_make_result(), "BTC")
    idx_90k = text.index("90,000")
    idx_95k = text.index("95,000", idx_90k)
    assert idx_90k < idx_95k
    line_90k = next(l for l in text.splitlines() if l.strip().startswith("90,000"))
    assert "Put Support" in line_90k
    assert "Cumulative GEX Zero Strike" not in line_90k  # hvl is 92000, not a strike row here
    line_95k = next(l for l in text.splitlines() if l.strip().startswith("95,000"))
    assert "Call Resistance" in line_95k


def test_gex_dex_section_neutral_environment():
    result = _make_result(
        total_net_gex=0.0, total_net_dex=0.0,
        # Task G2-D fix 2: dealer_delta_exposure_total's fallback now sums
        # call_delta - put_delta from strike_rows (the only mathematically
        # sound source -- total_net_dex, call_delta + put_delta, cannot
        # derive it) instead of aliasing the overridden total_net_dex
        # directly. This fixture's default strike_rows are non-neutral by
        # construction (they exist to exercise the strike table below), so
        # the neutral-dealer-delta case needs its own explicit override,
        # same as total_net_gex/total_net_dex above.
        dealer_delta_exposure_total=0.0,
    )
    text = format_gex_dex_section(result, "BTC")
    assert "-> NEUTRAL" in text  # dealer gamma
    assert "Dealers delta-neutral" in text
    assert "Option holders are delta-neutral" in text


def test_aggregate_gex_dex_section_has_expiration_count_and_no_strike_table():
    result = _make_result(expiration_count=5)
    text = format_aggregate_gex_dex_section(result, spot_price=93000.0, currency="BTC")
    assert "MARKET-WIDE GEX/DEX LEVELS (All 5 Expirations Aggregated)" in text
    assert "Spot Price: $93,000.00" in text
    assert "GEX/DEX BY STRIKE" not in text  # no per-strike table in the aggregate section
    assert "Dealer Gamma:   +500,000.00 USD per 1% move" in text


class TestFormatGammaRolloffSection:
    """
    institutional_metrics_spec.md section 5 / Task C6 report rendering.
    Uses the spec's own T5.1/T5.2 worked numbers directly.
    """

    def _t5_1_result(self) -> GammaRolloffResult:
        return GammaRolloffResult(
            rows=(
                GammaRolloffRow(
                    expiration="25JUL26", dte_days=0.6, net_gex=30_000_000.0,
                    share_pct=30.0, cum_share_pct=30.0, cum_net_gex=30_000_000.0,
                ),
                GammaRolloffRow(
                    expiration="31JUL26", dte_days=6.6, net_gex=50_000_000.0,
                    share_pct=50.0, cum_share_pct=80.0, cum_net_gex=80_000_000.0,
                ),
                GammaRolloffRow(
                    expiration="28AUG26", dte_days=34.6, net_gex=20_000_000.0,
                    share_pct=20.0, cum_share_pct=100.0, cum_net_gex=100_000_000.0,
                ),
            ),
            gamma_cliff_7d=True, cum_share_7d=80.0, cum_share_30d=100.0,
            gross_total=100_000_000.0,
        )

    def test_header_and_table_rows(self):
        text = format_gamma_rolloff_section(self._t5_1_result())
        assert "GAMMA ROLL-OFF" in text
        assert "25JUL26" in text and "31JUL26" in text and "28AUG26" in text
        assert "30.0%" in text
        assert "80.0%" in text
        assert "100.0%" in text

    def test_gamma_cliff_flag_rendered_with_disclaimer(self):
        text = format_gamma_rolloff_section(self._t5_1_result())
        assert "GAMMA CLIFF" in text
        assert "80.0% of gamma mass expires within 7 days" in text
        # Spec 5(b): "It is a presentation flag, not a trading signal --
        # state that on the line."
        assert "not a trading signal" in text

    def test_signed_net_contribution_line_uses_7d_boundary_cum_net_gex(self):
        text = format_gamma_rolloff_section(self._t5_1_result())
        assert "+80,000,000" in text

    def test_seven_day_boundary_row_marked(self):
        """The last row within the 7d window (31JUL26, dte 6.6) gets the
        boundary marker; the first (25JUL26, also <=7d) does not."""
        text = format_gamma_rolloff_section(self._t5_1_result())
        line_31jul = next(l for l in text.splitlines() if "31JUL26" in l)
        line_25jul = next(l for l in text.splitlines() if "25JUL26" in l)
        assert "7d" in line_31jul
        assert "<-- 7d" not in line_25jul

    def test_mixed_signs_no_flag_disclaimer_when_not_flagged(self):
        """T5.2: shares still on |net_gex| (sum to 100%), cum_net_gex signed
        and non-monotone; when NOT flagged, no GAMMA CLIFF text and the
        signed 7d contribution is negative."""
        result = GammaRolloffResult(
            rows=(
                GammaRolloffRow(
                    expiration="25JUL26", dte_days=0.6, net_gex=30_000_000.0,
                    share_pct=30.0, cum_share_pct=30.0, cum_net_gex=30_000_000.0,
                ),
                GammaRolloffRow(
                    expiration="31JUL26", dte_days=6.6, net_gex=-50_000_000.0,
                    share_pct=50.0, cum_share_pct=80.0, cum_net_gex=-20_000_000.0,
                ),
                GammaRolloffRow(
                    expiration="28AUG26", dte_days=34.6, net_gex=20_000_000.0,
                    share_pct=20.0, cum_share_pct=100.0, cum_net_gex=0.0,
                ),
            ),
            gamma_cliff_7d=True, cum_share_7d=80.0, cum_share_30d=100.0,
            gross_total=100_000_000.0,
        )
        text = format_gamma_rolloff_section(result)
        assert "signed" in text.lower()
        assert "-20,000,000" in text

    def test_no_flag_below_threshold(self):
        result = GammaRolloffResult(
            rows=(
                GammaRolloffRow(
                    expiration="28AUG26", dte_days=34.6, net_gex=100_000_000.0,
                    share_pct=100.0, cum_share_pct=100.0, cum_net_gex=100_000_000.0,
                ),
            ),
            gamma_cliff_7d=False, cum_share_7d=0.0, cum_share_30d=100.0,
            gross_total=100_000_000.0,
        )
        text = format_gamma_rolloff_section(result)
        assert "GAMMA CLIFF" not in text
        # No expiry rolls off within 7d -- signed contribution is 0.
        assert "0" in text.split("Signed net contribution")[1].splitlines()[0]

    def test_no_gamma_edge_case_renders_placeholder_not_crash(self):
        """Spec 5(c): gross_total == 0 -> print 'no gamma', no table, no
        flag, no crash on the None share values."""
        result = GammaRolloffResult(
            rows=(
                GammaRolloffRow(
                    expiration="25JUL26", dte_days=0.6, net_gex=0.0,
                    share_pct=None, cum_share_pct=None, cum_net_gex=0.0,
                ),
            ),
            gamma_cliff_7d=False, cum_share_7d=None, cum_share_30d=None,
            gross_total=0.0,
        )
        text = format_gamma_rolloff_section(result)  # must not raise
        assert "no gamma" in text.lower()
        assert "GAMMA CLIFF" not in text

    def test_zero_expiries_renders_placeholder_not_crash(self):
        result = GammaRolloffResult(
            rows=(), gamma_cliff_7d=False, cum_share_7d=None, cum_share_30d=None,
            gross_total=0.0,
        )
        text = format_gamma_rolloff_section(result)  # must not raise
        assert "no gamma" in text.lower()
