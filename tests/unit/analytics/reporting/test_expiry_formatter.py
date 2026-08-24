"""
Unit tests for coding.core.analytics.reporting.expiry_formatter
(refactor_design_spec.md section T3; institutional_metrics_spec.md section 9
/ Task D2 report restructure).

format_expiration_section now renders only the instrument-count summary and
the raw OI/volume-by-strike table. MAX PAIN, PUT/CALL RATIO, VOLUME
STATISTICS, MONEYNESS ANALYSIS and SUPPORT/RESISTANCE LEVELS (all full
multi-line blocks, several carrying trend arrows against a single prior
snapshot) are removed -- their one-line replacements live in
format_context_section, rendered by report_formatter.py as the LAST section
in each expiration's block (spec 9(b) per-expiry order item 8). Trend arrows
against a single prior DB snapshot (format_trend_delta) are deleted
everywhere in this module (spec 9's removals table, row "Trend arrows vs 1
prior snapshot -> delete everywhere").
"""

from datetime import datetime, timedelta, timezone

from coding.core.analytics.reporting.expiry_formatter import (
    format_context_section,
    format_expiration_section,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    LevelRef,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    StrikeOiRow,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.vol_surface_results import (
    MoneynessBucket,
    PutCallByMoneyness,
    SecondOrderGreeks,
    SkewResult,
    VolSurfaceResult,
)

SPOT_PRICE = 64405.02

# institutional_metrics_spec.md section 9 independent review round 2,
# Important #1: format_context_section takes now_utc as an explicit
# parameter (never reads the clock itself) -- these tests use a FIXED
# reference instant, not datetime.now(timezone.utc), so they are fully
# deterministic and independent of wall-clock time (matching how
# production supplies now_utc from OnChainAnalysisService.fetch_and_
# analyze's own frozen-in-tests clock read, never from this module).
NOW_UTC = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


def _expiry_string(days_ahead: float) -> str:
    """
    Deribit "DDMONYY" expiration string ``days_ahead`` days from ``NOW_UTC``
    -- used so tests exercising the Max Pain CONTEXT line's expiry-week
    gate (institutional_metrics_spec.md section 9 independent review
    ruling; see expiry_formatter._is_expiry_week) can construct expirations
    at a known distance from the fixed reference instant these tests pass
    as ``now_utc``.
    """
    dt = NOW_UTC + timedelta(days=days_ahead)
    return dt.strftime("%d%b%y").upper()


def _leg(itm_oi, otm_oi, itm_notional, otm_notional, itm_pct, otm_pct):
    return MoneynessLeg(
        itm_oi=itm_oi, otm_oi=otm_oi, total_oi=itm_oi + otm_oi,
        itm_notional=itm_notional, otm_notional=otm_notional,
        total_notional=itm_notional + otm_notional, itm_pct=itm_pct, otm_pct=otm_pct,
    )


def _make_analysis(**overrides) -> ExpirationAnalysisResult:
    defaults = dict(
        # Within the expiry-week gate by default (2 days out) so every
        # existing test asserting the Max Pain CONTEXT line renders keeps
        # working regardless of today's date -- see _expiry_string.
        expiration=_expiry_string(2),
        underlying_price=SPOT_PRICE,
        total_instruments=3,
        call_count=1,
        put_count=2,
        strike_rows=(
            StrikeOiRow(strike=60000.0, call_oi=0.0, put_oi=100.0, call_volume=0.0, put_volume=10.0),
            StrikeOiRow(strike=65000.0, call_oi=50.0, put_oi=20.0, call_volume=5.0, put_volume=2.0),
        ),
        max_pain=MaxPainResult(
            max_pain_strike=65000.0, pain_by_strike={60000.0: 500.0, 65000.0: 100.0}, min_pain_value=100.0
        ),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=50.0, total_put_oi=120.0, ratio=2.4, bias="Strong Bearish"
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=5.0, total_put_volume=12.0, total_volume=17.0, volume_ratio=2.4
        ),
        moneyness=MoneynessResult(
            calls=_leg(0.0, 50.0, 0.0, 3_000_000.0, 0.0, 100.0),
            puts=_leg(20.0, 100.0, 1_200_000.0, 6_000_000.0, 16.67, 83.33),
            totals=_leg(20.0, 150.0, 1_200_000.0, 9_000_000.0, 11.76, 88.24),
            oi_skew="Heavy OTM (Speculative)",
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=(LevelRef(strike=65000.0, open_interest=50.0),),
            support_levels=(LevelRef(strike=60000.0, open_interest=100.0),),
            short_term_resistance=LevelRef(strike=65000.0, open_interest=50.0),
            short_term_support=LevelRef(strike=60000.0, open_interest=100.0),
        ),
    )
    defaults.update(overrides)
    return ExpirationAnalysisResult(**defaults)


