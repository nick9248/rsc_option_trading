"""
Unit tests for VolatilitySurfaceCalculator.

T4 (refactor_design_spec.md): calculate() returns the typed VolSurfaceResult
(coding/core/analytics/results/vol_surface_results.py) instead of a dict —
assertions here use attribute access. ``iv_by_strike`` is now one row per
INSTRUMENT; use ``result.merged_iv_by_strike()`` for the legacy per-strike
{call_iv, put_iv} view.
"""

import logging

import pytest
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator


def _make_instrument(
    strike, option_type, mark_iv=None, delta=None, gamma=None,
    theta=None, vega=None, open_interest=100, volume=10
):
    """Helper to create instrument dicts for testing."""
    return {
        "instrument_name": f"BTC-28MAR26-{int(strike)}-{option_type}",
        "expiration": "28MAR26",
        "strike": strike,
        "option_type": option_type,
        "open_interest": open_interest,
        "volume": volume,
        "volume_usd": volume * 90000,
        "mark_price": 0.05,
        "mark_iv": mark_iv,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "underlying_price": 90000,
    }


@pytest.fixture
def sample_instruments():
    """Create a realistic set of instruments around BTC at 90000."""
    instruments = []
    spot = 90000

    # Calls
    for strike, iv, delta, gamma, theta, vega in [
        (80000, 85.0, 0.75, 0.00001, -50, 100),
        (85000, 72.0, 0.55, 0.00002, -60, 120),
        (90000, 65.0, 0.50, 0.00003, -70, 130),
        (95000, 68.0, 0.25, 0.00002, -55, 110),
        (100000, 75.0, 0.10, 0.00001, -40, 80),
    ]:
        instruments.append(_make_instrument(
            strike, "C", mark_iv=iv, delta=delta,
            gamma=gamma, theta=theta, vega=vega, open_interest=500
        ))

    # Puts
    for strike, iv, delta, gamma, theta, vega in [
        (80000, 88.0, -0.25, 0.00001, -45, 95),
        (85000, 74.0, -0.45, 0.00002, -55, 115),
        (90000, 66.0, -0.50, 0.00003, -65, 125),
        (95000, 70.0, -0.75, 0.00002, -50, 105),
        (100000, 78.0, -0.90, 0.00001, -35, 75),
    ]:
        instruments.append(_make_instrument(
            strike, "P", mark_iv=iv, delta=delta,
            gamma=gamma, theta=theta, vega=vega, open_interest=600
        ))

    return instruments


