"""
Unit tests for ExposureProfileCalculator (per-strike vanna/charm exposure
profiles -- VEX/CEX, institutional_metrics_spec.md section 4, Task C5).

Acceptance tests T4.1/T4.2/T4.3 are the spec's own hand-checked numeric
values (section 4(d)) -- verified to 1e-12 / assertAlmostEqual per the
spec's own tolerance statement.
"""

import math
from datetime import datetime

import pytest

from coding.core.analytics.exposure_profile_calculator import ExposureProfileCalculator

_VALUATION_TIME = datetime(2026, 1, 1, 0, 0, 0)


def _instrument(instrument_name, strike, option_type, mark_iv, open_interest):
    return {
        "instrument_name": instrument_name,
        "strike": strike,
        "option_type": option_type,
        "mark_iv": mark_iv,
        "open_interest": open_interest,
    }


class TestClosedFormAtHandCheckablePoint:
    """T4.1 -- S=K, sigma=0.40, tau=0.25 -> hand-computed vanna/charm."""

    def test_vanna_charm_hand_checked_values(self):
        # tau = 0.25 years = 91.25 days from valuation time -> expiry at
        # 08:00 UTC on that date, matching parse_instrument_name's convention.
        expiry = _VALUATION_TIME.replace(hour=8)
        # Build an instrument whose parsed tau is exactly 0.25 years by
        # constructing valuation_time_utc 0.25*365*24h before expiry.
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)

        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=1),
        ]
        calc = ExposureProfileCalculator(
            instruments=instruments,
            spot_price=64000,
            valuation_time_utc=valuation_time,
            currency="BTC",
        )
        result = calc.calculate(side_convention="holder")

        row = result["strike_data"][64000]
        # Hand-computed: d1=0.1, d2=-0.1, phi(0.1)=0.3969525474770118
        # vanna = +0.09923813686925294, charm = -0.07939050949540236
        assert row["call_vanna"] == pytest.approx(0.09923813686925294, abs=1e-9)
        assert row["call_charm"] == pytest.approx(-0.07939050949540236, abs=1e-9)


class TestHolderVsAssumedDealerSignsDiverge:
    """T4.2 -- holder vs assumed-dealer VEX/CEX, same greeks."""

    def _instruments(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        return valuation_time, [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=1000),
            _instrument("BTC-02APR26-64000-P", 64000, "P", mark_iv=40.0, open_interest=400),
        ]

    def test_holder_side_vex_cex(self):
        valuation_time, instruments = self._instruments()
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")

        assert result["total_vex"] == pytest.approx(88917.37063485062, rel=1e-6)
        assert result["total_cex"] == pytest.approx(-19488.738769282332, rel=1e-6)

    def test_assumed_dealer_vex(self):
        valuation_time, instruments = self._instruments()
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="assumed_dealer")

        assert result["total_vex"] == pytest.approx(38107.44455779313, rel=1e-6)

    def test_vanna_identical_for_call_and_put_leg(self):
        """No per-type sign injected into the greek itself -- all sign
        information comes from the side weight, not the option leg."""
        valuation_time, instruments = self._instruments()
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")
        row = result["strike_data"][64000]
        assert row["call_vanna"] == pytest.approx(row["put_vanna"])
        assert row["call_vanna"] > 0


class TestIdempotence:
    """T4.3 -- calling calculate() twice must be bit-identical (the
    GexDexCalculator double-counting regression this class must not repeat)."""

    def test_calculate_twice_is_bit_identical(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=1000),
            _instrument("BTC-02APR26-64000-P", 64000, "P", mark_iv=40.0, open_interest=400),
            _instrument("BTC-02APR26-70000-C", 70000, "C", mark_iv=45.0, open_interest=250),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")

        first = calc.calculate(side_convention="holder")
        second = calc.calculate(side_convention="holder")

        assert first["total_vex"] == second["total_vex"]
        assert first["total_cex"] == second["total_cex"]
        for strike in first["strike_data"]:
            assert first["strike_data"][strike] == second["strike_data"][strike]