def _make_vol_surface(**overrides) -> VolSurfaceResult:
    defaults = dict(
        expiration="14AUG26",
        spot_price=SPOT_PRICE,
        atm_iv=60.0,
        skew_25d=SkewResult(
            put_25d_iv=None, call_25d_iv=None, put_25d_strike=None,
            call_25d_strike=None, interpretation="insufficient chain",
        ),
        iv_by_strike=(),
        pc_by_moneyness=PutCallByMoneyness(
            atm=MoneynessBucket(range_label="+/-5%", call_oi=0, put_oi=0, ratio=0.0, bias="N/A"),
            near_otm=MoneynessBucket(range_label="5-15%", call_oi=0, put_oi=0, ratio=0.0, bias="N/A"),
            far_otm=MoneynessBucket(range_label="15%+", call_oi=0, put_oi=0, ratio=0.0, bias="N/A"),
        ),
        second_order_greeks=SecondOrderGreeks(
            vanna_exposure_holder=0.0, charm_exposure_holder=0.0,
            vanna_signal="N/A", charm_signal="N/A", skipped_instruments=0,
            dealer_vanna_exposure=0.0, dealer_charm_exposure=0.0,
        ),
        vwap_iv=82.0,
        mark_iv_average=80.0,
        traded_instrument_count=10,
    )
    defaults.update(overrides)
    return VolSurfaceResult(**defaults)


# ---------------------------------------------------------------------------
# format_expiration_section — summary + strike table only
# ---------------------------------------------------------------------------

def test_summary_line():
    text = format_expiration_section(_make_analysis())
    assert "Total Instruments: 3 (1 Calls, 2 Puts)" in text


def test_no_max_pain_pcr_volume_moneyness_or_support_resistance_blocks():
    """institutional_metrics_spec.md section 9: these full blocks are gone
    from format_expiration_section -- their one-liners live in
    format_context_section instead."""
    text = format_expiration_section(_make_analysis())
    assert "MAX PAIN ANALYSIS" not in text
    assert "PUT/CALL RATIO" not in text
    assert "VOLUME STATISTICS" not in text
    assert "MONEYNESS ANALYSIS" not in text
    assert "SUPPORT/RESISTANCE LEVELS" not in text
    assert "RESISTANCE (Top 3" not in text
    assert "SUPPORT (Top 3" not in text
    assert "Heavy OTM" not in text
    assert "Speculative" not in text


def test_strike_table_lists_strikes_ascending_with_max_pain_note_only():
    text = format_expiration_section(_make_analysis())
    idx_60k = text.index("60,000")
    idx_65k = text.index("65,000", idx_60k)
    assert idx_60k < idx_65k  # ascending order

    lines = text.splitlines()
    line_60k = next(l for l in lines if l.strip().startswith("60,000"))
    line_65k = next(l for l in lines if l.strip().startswith("65,000"))
    # institutional_metrics_spec.md section 9: raw-OI top-3 support/
    # resistance annotations are deleted from the strike table -- merged
    # into the existing (GEX-based) key levels shown elsewhere.
    assert "Support" not in line_60k
    assert "Resistance" not in line_65k
    assert "<< MAX PAIN" in line_65k


def test_strike_table_no_notes_when_not_max_pain():
    analysis = _make_analysis(
        strike_rows=(StrikeOiRow(strike=61000.0, call_oi=1.0, put_oi=1.0, call_volume=0.1, put_volume=0.1),),
        max_pain=MaxPainResult(max_pain_strike=65000.0, pain_by_strike={}, min_pain_value=0.0),
    )
    text = format_expiration_section(analysis)
    line = next(l for l in text.splitlines() if l.strip().startswith("61,000"))
    assert line.rstrip().endswith("0.10")  # no trailing notes text after put volume column


# ---------------------------------------------------------------------------
# format_context_section
# ---------------------------------------------------------------------------

def test_context_header_present():
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert text.startswith("CONTEXT\n" + "-" * 80 + "\n")


def test_context_max_pain_one_line_no_trend():
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Max Pain: $65,000" in text
    assert "Trend (Max Pain)" not in text
    assert "↑" not in text
    assert "↓" not in text


def test_context_max_pain_na_when_no_strike():
    analysis = _make_analysis(
        max_pain=MaxPainResult(max_pain_strike=None, pain_by_strike={}, min_pain_value=0.0)
    )
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Max Pain: N/A" in text


def test_context_max_pain_suppressed_outside_expiry_week():
    """
    institutional_metrics_spec.md section 9 independent review ruling:
    Max Pain -> one line, expiry-week only, is a TIME-WINDOW gate. An
    expiration 60 days out must not print a Max Pain line at all (not
    even N/A) -- max pain's pinning thesis isn't meaningful that far out.
    """
    analysis = _make_analysis(expiration=_expiry_string(60))
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Max Pain:" not in text


