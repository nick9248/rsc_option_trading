"""
Unit tests for VolatilitySurfaceCalculator.

T4 (refactor_design_spec.md): calculate() returns the typed VolSurfaceResult
(coding/core/analytics/results/vol_surface_results.py) instead of a dict —
assertions here use attribute access. ``iv_by_strike`` is now one row per
INSTRUMENT; use ``result.merged_iv_by_strike()`` for the legacy per-strike
{call_iv, put_iv} view.
"""

import logging
import math

import pytest
from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator


def _make_instrument(
    strike, option_type, mark_iv=None, delta=None, gamma=None,
    theta=None, vega=None, open_interest=100, volume=10,
    bid_price=1.0, ask_price=1.0,
):
    """
    Helper to create instrument dicts for testing.

    Task G2-D fix 1: ``bid_price``/``ask_price`` default to quoted (1.0)
    -- the per-expiry RR25/BF25 path now runs through the same bracket-
    gated delta-interpolation as the market-wide path
    (``calculate_risk_reversal_butterfly``), which requires an instrument
    to be "quoted" (``bid_price > 0 or ask_price > 0``) before it can
    contribute a point to either side's bracket search. Real production
    instrument dicts always carry these fields (see
    ``on_chain_analysis_service.py``'s ticker enrichment); defaulting them
    here keeps every existing fixture meaningful under the new gating
    instead of silently going quote-less.
    """
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
        "bid_price": bid_price,
        "ask_price": ask_price,
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
        # Task G2-D fix 1: "insufficient chain" is the existing convention
        # (coding/core/analytics/reporting/market_wide_formatter.py's
        # _format_rr25_cell/_format_bf25_cell) reused here now that the
        # per-expiry path shares calculate_risk_reversal_butterfly()'s
        # gating with the market-wide path -- not a newly-invented string.
        assert skew.interpretation == "insufficient chain"

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

        # Wave-H-A (reverting Task C5 review fix round 1 / commit
        # b6d483e): dealer_* is -(holder-side sum) -- dealers short
        # whatever holders hold -- per GexDexCalculator's own canonical
        # SIGN CONVENTION (its class docstring: the call/put-SPLIT applies
        # to GAMMA ONLY; delta/vanna/charm are each "short whatever
        # customers hold"). Independently re-derived here via the SAME
        # public BlackScholesCalculator methods and the SAME documented
        # tau-derivation formula
        # (VolatilitySurfaceCalculator._calculate_second_order_greeks's own
        # docstring), in a separate accumulation loop from production code
        # -- NOT by simply asserting dealer == -holder (that would be
        # circular against the very formula under test).
        bs = BlackScholesCalculator()
        expected_holder_vanna = 0.0
        expected_holder_charm = 0.0
        for inst in sample_instruments:
            oi = inst["open_interest"]
            sigma = inst["mark_iv"] / 100.0
            gamma_f = inst["gamma"]
            vega_f = inst["vega"]
            raw_vega = vega_f * 100.0
            tau = raw_vega / (90000 ** 2 * gamma_f * sigma)
            d1 = bs.d1_from_delta(inst["delta"], inst["option_type"])
            d2 = d1 - sigma * math.sqrt(tau)
            vanna_i = bs.calculate_vanna(d1, d2, sigma)
            charm_i = bs.calculate_charm(d1, d2, tau)
            expected_holder_vanna += vanna_i * oi
            expected_holder_charm += charm_i * oi

        assert greeks.vanna_exposure_holder == pytest.approx(expected_holder_vanna)
        assert greeks.charm_exposure_holder == pytest.approx(expected_holder_charm)
        assert greeks.dealer_vanna_exposure == pytest.approx(-expected_holder_vanna)
        assert greeks.dealer_charm_exposure == pytest.approx(-expected_holder_charm)
        # dealer_* must be exactly -(holder sum), matching the production
        # field's own values -- confirms the negation convention, not the
        # retired call/put SPLIT (which would diverge here since this
        # fixture mixes calls and puts with unequal OI).
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
        # Wave-H-A (Task 4, None-vs-zero): the ONLY instrument in this
        # fixture was skipped -- nothing was measured, so every vanna/charm
        # field must be None, not a fabricated 0.0 indistinguishable from a
        # genuinely-balanced book.
        assert greeks["vanna_exposure_holder"] is None
        assert greeks["charm_exposure_holder"] is None
        assert greeks["dealer_vanna_exposure"] is None
        assert greeks["dealer_charm_exposure"] is None

    def test_second_order_greeks_no_oi_anywhere_yields_none(self):
        """
        Wave-H-A (Task 4, None-vs-zero): zero OI everywhere is a DIFFERENT
        code path than "every OI>0 instrument was skipped" (the oi<=0
        branch ``continue``s before skipped_instruments is ever
        incremented) -- must independently confirm this path also yields
        None, not a fabricated 0.0.
        """
        instruments = [
            _make_instrument(90000, "C", mark_iv=65.0, delta=0.5, gamma=0.00003, vega=130, open_interest=0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        greeks = calc._calculate_second_order_greeks()

        assert greeks["skipped_instruments"] == 0
        assert greeks["vanna_exposure_holder"] is None
        assert greeks["charm_exposure_holder"] is None
        assert greeks["dealer_vanna_exposure"] is None
        assert greeks["dealer_charm_exposure"] is None

    def test_second_order_greeks_measured_zero_stays_zero_not_none(self, monkeypatch, sample_instruments):
        """
        Wave-H-A (Task 4, None-vs-zero): a genuinely-measured zero (every
        per-instrument vanna/charm contribution nets to exactly 0.0 for a
        real, balanced book with data actually available) must stay a
        plain 0.0 -- NOT collapse into None, which is reserved
        exclusively for "nothing could be computed". Forces every
        per-instrument vanna/charm to 0.0 via the module-level BS
        singleton so the net is deterministically exactly zero while still
        going through the full computed-instruments code path (skipped_
        instruments stays 0 -- every instrument genuinely contributed).
        """
        import coding.core.analytics.volatility_surface_calculator as vsc_module

        monkeypatch.setattr(vsc_module._bs, "calculate_vanna", lambda d1, d2, sigma: 0.0)
        monkeypatch.setattr(vsc_module._bs, "calculate_charm", lambda d1, d2, tau: 0.0)

        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        greeks = calc._calculate_second_order_greeks()

        assert greeks["skipped_instruments"] == 0
        assert greeks["vanna_exposure_holder"] == 0.0
        assert greeks["vanna_exposure_holder"] is not None
        assert greeks["charm_exposure_holder"] == 0.0
        assert greeks["charm_exposure_holder"] is not None
        assert greeks["dealer_vanna_exposure"] == 0.0
        assert greeks["dealer_vanna_exposure"] is not None
        assert greeks["dealer_charm_exposure"] == 0.0
        assert greeks["dealer_charm_exposure"] is not None

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

        Wave-H-A (Task 5): ratio is None here (no data -- spot price
        undefined means nothing can be bucketed), not the fabricated 0.0
        the "H2 fix" used to produce -- see MoneynessBucket's docstring.
        bias stays "N/A" either way.
        """
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        buckets = calc._calculate_pc_by_moneyness()

        for bucket_name in ("atm", "near_otm", "far_otm"):
            bucket = buckets[bucket_name]
            assert bucket["ratio"] is None
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
    codebase's typed VolSurfaceResult/SkewResult (attribute access).

    Task G2-D fix 1: ``_calculate_25_delta_risk_reversal``/``_find_closest_
    delta`` (the ungated nearest-delta picker these tests used to exercise
    directly) are DELETED -- ``calculate().skew_25d`` now derives from
    ``calculate_risk_reversal_butterfly()`` (the same bracket-gated
    delta-interpolation the market-wide SKEW TERM STRUCTURE section
    already used), so every test below goes through the public
    ``calculate()`` path instead of the deleted private method. Several
    fixtures below had to change from a single put + single call (which
    can only ever produce an EXACT match at |delta|=0.25 or "insufficient
    chain" under the new bracketing -- there is no more "closest, however
    far" fallback) to genuine two-point brackets straddling 0.25.
    """

    def test_calculate_skew_25d_matches_calculate_risk_reversal_butterfly(self):
        """
        There is exactly ONE risk-reversal computation now: this proves
        the per-expiry report's ``skew_25d`` and the market-wide
        ``calculate_risk_reversal_butterfly()`` return the literal SAME
        numbers for the same chain -- not two independently-implemented
        formulas that happen to agree. Uses a genuine bracket (two quotes
        per side straddling 0.25), not the trivial a==b exact-match case,
        so the comparison actually exercises interpolation.
        """
        instruments = [
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
            _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0),
            _quoted_instrument(90_000 * 0.70, "P", -0.30, 40.0),
            _quoted_instrument(90_000 * 0.80, "P", -0.20, 44.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        skew = calc.calculate().skew_25d
        rr_bf = calc.calculate_risk_reversal_butterfly()

        assert rr_bf["rr_25d"] is not None
        assert skew.risk_reversal_25d == rr_bf["rr_25d"]
        assert skew.call_25d_iv == rr_bf["call_25d_iv"]
        assert skew.put_25d_iv == rr_bf["put_25d_iv"]
        assert skew.call_25d_strike == rr_bf["call_25d_strike"]
        assert skew.put_25d_strike == rr_bf["put_25d_strike"]

    def test_close_but_unbracketed_delta_reads_insufficient_not_balanced(self):
        """
        Confirmed live by an independent audit: the OLD ungated nearest-
        delta picker would compare a ~21-delta put against a ~36-delta
        call (neither close to 25-delta, no bracket on either side) and
        still label the result "Balanced" -- reading two unrelated points
        on the smile as a genuine 25-delta risk reversal. The properly-
        gated method refuses to interpolate when a side is not bracketed;
        the per-expiry path must now inherit that refusal.
        """
        instruments = [
            _quoted_instrument(70_000, "P", -0.21, 45.0),
            _quoted_instrument(58_000, "C", 0.36, 30.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")

        skew = calc.calculate().skew_25d
        assert skew.risk_reversal_25d is None
        assert skew.interpretation == "insufficient chain"

    def test_t9_2_calls_richer_flips_the_label(self):
        instruments = [
            _quoted_instrument(62_000, "P", -0.25, 30.00),
            _quoted_instrument(66_000, "C", 0.25, 37.00),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")
        skew = calc.calculate().skew_25d

        assert skew.risk_reversal_25d == pytest.approx(7.00)
        assert skew.interpretation == "Calls Much Richer - Strong Upside Speculation"

    def test_t9_3_report_prints_the_convention_explicitly(self):
        """
        Both instruments sit exactly at |delta|=0.25 (an exact bracket
        match, not an approximate nearest-pick), so the delta-interpolated
        read lands on exactly the target delta -- unlike the old nearest-
        pick fixture (put -0.228 / call 0.265), which is now unbracketed
        (single quote, wrong side of 0.25) and would read "insufficient
        chain" instead. See test_close_but_unbracketed_delta_reads_
        insufficient_not_balanced for that case.
        """
        from coding.core.analytics.reporting.vol_surface_formatter import format_vol_surface_section

        instruments = [
            _quoted_instrument(62_000, "P", -0.25, 39.0),
            _quoted_instrument(66_000, "C", 0.25, 34.6),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")
        result = calc.calculate()
        report = format_vol_surface_section(result, expiration="31JUL26")

        assert "25Δ Risk Reversal (call − put): -4.4%" in report
        assert "Δ=+0.250" in report and "Δ=-0.250" in report

    def test_exactly_zero_risk_reversal_is_balanced(self):
        instruments = [
            _quoted_instrument(62_000, "P", -0.25, 35.0),
            _quoted_instrument(66_000, "C", 0.25, 35.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")
        skew = calc.calculate().skew_25d
        assert skew.risk_reversal_25d == pytest.approx(0.0)
        assert skew.interpretation == "Balanced"

    def test_null_mark_iv_instrument_excluded_from_interpolation_bracket(self):
        """
        Independent review round 4 (Minor)'s original concern -- a null-
        mark_iv instrument must never be used for the 25d read -- still
        holds under delta-interpolation, via a different mechanism:
        ``_build_delta_points`` drops any instrument with ``mark_iv is
        None`` before bracket selection even sees it (rather than the
        deleted nearest-delta picker's own explicit guard). A null-IV put
        sitting exactly at |delta|=0.25 must be excluded, and the
        surrounding valid quotes must still produce a genuine interpolated
        read, not a hole in the chain."""
        instruments = [
            _quoted_instrument(62_000, "P", -0.25, None),  # excluded: null IV
            _quoted_instrument(60_000, "P", -0.20, 32.0),
            _quoted_instrument(70_000, "P", -0.30, 38.0),
            _quoted_instrument(66_000, "C", 0.25, 30.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 64_000.0, "31JUL26")
        skew = calc.calculate().skew_25d

        # Put 25d: interpolated between the two VALID brackets (0.20/0.30),
        # never the null-IV quote sitting exactly at 0.25.
        assert skew.put_25d_iv == pytest.approx(35.0)  # midpoint of 32.0/38.0
        assert skew.risk_reversal_25d == pytest.approx(-5.0)  # 30.0 - 35.0


def _quoted_instrument(strike, option_type, delta, mark_iv, bid_price=1.0, ask_price=1.0,
                        bid_is_estimated=False, ask_is_estimated=False):
    """
    Instrument dict for the delta-interpolation (RR25/BF25) tests
    (institutional_metrics_spec.md section 3(b)). Unlike ``_make_instrument``,
    includes bid_price/ask_price -- the "quoted" filter step 1 requires
    ``bid_price > 0 or ask_price > 0``.

    Task Wave-J-E Fix 2: bid_is_estimated/ask_is_estimated default to False
    (i.e. genuinely quoted), matching the live on-chain path's real
    ticker-sourced bid/ask -- every pre-existing test using this helper
    keeps its original "quoted" outcome unchanged.
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
        "bid_is_estimated": bid_is_estimated,
        "ask_is_estimated": ask_is_estimated,
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


class TestEstimatedQuoteExcludedFromQuotedFilter:
    """
    Task Wave-J-E: Fix 1 makes DatabaseRepository.get_hourly_snapshots_for_hour
    return bid_price/ask_price for the first time. Fix 2 makes sure a
    hourly_snapshots-sourced instrument whose bid/ask is a pure vwap+/-0.5%
    fallback (bid_is_estimated/ask_is_estimated True -- migration 025) does
    not silently pass the "quoted" filter as if it were a real, observed
    quote.
    """

    def test_both_sides_estimated_excluded_even_though_prices_are_positive(self):
        """A row where neither side has real trade evidence (both flags
        True) must be excluded -- positive fallback prices alone are not
        enough. Regression guard for the exact risk Fix 1 would otherwise
        have unmasked: vwap+/-0.5% is always > 0, so the OLD bid_price > 0
        or ask_price > 0 check alone would pass this instrument through."""
        instruments = [
            _quoted_instrument(90_000 * 1.25, "C", 0.25, 999.0,
                                bid_price=0.049, ask_price=0.051,
                                bid_is_estimated=True, ask_is_estimated=True),
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 32.0),
            _quoted_instrument(90_000 * 1.20, "C", 0.20, 36.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        # Same expectation as test_unquoted_instrument_excluded: the
        # fully-estimated 999.0 IV point must not be picked directly.
        assert point.iv == pytest.approx(34.0)

    def test_one_side_genuinely_traded_still_counts_as_quoted(self):
        """Only one side needs real trade evidence -- matches
        HourlyAggregationService's guarantee that at least one of
        bid_price/ask_price is trade-derived whenever the instrument
        traded at all that hour (a row only exists in hourly_snapshots
        because SOME trade occurred)."""
        instruments = [
            _quoted_instrument(90_000 * 1.25, "C", 0.25, 33.0,
                                bid_price=0.049, ask_price=0.051,
                                bid_is_estimated=False, ask_is_estimated=True),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        points = calc._build_delta_points("C")
        assert len(points) == 1
        assert points[0]["iv"] == pytest.approx(33.0)

    def test_missing_estimated_keys_default_to_quoted_matching_live_path(self):
        """The live on-chain path never sets bid_is_estimated/ask_is_estimated
        (its bid/ask always come from a real ticker) -- a missing key must
        default to "not estimated", not exclude the instrument. This is
        exactly what every pre-existing test in this file already relies on
        via _quoted_instrument's own explicit False defaults; this test
        additionally proves the calculator itself defaults correctly when
        the keys are absent entirely from the dict (not merely False)."""
        instrument = _quoted_instrument(90_000 * 1.25, "C", 0.25, 33.0)
        del instrument["bid_is_estimated"]
        del instrument["ask_is_estimated"]

        calc = VolatilitySurfaceCalculator([instrument], 90_000.0, "28MAR26")
        points = calc._build_delta_points("C")
        assert len(points) == 1

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
        # Wave-I-C Fix 8: exact match -- delta at strike is exactly the
        # matched quote's own |delta|.
        assert point.delta == pytest.approx(0.25)

    def test_interp_point_delta_is_bracket_midpoint_not_target_when_asymmetric(self):
        """
        Wave-I-C Fix 8: InterpPoint.delta is the |delta| actually implied
        AT the bracket-midpoint strike -- computed the same 0.5-weight way
        as the strike itself -- NOT target_abs_delta. Before this fix,
        put_25d_delta/call_25d_delta were hardcoded to exactly
        -TARGET_ABS_DELTA_25D/+TARGET_ABS_DELTA_25D (-0.25/+0.25)
        regardless of the bracket, so the report printed "Δ=-0.250" next
        to a strike that was, in general, NOT precisely the 25-delta
        strike. An asymmetric bracket (0.15/0.30, not straddling 0.25
        symmetrically) makes the distinction visible: midpoint delta is
        (0.15+0.30)/2 = 0.225, not 0.25.
        """
        instruments = [
            _quoted_instrument(90_000 * 1.15, "C", 0.15, 40.0),
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 20.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        point = calc.interpolate_iv_at_delta("C", 0.25)
        assert point.bracket == (0.15, 0.30)
        assert point.delta == pytest.approx(0.225)
        assert point.delta != pytest.approx(0.25)

    def test_calculate_risk_reversal_butterfly_reports_actual_delta_not_target(self):
        """
        Wave-I-C Fix 8, at the level actually consumed by the report:
        calculate_risk_reversal_butterfly()'s call_25d_delta/put_25d_delta
        must be the honest interpolated values (signed: put negative,
        call positive), not the old hardcoded ±TARGET_ABS_DELTA_25D.
        """
        instruments = [
            _quoted_instrument(90_000 * 1.15, "C", 0.15, 40.0),
            _quoted_instrument(90_000 * 1.30, "C", 0.30, 20.0),
            _quoted_instrument(90_000 * 0.80, "P", -0.20, 45.0),
            _quoted_instrument(90_000 * 0.71, "P", -0.28, 55.0),
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90_000.0, "28MAR26")

        result = calc.calculate_risk_reversal_butterfly()
        assert result["call_25d_delta"] == pytest.approx(0.225)
        assert result["put_25d_delta"] == pytest.approx(-0.24)
        assert result["call_25d_delta"] != pytest.approx(0.25)
        assert result["put_25d_delta"] != pytest.approx(-0.25)

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