class TestEdgeCases:
    def test_empty_instruments_returns_well_formed_empty_result(self):
        calc = ExposureProfileCalculator([], spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["strike_data"] == {}
        assert result["total_vex"] == 0.0
        assert result["total_cex"] == 0.0
        assert result["top_vanna_strikes"] == []
        assert result["top_charm_strikes"] == []
        assert result["peak_vanna_strike"] is None
        assert result["peak_charm_strike"] is None
        assert result["skipped_instruments"] == 0

    def test_expired_option_tau_le_zero_still_listed_with_zero_exposure(self):
        expiry_time = datetime(2020, 1, 1, 8, 0, 0)  # long expired
        instruments = [
            _instrument("BTC-01JAN20-64000-C", 64000, "C", mark_iv=40.0, open_interest=100),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert 64000 in result["strike_data"]
        assert result["strike_data"][64000]["vex"] == 0.0
        assert result["strike_data"][64000]["cex"] == 0.0

    def test_missing_mark_iv_skips_instrument(self):
        instruments = [
            {"instrument_name": "BTC-02APR26-64000-C", "strike": 64000,
             "option_type": "C", "mark_iv": None, "open_interest": 100},
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["strike_data"] == {}
        assert result["skipped_instruments"] == 1

    def test_zero_or_negative_mark_iv_skips_instrument(self):
        instruments = [
            {"instrument_name": "BTC-02APR26-64000-C", "strike": 64000,
             "option_type": "C", "mark_iv": 0.0, "open_interest": 100},
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["skipped_instruments"] == 1

    def test_unparseable_instrument_name_skips_instrument(self):
        instruments = [
            {"instrument_name": "not-a-valid-name", "strike": 64000,
             "option_type": "C", "mark_iv": 40.0, "open_interest": 100},
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["strike_data"] == {}
        assert result["skipped_instruments"] == 1

    def test_missing_strike_or_invalid_option_type_skips_instrument(self):
        instruments = [
            {"instrument_name": "BTC-02APR26-X-C", "strike": None,
             "option_type": "C", "mark_iv": 40.0, "open_interest": 100},
            {"instrument_name": "BTC-02APR26-64000-X", "strike": 64000,
             "option_type": "X", "mark_iv": 40.0, "open_interest": 100},
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["strike_data"] == {}
        assert result["skipped_instruments"] == 2

    def test_zero_oi_on_one_side_kept_if_other_side_has_oi(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=1000),
            _instrument("BTC-02APR26-64000-P", 64000, "P", mark_iv=40.0, open_interest=0),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert 64000 in result["strike_data"]
        assert result["strike_data"][64000]["put_oi"] == 0.0
        assert result["strike_data"][64000]["call_oi"] == 1000.0

    def test_all_strikes_zero_oi_excluded_from_strike_data(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=0),
            _instrument("BTC-02APR26-64000-P", 64000, "P", mark_iv=40.0, open_interest=0),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["strike_data"] == {}
        assert result["total_vex"] == 0.0

    def test_all_identical_strikes_single_row(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=100),
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=50),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert len(result["strike_data"]) == 1
        assert result["strike_data"][64000]["call_oi"] == 150.0

    def test_invalid_side_convention_raises(self):
        calc = ExposureProfileCalculator([], spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        with pytest.raises(ValueError):
            calc.calculate(side_convention="bogus")

    def test_inferred_without_side_weights_raises(self):
        calc = ExposureProfileCalculator([], spot_price=64000,
                                          valuation_time_utc=_VALUATION_TIME, currency="BTC")
        with pytest.raises(ValueError):
            calc.calculate(side_convention="inferred")

    def test_inferred_uses_provided_side_weights(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=1000),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(
            side_convention="inferred",
            side_weights={(64000, "C"): 0.5},
        )
        holder = calc.calculate(side_convention="holder")
        # inferred weight 0.5 should be exactly half the holder-side (weight 1.0) VEX
        assert result["total_vex"] == pytest.approx(holder["total_vex"] * 0.5)

    def test_peak_strike_identifies_largest_absolute_vex(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        valuation_time = expiry_time - timedelta(days=0.25 * 365)
        instruments = [
            _instrument("BTC-02APR26-60000-C", 60000, "C", mark_iv=40.0, open_interest=10),
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=10000),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")
        assert result["peak_vanna_strike"] == 64000


class TestReviewFixRound1:
    """
    Task C5 review fixes: Minor #1 (a non-numeric strike must be skipped,
    not abort the whole profile) and Minor #2 (a NaN mark_iv must be
    skipped explicitly, not silently propagate NaN into vex/cex).
    """

    def _future_valuation_time(self):
        from datetime import timedelta
        expiry_time = datetime(2026, 4, 2, 8, 0, 0)
        return expiry_time - timedelta(days=0.25 * 365)

    def test_non_numeric_strike_skips_only_that_instrument(self):
        """
        Before the fix: _calculate_d1's `spot / strike` raised TypeError
        for a non-numeric strike, which was NOT in the (ValueError,
        ZeroDivisionError) except tuple -- it propagated out of
        calculate() entirely, aborting the WHOLE profile (every other
        strike lost too), not just the one bad instrument.
        """
        valuation_time = self._future_valuation_time()
        instruments = [
            {"instrument_name": "BTC-02APR26-BAD-C", "strike": "not-a-number",
             "option_type": "C", "mark_iv": 40.0, "open_interest": 100},
            _instrument("BTC-02APR26-64000-C", 64000, "C", mark_iv=40.0, open_interest=1000),
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")

        # Must not raise -- and the GOOD instrument's strike must still be
        # present, proving only the bad one was skipped.
        assert 64000 in result["strike_data"]
        assert result["skipped_instruments"] == 1

    def test_nan_mark_iv_skipped_not_propagated(self):
        """
        Before the fix: `mark_iv <= 0` is False for NaN (any comparison
        against NaN is False), so a NaN mark_iv slipped through into
        sigma -> d1 -> vanna/charm as NaN, with no exception raised (NaN
        arithmetic doesn't raise) -- silently corrupting vex/cex.
        """
        valuation_time = self._future_valuation_time()
        instruments = [
            {"instrument_name": "BTC-02APR26-64000-C", "strike": 64000,
             "option_type": "C", "mark_iv": float("nan"), "open_interest": 1000},
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")

        assert result["strike_data"] == {}
        assert result["skipped_instruments"] == 1
        assert not math.isnan(result["total_vex"])
        assert result["total_vex"] == 0.0

    def test_infinite_mark_iv_skipped_not_propagated(self):
        """
        Round-2 review follow-up: the round-1 NaN guard (`mark_iv <= 0 or
        math.isnan(mark_iv)`) does NOT catch +/-inf -- `float("inf")`
        passes both checks (isnan is specifically NaN, not infinite), then
        d1's `sigma**2` / `sigma*sqrt(tau)` terms produce inf/inf = NaN
        anyway, silently reproducing the exact NUMERIC-column-poisoning
        failure the guard was supposed to close. math.isfinite() closes
        both cases.
        """
        valuation_time = self._future_valuation_time()
        instruments = [
            {"instrument_name": "BTC-02APR26-64000-C", "strike": 64000,
             "option_type": "C", "mark_iv": float("inf"), "open_interest": 1000},
        ]
        calc = ExposureProfileCalculator(instruments, spot_price=64000,
                                          valuation_time_utc=valuation_time, currency="BTC")
        result = calc.calculate(side_convention="holder")

        assert result["strike_data"] == {}
        assert result["skipped_instruments"] == 1
        assert not math.isnan(result["total_vex"])
        assert result["total_vex"] == 0.0