def test_context_max_pain_rendered_well_within_the_threshold():
    # Deliberately not testing the exact 7-day boundary here: the
    # expiration string truncates to a calendar date (re-parsed at 08:00
    # UTC settlement), so the exact fractional DTE from "right now" to
    # that boundary depends on what time of day the test happens to run
    # -- a boundary test would be flaky. 5 days out is unambiguously
    # inside the window regardless of time-of-day.
    analysis = _make_analysis(expiration=_expiry_string(5))
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Max Pain: $65,000" in text


def test_context_max_pain_distance_matches_synthesis_convention_above_spot():
    """
    Task Wave-J-C Fix 1: this line used to compute
    (spot - max_pain) / max_pain * 100 -- opposite sign AND max_pain as the
    percentage base -- silently disagreeing with synthesis.py's
    (max_pain - spot) / spot * 100 for the same expiry/strike/spot. Pin the
    now-shared convention: max pain ABOVE spot is a POSITIVE percentage.
    max_pain_strike=65000 > SPOT_PRICE=64405.02 -> (65000-64405.02)/64405.02*100.
    """
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    expected_pct = (65000.0 - SPOT_PRICE) / SPOT_PRICE * 100
    assert expected_pct > 0
    assert f"Max Pain: $65,000  ({expected_pct:+.2f}% from spot)" in text


def test_context_max_pain_distance_negative_when_below_spot():
    """Max pain BELOW spot must render a NEGATIVE percentage."""
    analysis = _make_analysis(
        max_pain=MaxPainResult(max_pain_strike=60000.0, pain_by_strike={}, min_pain_value=0.0)
    )
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    expected_pct = (60000.0 - SPOT_PRICE) / SPOT_PRICE * 100
    assert expected_pct < 0
    assert f"Max Pain: $60,000  ({expected_pct:+.2f}% from spot)" in text


def test_context_other_lines_still_render_when_max_pain_suppressed():
    """Suppressing Max Pain must not suppress the rest of CONTEXT."""
    analysis = _make_analysis(expiration=_expiry_string(60))
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "P/C Ratio:" in text
    assert "Moneyness:" in text
    assert "Volume P/C:" in text
    assert "VWAP-IV gap:" in text


def test_context_pcr_percentile_no_bias_word():
    """institutional_metrics_spec.md section 9: PCR hard-coded/percentile-
    derived bias word labels ("Strong Bullish" etc) are deleted -- only the
    raw value + percentile print, "p"+digits convention (T9.2 acceptance:
    must not contain "Strong Bullish", must contain a "p"+digits marker on
    the PCR line)."""
    analysis = _make_analysis(
        put_call_ratio=PutCallRatioResult(
            total_call_oi=50.0, total_put_oi=120.0, ratio=2.4, bias="Strong Bearish",
            percentile_90d=98.3, history_n_90d=705,
        )
    )
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "P/C Ratio: 2.40  p98 (90d history, n=705)" in text
    assert "Strong Bearish" not in text
    assert "->" not in text.split("P/C Ratio")[1].splitlines()[0]


def test_context_pcr_insufficient_history():
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "P/C Ratio: 2.40  (n=0 - insufficient history for a percentile)" in text


def test_context_pcr_na_when_infinite():
    analysis = _make_analysis(
        put_call_ratio=PutCallRatioResult(
            total_call_oi=0.0, total_put_oi=50.0, ratio=float("inf"), bias="Strong Bearish"
        )
    )
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "P/C Ratio: N/A (No Call OI)" in text


def test_context_moneyness_one_line_itm_pct():
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Moneyness: ITM calls 0.0%  |  ITM puts 16.7%" in text


def test_context_volume_pc_one_line():
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Volume P/C: 2.40" in text


def test_context_volume_pc_na_when_infinite():
    analysis = _make_analysis(
        volume_stats=VolumeStatsResult(
            total_call_volume=0.0, total_put_volume=5.0, total_volume=5.0, volume_ratio=float("inf")
        )
    )
    text = format_context_section(analysis, SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "Volume P/C: N/A (No Call Volume)" in text


def test_context_vwap_iv_gap_one_line():
    text = format_context_section(_make_analysis(), SPOT_PRICE, _make_vol_surface(), NOW_UTC)
    assert "VWAP-IV gap: +2.0%  (VWAP 82.0% vs Matched Mark 80.0%, 10 instr)" in text


def test_context_vwap_iv_gap_none_when_vol_surface_missing():
    text = format_context_section(_make_analysis(), SPOT_PRICE, None, NOW_UTC)
    assert "VWAP-IV gap: N/A (no vol surface data)" in text


def test_context_vwap_iv_gap_suppressed_low_instrument_count():
    vs = _make_vol_surface(traded_instrument_count=1)
    text = format_context_section(_make_analysis(), SPOT_PRICE, vs, NOW_UTC)
    assert "VWAP-IV gap: n/a (only 1 instrument(s) traded)" in text