class TestVolatilitySurfaceCalculator:
    """Tests for VolatilitySurfaceCalculator."""

    def test_calculate_returns_all_fields(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        assert result.iv_by_strike is not None
        assert result.skew_25d is not None
        assert result.pc_by_moneyness is not None
        assert result.second_order_greeks is not None
        assert result.atm_iv is not None

    def test_iv_by_strike(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        # One row per instrument: 5 calls + 5 puts
        assert len(result.iv_by_strike) == 10

        merged = result.merged_iv_by_strike()
        assert len(merged) == 5  # 5 unique strikes

        # Check ATM strike has both call and put IV
        atm_entry = merged[90000.0]
        assert atm_entry["call_iv"] == 65.0
        assert atm_entry["put_iv"] == 66.0

    def test_25_delta_skew(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        skew = result.skew_25d
        assert skew.put_25d_iv is not None
        assert skew.call_25d_iv is not None
        # Put 25d should be at strike 80000 (delta -0.25)
        assert skew.put_25d_strike == 80000
        # Call 25d should be at strike 95000 (delta 0.25)
        assert skew.call_25d_strike == 95000
        # bugfix_spec.md Item 9: risk_reversal_25d = call IV - put IV =
        # 68 - 88 = -20 (market convention); put_over_call_skew_25d = +20
        # (the legacy sign); skew (deprecated alias) == put_over_call_skew_25d.
        assert skew.risk_reversal_25d == pytest.approx(-20.0)
        assert skew.put_over_call_skew_25d == pytest.approx(20.0)
        assert skew.skew == pytest.approx(20.0)

    def test_25_delta_skew_insufficient_data(self):
        instruments = [
            _make_instrument(90000, "C", mark_iv=65.0, delta=0.50)
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        result = calc.calculate()

        skew = result.skew_25d
        assert skew.risk_reversal_25d is None
        assert skew.skew is None
        assert "Insufficient" in skew.interpretation

    def test_pc_by_moneyness(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        pc = result.pc_by_moneyness

        # ATM bucket (±5%) should contain 90000 strike
        atm = pc.atm
        assert atm.call_oi > 0
        assert atm.put_oi > 0
        assert atm.ratio is not None
        assert atm.bias is not None

    def test_second_order_greeks(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        greeks = result.second_order_greeks
        # Values should be non-zero with our test data
        assert greeks.vanna_exposure_holder != 0
        assert greeks.charm_exposure_holder != 0
        # bugfix_spec.md Item 8: dealer_* is the exact negation of the
        # holder-side sum.
        assert greeks.dealer_vanna_exposure == pytest.approx(-greeks.vanna_exposure_holder)
        assert greeks.dealer_charm_exposure == pytest.approx(-greeks.charm_exposure_holder)
        assert greeks.skipped_instruments >= 0

        # bugfix_spec.md Item 8 fix-review (Important #7 -- T8.3's other
        # half, restored): vanna_signal/charm_signal text must be
        # genuinely DERIVED from the DEALER field's sign, not the holder
        # sum's -- this is the actual behavioral correctness the item
        # fixes, and asserting only dealer == -holder (above) doesn't
        # independently verify it (a regression that swapped which field
        # feeds the signal text would still pass that assertion).
        if greeks.dealer_vanna_exposure > 0:
            assert "dealers buy underlying (bullish)" in greeks.vanna_signal
        else:
            assert "dealers sell underlying (bearish)" in greeks.vanna_signal

        if greeks.dealer_charm_exposure > 0:
            assert "pushing delta positive (bullish drift)" in greeks.charm_signal
        else:
            assert "pushing delta negative (bearish drift)" in greeks.charm_signal

    def test_second_order_greeks_exception_is_logged_with_instrument_context(self, caplog):
        """
        M5 (code_quality_review.md): the vanna/charm loop's ``except
        Exception: skipped_instruments += 1; continue`` used to discard
        per-instrument failures with zero logging -- a systematic input
        problem (e.g. every mark_iv malformed) yielded a plausible-looking
        net vanna of 0.0 with no diagnostics. Must now log a warning naming
        the failing instrument.
        """
        instruments = [
            _make_instrument(
                90000, "C", mark_iv="not-a-number", delta=0.5,
                gamma=0.00003, theta=-70, vega=130,
            )
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")

        with caplog.at_level(logging.WARNING):
            greeks = calc._calculate_second_order_greeks()

        assert greeks["skipped_instruments"] == 1
        # task A7 review: the original assertion's "or 'instrument' in
        # message.lower()" clause would pass even if the instrument name
        # rendered as "<unknown>", since the log format string contains the
        # word "instrument" unconditionally. Require the actual identifier.
        assert any("BTC-28MAR26-90000-C" in record.message for record in caplog.records)

    def test_atm_iv(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        atm_iv = result.atm_iv
        assert atm_iv is not None
        # ATM IV should be average of call (65) and put (66) at strike 90000
        assert atm_iv == pytest.approx(65.5)

    def test_atm_iv_no_data(self):
        instruments = [
            _make_instrument(90000, "C", mark_iv=None, delta=0.50)
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        result = calc.calculate()
        assert result.atm_iv is None

    # NOTE (task A7, carried finding #1): test_generate_report_section and
    # test_vwap_iv_in_report used to live here, covering
    # VolatilitySurfaceCalculator.generate_report_section() (deleted -- zero
    # production callers). Equivalent (better) coverage now lives in
    # tests/unit/analytics/reporting/test_vol_surface_formatter.py, which
    # exercises the actual live render path (format_vol_surface_section)
    # against a typed VolSurfaceResult: test_skew_and_atm_iv_rendered,
    # test_vwap_iv_buyers_aggressive, test_iv_by_strike_merges_call_and_
    # put_rows_per_strike, test_pc_by_moneyness_buckets_and_na_ratio,
    # test_second_order_greeks_rendered.

    def test_zero_spot_price(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        result = calc.calculate()
        # Should not crash
        assert result is not None

    def test_zero_spot_pc_by_moneyness_buckets_have_ratio_and_bias(self, sample_instruments):
        """
        bugfix_spec.md H2: the spot<=0 early return in _calculate_pc_by_moneyness()
        must populate ratio/bias on every bucket, not just call_oi/put_oi/range,
        otherwise generate_report_section() KeyErrors on bucket["ratio"].

        _calculate_pc_by_moneyness() stays a dict-returning internal helper
        (not part of the T4 public typed API) — calculate() wraps its output
        into PutCallByMoneyness/MoneynessBucket.
        """
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        buckets = calc._calculate_pc_by_moneyness()

        for bucket_name in ("atm", "near_otm", "far_otm"):
            bucket = buckets[bucket_name]
            assert bucket["ratio"] == 0.0
            assert bucket["bias"] == "N/A"

    # NOTE (task A7, carried finding #1): test_zero_spot_report_generation_
    # does_not_raise used to live here, covering the H2 zero-spot fix via
    # the now-deleted generate_report_section(). test_zero_spot_price above
    # already guards the same H2 regression at the live path
    # (calc.calculate() must not crash with spot_price == 0); redundant via
    # the dead-code report path is dropped, not weakened.

    def test_empty_instruments(self):
        calc = VolatilitySurfaceCalculator([], 90000, "28MAR26")
        result = calc.calculate()

        assert result.iv_by_strike == ()
        assert result.skew_25d.skew is None
        assert result.atm_iv is None

    def test_pc_by_moneyness_bias_uses_shared_interpreter(self, sample_instruments):
        """M4 (code_quality_review.md): _interpret_pc_ratio was deleted --
        bucket bias now comes from the shared
        coding.core.analytics.thresholds.interpret_put_call_ratio (see
        tests/unit/analytics/test_thresholds.py for the full boundary
        coverage). This just confirms the wiring."""
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        buckets = calc._calculate_pc_by_moneyness()

        for bucket in buckets.values():
            assert bucket["bias"] in (
                "N/A", "Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish",
            )


class TestRiskReversalSignConvention:
    """
    bugfix_spec.md Item 9 acceptance tests (section 9.5), adapted to this
    codebase's typed VolSurfaceResult/SkewResult (attribute access) and the
    renamed ``_calculate_25_delta_risk_reversal`` method.
    """

    def test_t9_1_sign_matches_market_convention(self):
        """Hand-computed from the live pair confirmed by the audit."""
        instruments = [
            _make_instrument(62_000, "P", mark_iv=39.02, delta=-0.228),
            _make_instrument(66_000, "C", mark_iv=34.65, delta=0.265),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_264.0, "31JUL26")
        r = calc._calculate_25_delta_risk_reversal()

        assert r["risk_reversal_25d"] == pytest.approx(-4.37, abs=1e-9)  # 34.65 - 39.02
        assert r["put_over_call_skew_25d"] == pytest.approx(4.37, abs=1e-9)
        assert r["interpretation"] == "Puts Richer - Downside Hedging Demand"

    def test_t9_2_calls_richer_flips_the_label(self):
        instruments = [
            _make_instrument(62_000, "P", mark_iv=30.00, delta=-0.25),
            _make_instrument(66_000, "C", mark_iv=37.00, delta=0.25),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")
        r = calc._calculate_25_delta_risk_reversal()

        assert r["risk_reversal_25d"] == pytest.approx(7.00)
        assert r["interpretation"] == "Calls Much Richer - Strong Upside Speculation"

    def test_t9_3_report_prints_the_convention_explicitly(self):
        from coding.core.analytics.reporting.vol_surface_formatter import format_vol_surface_section

        instruments = [
            _make_instrument(62_000, "P", mark_iv=39.02, delta=-0.228),
            _make_instrument(66_000, "C", mark_iv=34.65, delta=0.265),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_264.0, "31JUL26")
        result = calc.calculate()
        report = format_vol_surface_section(result, expiration="31JUL26")

        assert "25Δ Risk Reversal (call − put): -4.4%" in report
        assert "Δ=+0.265" in report and "Δ=-0.228" in report

    def test_exactly_zero_risk_reversal_is_balanced(self):
        instruments = [
            _make_instrument(62_000, "P", mark_iv=35.0, delta=-0.25),
            _make_instrument(66_000, "C", mark_iv=35.0, delta=0.25),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")
        r = calc._calculate_25_delta_risk_reversal()
        assert r["risk_reversal_25d"] == pytest.approx(0.0)
        assert r["interpretation"] == "Balanced"


def _quoted_instrument(strike, option_type, delta, mark_iv, bid_price=1.0, ask_price=1.0):
    """
    Instrument dict for the delta-interpolation (RR25/BF25) tests
    (institutional_metrics_spec.md section 3(b)). Unlike ``_make_instrument``,
    includes bid_price/ask_price -- the "quoted" filter step 1 requires
    ``bid_price > 0 or ask_price > 0``.
    """
    return {
        "instrument_name": f"BTC-28MAR26-{int(strike)}-{option_type}",
        "expiration": "28MAR26",
        "strike": strike,
        "option_type": option_type,
        "open_interest": 100,
        "volume": 10,
        "mark_price": 0.05,
        "mark_iv": mark_iv,
        "delta": delta,
        "bid_price": bid_price,
        "ask_price": ask_price,
    }


class TestInterpolateIvAtDelta:
    """
    institutional_metrics_spec.md section 3(b): delta-space interpolation
    for the 25-delta risk reversal / butterfly, replacing the nearest-delta
    pick used by ``_calculate_25_delta_risk_reversal``.
    """

    def test_t3_1_interpolation_and_both_quantities(self):
        """
        Acceptance test T3.1. Calls (|delta|=0.30, IV=32.0), (|delta|=0.20,
        IV=36.0); puts (|delta|=0.30, IV=40.0), (|delta|=0.20, IV=44.0).
        Also includes one call and one put exactly at |delta|=0.50 (both
        IV=35.0) so the ATM interpolation at |delta|=0.50 -- averaged across
        call and put sides -- reproduces the spec's given ATM=35.0 (the
        spec's abbreviated fixture states this as a given fact without
        showing the points that produce it).

        Expected: IV_c(0.25)=34.0, IV_p(0.25)=42.0, RR25=-8.0, BF25=+3.0.
        """
        instruments = [
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
            _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0),
            _quoted_instrument(90_000 * 0.70, "P", -0.30, 40.0),
            _quoted_instrument(90_000 * 0.80, "P", -0.20, 44.0),
            _quoted_instrument(90_000, "C", 0.50, 35.0),
            _quoted_instrument(90_000, "P", -0.50, 35.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        call_pt = calc.interpolate_iv_at_delta("C", 0.25)
        put_pt = calc.interpolate_iv_at_delta("P", 0.25)
        assert call_pt.iv == pytest.approx(34.0)
        assert put_pt.iv == pytest.approx(42.0)

        result = calc.calculate_risk_reversal_butterfly()
        assert result["rr_25d"] == pytest.approx(-8.0)
        assert result["bf_25d"] == pytest.approx(3.0)
        assert result["call_25d_iv"] == pytest.approx(34.0)
        assert result["put_25d_iv"] == pytest.approx(42.0)
        assert result["atm_iv_interp"] == pytest.approx(35.0)
        assert result["method"] == "linear_delta"
        assert result["n_quotes_used"] == 6

    def test_t3_2_no_extrapolation(self):
        """
        Acceptance test T3.2. Calls only at |delta|=0.45 and |delta|=0.38 --
        0.25 is not bracketed (both quotes are above 0.25). Must return
        None, never extrapolate a numeric value.
        """
        instruments = [
            _quoted_instrument(85_000, "C", 0.45, 30.0),
            _quoted_instrument(88_000, "C", 0.38, 31.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        assert calc.interpolate_iv_at_delta("C", 0.25) is None

        result = calc.calculate_risk_reversal_butterfly()
        assert result["rr_25d"] is None
        assert result["call_25d_iv"] is None

    def test_t3_3_f2_degeneracy_is_gone(self):
        """
        Acceptance test T3.3 -- reproduces the real 7AUG26 thin-chain case.
        Neither side reaches 25-delta (max call |delta|=0.185, max put
        |delta|=0.022) -- both call_25d_iv and put_25d_iv, and therefore
        bf_25d, must be None. The old nearest-delta code returned
        bf25=0.0000 on this exact input; asserting ``is None`` (not ``== 0``)
        is the point of this test.
        """
        instruments = [
            _quoted_instrument(0, "C", 0.185, 32.79),
            _quoted_instrument(0, "C", 0.135, 33.50),
            _quoted_instrument(0, "C", 0.055, 36.88),
            _quoted_instrument(0, "P", -0.022, 50.39),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        result = calc.calculate_risk_reversal_butterfly()
        assert result["call_25d_iv"] is None
        assert result["put_25d_iv"] is None
        assert result["bf_25d"] is None
        assert result["rr_25d"] is None

    def test_unquoted_instrument_excluded(self):
        """
        Step 1 of the delta-interpolation algorithm: only instruments with
        bid_price > 0 or ask_price > 0 are "quoted" and usable. A quote-less
        instrument sitting exactly at the bracket must be excluded, so the
        bracket falls back to (or fails against) the next quoted point.
        """
        instruments = [
            _quoted_instrument(90_000 * 1.25, "C", 0.25, 999.0, bid_price=0, ask_price=0),
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
            _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        # The unquoted 999.0 IV at exactly 0.25 must NOT be picked directly;
        # interpolation between the two quoted brackets (0.20/0.30) must
        # still land on 34.0, same as T3.1.
        assert point.iv == pytest.approx(34.0)

    def test_delta_outside_valid_range_excluded(self):
        """Step 1: 0.02 <= |delta| <= 0.98 only. A |delta|=0.99 point is
        excluded from the bracket search entirely."""
        instruments = [
            _quoted_instrument(90_000 * 1.60, "C", 0.99, 10.0),
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
            _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        assert point.iv == pytest.approx(34.0)
        assert point.bracket == (0.20, 0.30)

    def test_duplicate_delta_averaged(self):
        """Duplicate |delta| values (e.g. call and put at the same strike
        rounding to the same |delta|) are averaged before interpolation,
        not treated as two separate brackets."""
        instruments = [
            _quoted_instrument(90_000 * 1.20, "C", 0.20, 34.0),
            _quoted_instrument(90_000 * 1.20 + 1, "C", 0.20, 38.0),  # duplicate |delta|, averages to 36.0
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        assert point.iv == pytest.approx(34.0)  # same as T3.1's 32/36 bracket

    def test_exact_delta_match_no_division_by_zero(self):
        """target_abs_delta exactly equal to an existing quote's |delta|
        must return that quote's IV directly, not divide by zero."""
        instruments = [
            _quoted_instrument(90_000 * 1.25, "C", 0.25, 33.5),
            _quoted_instrument(90_000 * 1.40, "C", 0.40, 28.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        assert point.iv == pytest.approx(33.5)

    def test_bracket_too_wide_returns_none_even_though_bracketed(self):
        """
        Task C4 review Important #1 -- reproduces the real BTC 26JUL26
        pathology: 0.25 IS bracketed (a=0.10 <= 0.25 <= b=0.40), but the
        bracket spans 0.30 -- wider than MAX_ABS_DELTA_BRACKET_WIDTH (0.20)
        -- a chord across most of the smile, not a genuine 25-delta read.
        Must return None, not a fabricated interpolated value.
        """
        instruments = [
            _quoted_instrument(90_000 * 1.10, "C", 0.10, 50.0),
            _quoted_instrument(90_000 * 1.40, "C", 0.40, 30.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        assert calc.interpolate_iv_at_delta("C", 0.25) is None

        result = calc.calculate_risk_reversal_butterfly()
        assert result["call_25d_iv"] is None
        assert result["rr_25d"] is None

    def test_bracket_at_max_width_is_accepted(self):
        """Width exactly equal to MAX_ABS_DELTA_BRACKET_WIDTH (0.20) is
        still a genuine read -- only WIDER than the threshold is rejected."""
        instruments = [
            _quoted_instrument(90_000 * 1.15, "C", 0.15, 40.0),
            _quoted_instrument(90_000 * 1.35, "C", 0.35, 30.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        assert point is not None
        assert point.bracket == (0.15, 0.35)

    def test_bracket_barely_over_max_width_is_rejected(self):
        instruments = [
            _quoted_instrument(90_000 * 1.14, "C", 0.14, 40.0),
            _quoted_instrument(90_000 * 1.35, "C", 0.35, 30.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        assert calc.interpolate_iv_at_delta("C", 0.25) is None

    def test_missing_strike_key_does_not_raise(self):
        """Task C4 review Important #2 root cause: a real-world instrument
        dict missing the 'strike' key entirely must be skipped, like a
        missing delta/mark_iv, not raise KeyError."""
        instrument_missing_strike = _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0)
        del instrument_missing_strike["strike"]
        instruments = [
            instrument_missing_strike,
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        # The strike-less instrument is excluded entirely -- with only one
        # remaining quoted point (0.30), 0.25 is not bracketed (no point
        # with |delta| <= 0.25).
        assert calc.interpolate_iv_at_delta("C", 0.25) is None

    def test_none_strike_value_does_not_raise(self):
        """Same as above, but the key is present with a None value
        (e.g. an API response that omits strike for a non-option
        instrument accidentally routed through) -- float(None) must never
        be reached."""
        instrument_none_strike = _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0)
        instrument_none_strike["strike"] = None
        instruments = [
            instrument_none_strike,
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        assert calc.interpolate_iv_at_delta("C", 0.25) is None
