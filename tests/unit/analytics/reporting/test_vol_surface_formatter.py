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
            skew=7.0, interpretation="Puts More Expensive - Hedging Demand",
        ),
        pc_by_moneyness=PutCallByMoneyness(
            atm=MoneynessBucket(call_oi=100.0, put_oi=150.0, range_label="±5%", ratio=1.5, bias="Slightly Bearish"),
            near_otm=MoneynessBucket(call_oi=50.0, put_oi=0.0, range_label="5-15%", ratio=float("inf"), bias="N/A"),
            far_otm=MoneynessBucket(call_oi=0.0, put_oi=0.0, range_label="15%+", ratio=0.0, bias="N/A"),
        ),
        second_order_greeks=SecondOrderGreeks(
            net_vanna=0.001234, net_charm=-0.005678,
            vanna_signal="IV drop → dealers buy underlying (bullish)",
            charm_signal="Time decay pushing delta negative (bearish drift)",
            skipped_instruments=2,
        ),
        atm_iv=81.0,
        vwap_iv=82.0,
        mark_iv_average=80.0,
    )
    defaults.update(overrides)
    return VolSurfaceResult(**defaults)


def test_skew_and_atm_iv_rendered():
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "25-Delta Skew: +7.0% (Puts More Expensive - Hedging Demand)" in text
    assert "25d Put: 85.0% (K=1,800)" in text
    assert "25d Call: 78.0% (K=2,000)" in text
    assert "ATM IV: 81.0%" in text


def test_skew_insufficient_data_when_skew_none():
    result = _make_result(
        skew_25d=SkewResult(
            put_25d_iv=None, call_25d_iv=None, put_25d_strike=None, call_25d_strike=None,
            skew=None, interpretation="Insufficient data",
        )
    )
    text = format_vol_surface_section(result, expiration="10MAR26")
    assert "25-Delta Skew: Insufficient data" in text


def test_vwap_iv_buyers_aggressive():
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "VWAP IV: 82.0%  |  Mark IV: 80.0%  |  Diff: +2.0%" in text
    assert "Buyers aggressive (VWAP > Mark)" in text


def test_vwap_iv_omitted_when_none():
    result = _make_result(vwap_iv=None, mark_iv_average=None)
    text = format_vol_surface_section(result, expiration="10MAR26")
    assert "VWAP IV" not in text


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
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "ATM (±5%):" in text
    assert "P/C = 1.50 (Slightly Bearish)" in text
    assert "Near-OTM (5-15%):" in text
    assert "N/A (No Call OI)" in text  # near_otm ratio is inf
    assert "Far-OTM (15%+):" in text


def test_second_order_greeks_rendered():
    text = format_vol_surface_section(_make_result(), expiration="10MAR26")
    assert "Net Vanna Exposure: +0.001234" in text
    assert "Net Charm Exposure: -0.005678" in text
    assert "Vanna Signal: IV drop → dealers buy underlying (bullish)" in text
    assert "Charm Signal: Time decay pushing delta negative (bearish drift)" in text
