"""
Unit tests for coding.core.analytics.reporting.vol_surface_formatter
(refactor_design_spec.md section T3).
"""

from coding.core.analytics.reporting.vol_surface_formatter import format_vol_surface_section
from coding.core.analytics.results.vol_surface_results import (
    IvByStrikeRow,
    MoneynessBucket,
    PutCallByMoneyness,
    SecondOrderGreeks,
    SkewResult,
    VolSurfaceResult,
)


def _make_result(**overrides) -> VolSurfaceResult:
    defaults = dict(
        expiration="10MAR26",
        spot_price=1900.0,
        iv_by_strike=(
            IvByStrikeRow(strike=1900.0, option_type="C", mark_iv=80.0, delta=0.5, moneyness_pct=0.0),
            IvByStrikeRow(strike=1900.0, option_type="P", mark_iv=82.0, delta=-0.5, moneyness_pct=0.0),
            IvByStrikeRow(strike=5000.0, option_type="C", mark_iv=70.0, delta=0.1, moneyness_pct=163.0),
        ),
        skew_25d=SkewResult(
            put_25d_iv=85.0, call_25d_iv=78.0, put_25d_strike=1800.0, call_25d_strike=2000.0,
            put_25d_delta=-0.25, call_25d_delta=0.25,
            risk_reversal_25d=-7.0, interpretation="Puts Richer - Downside Hedging Demand",
        ),
        pc_by_moneyness=PutCallByMoneyness(
            atm=MoneynessBucket(call_oi=100.0, put_oi=150.0, range_label="±5%", ratio=1.5, bias="Slightly Bearish"),
            near_otm=MoneynessBucket(call_oi=50.0, put_oi=0.0, range_label="5-15%", ratio=float("inf"), bias="N/A"),
            far_otm=MoneynessBucket(call_oi=0.0, put_oi=0.0, range_label="15%+", ratio=0.0, bias="N/A"),
        ),
        second_order_greeks=SecondOrderGreeks(
            vanna_exposure_holder=0.001234, charm_exposure_holder=-0.005678,
            # Task C5 review fix round 2: dealer_vanna_exposure/
            # dealer_charm_exposure are now REQUIRED (no default) --
            # explicit values here, deliberately NOT the negation of the
            # holder sum above, so this fixture cannot be mistaken for
            # (or silently drift back to) the retired negation convention.
            # These formatter tests don't exercise the real call/put-split
            # derivation (that's test_volatility_surface_calculator.py's
            # job) -- only that whatever value is here renders correctly.
            dealer_vanna_exposure=0.002468, dealer_charm_exposure=-0.003456,
            vanna_signal="IV drop → dealers buy underlying (bullish)",
            charm_signal="Time decay pushing delta negative (bearish drift)",
            skipped_instruments=2,
        ),
        atm_iv=81.0,
        vwap_iv=82.0,
        mark_iv_average=80.0,
        traded_instrument_count=3,
    )
    defaults.update(overrides)
    return VolSurfaceResult(**defaults)


def test_skew_and_atm_iv_rendered():
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    # bugfix_spec.md Item 9: market convention (call - put), explicit sign label.
    assert "25Δ Risk Reversal (call − put): -7.0% (Puts Richer - Downside Hedging Demand)" in text
    assert "25d Put: 85.0% (K=1,800, Δ=-0.250)" in text
    assert "25d Call: 78.0% (K=2,000, Δ=+0.250)" in text
    assert "ATM IV: 81.0%" in text


def test_skew_insufficient_data_when_skew_none():
    result = _make_result(
        skew_25d=SkewResult(
            put_25d_iv=None, call_25d_iv=None, put_25d_strike=None, call_25d_strike=None,
            risk_reversal_25d=None, interpretation="Insufficient data",
        )
    )
    text = format_vol_surface_section(result, expiration="10MAR26")
    assert "25Δ Risk Reversal: Insufficient data" in text


def test_vwap_iv_never_rendered_here():
    """
    institutional_metrics_spec.md section 9 (Task D2): "VWAP IV vs mark IV
    -> one line, matched-baseline only". The old two-line VWAP/Matched-
    Mark/Diff + aggression-label block is deleted from this section
    entirely -- its one-line replacement is
    expiry_formatter.format_context_section's CONTEXT block (see
    tests/unit/analytics/reporting/test_expiry_formatter.py's
    test_context_vwap_iv_gap_* tests). This must hold regardless of
    whether vwap_iv/mark_iv_average are present on the result.
    """
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "VWAP IV" not in text
    assert "Matched Mark IV" not in text
    assert "aggressive" not in text.lower()

    result_no_vwap = _make_result(vwap_iv=None, mark_iv_average=None)
    text_no_vwap = format_vol_surface_section(result_no_vwap, expiration="10MAR26")
    assert "VWAP IV" not in text_no_vwap


def test_iv_by_strike_merges_call_and_put_rows_per_strike():
    """iv_by_strike holds one row per instrument; the table re-merges by strike."""
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    line = next(l for l in text.splitlines() if l.strip().startswith("1,900"))
    assert "80.0%" in line  # call IV
    assert "82.0%" in line  # put IV


def test_iv_by_strike_filters_beyond_30_pct_of_spot():
    """The 5000-strike row (163% away from spot 1900) is filtered out of the table."""
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "5,000" not in text


def test_pc_by_moneyness_buckets_and_na_ratio():
    """
    bugfix_spec.md Item 10 / C1 review Important #3: the per-bucket
    directional label (bias) is no longer printed -- it still comes from
    the discredited hard-coded 0.7/1.0/1.3 thresholds and no verified
    per-bucket history exists to reclassify it, so the raw ratio alone is
    shown until that data source exists.
    """
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "ATM (±5%):" in text
    assert "P/C = 1.50" in text
    assert "Slightly Bearish" not in text
    assert "Near-OTM (5-15%):" in text
    assert "N/A (No Call OI)" in text  # near_otm ratio is inf
    assert "Far-OTM (15%+):" in text


def test_second_order_greeks_text_removed_superseded_by_exposure_profile_section():
    """
    institutional_metrics_spec.md section 4(c) / task C5: "Report --
    replaces the aggregate vanna/charm advice block entirely." The old
    "SECOND-ORDER GREEKS" text (aggregate scalar, tau via gamma-inversion)
    no longer renders here -- superseded by the new per-strike "VANNA /
    CHARM PROFILE" section (exposure_profile_formatter.format_exposure_
    profile_section, wired in by report_formatter.py). This is a
    text-rendering removal only: VolSurfaceResult.second_order_greeks
    itself is untouched (still feeds synthesis.py's scoring engine) --
    verified directly on the result object, not through this formatter.
    """
    result = _make_result()
    text = format_vol_surface_section(result, expiration="10MAR26")

    assert "SECOND-ORDER GREEKS" not in text
    assert "Vanna Exposure" not in text
    assert "ASSUMED DEALER VIEW" not in text
    assert "Vanna Signal" not in text

    # The underlying data model is unchanged.
    second = result.second_order_greeks
    assert second.vanna_exposure_holder == 0.001234
    assert second.charm_exposure_holder == -0.005678
    assert second.dealer_vanna_exposure == 0.002468
    assert second.dealer_charm_exposure == -0.003456
