"""
Tests for FixedStrikeVolCalculator (institutional_metrics_spec.md section 7 /
Task C8).

Covers the spec's own acceptance tests (T7.1 arithmetic, T7.2 sticky-strike
detection, T7.3 stale-prior guard) plus the exhaustive degenerate-case
enumeration required by task-C8-brief.md: zero prior-day data, a strike
present on only one side (new listing / expired-off), all-identical IVs,
missing/null mark_iv on either day, missing ATM IV, and a missing/zero spot.
"""

from datetime import date, timedelta

import pytest

from coding.core.analytics.fixed_strike_vol_calculator import (
    FixedStrikeVolCalculator,
    compute_nearest_strike_atm_iv,
)

TODAY = date(2026, 7, 31)
YESTERDAY = TODAY - timedelta(days=1)
FOUR_DAYS_AGO = TODAY - timedelta(days=4)


def _row(strike, option_type, mark_iv):
    return {"strike": strike, "option_type": option_type, "mark_iv": mark_iv}


class TestArithmetic:
    def test_t7_1_hand_computed_delta(self):
        """T7.1: K=65000, IV(t-1d)=32.00, IV(t)=34.50, ATM 30.00 -> 33.00.
        Expected d_iv=+2.50, d_atm=+3.00, rel=-0.50."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
            expiration="31JUL26",
        )
        result = calc.calculate()

        assert result.d_atm == pytest.approx(3.00)
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.strike == 65000
        assert row.d_iv == pytest.approx(2.50)
        assert row.d_vs_atm == pytest.approx(-0.50)
        assert result.n_strikes_matched == 1
        assert result.n_strikes_unmatched == 0
        assert result.stale_prior is False


class TestStickyRegimeDetection:
    def test_t7_2_sticky_strike(self):
        """T7.2: 5 ATM-region strikes all with d_iv=0.0, d_atm=+2.0,
        spot_move=-3%. Expected regime == STICKY_STRIKE (fixed-strike IVs
        pinned; ATM moved purely from the spot sliding along the smile)."""
        spot_today = 97000.0
        spot_prior = 100000.0  # -3%
        strikes = [93000, 95000, 97000, 99000, 101000]  # all within 10% of 97000

        today_rows = [_row(k, "C", 30.0) for k in strikes]
        prior_rows = [_row(k, "C", 30.0) for k in strikes]  # d_iv == 0 for all

        calc = FixedStrikeVolCalculator(
            today_rows=today_rows,
            prior_rows=prior_rows,
            spot_today=spot_today,
            spot_prior=spot_prior,
            atm_iv_today=32.0,
            atm_iv_prior=30.0,  # d_atm = +2.0
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.d_atm == pytest.approx(2.0)
        assert result.spot_move_pct == pytest.approx(-3.0)
        assert result.regime == "STICKY_STRIKE"

    def test_sticky_delta_when_strikes_move_in_lockstep_with_atm(self):
        """Mirror of T7.2: every ATM-region strike's d_iv tracks d_atm
        exactly (d_vs_atm == 0), |d_atm| > 1.0 -> STICKY_DELTA (whole smile
        translated with the ATM move, each strike repriced by that amount)."""
        strikes = [93000, 95000, 97000, 99000, 101000]
        today_rows = [_row(k, "C", 32.0) for k in strikes]  # +2.0 vs prior, matching d_atm
        prior_rows = [_row(k, "C", 30.0) for k in strikes]

        calc = FixedStrikeVolCalculator(
            today_rows=today_rows,
            prior_rows=prior_rows,
            spot_today=97000.0,
            spot_prior=97000.0,
            atm_iv_today=32.0,
            atm_iv_prior=30.0,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.regime == "STICKY_DELTA"
        for row in result.rows:
            assert row.d_vs_atm == pytest.approx(0.0)

    def test_repriced_when_neither_pattern_holds(self):
        """ATM-region strikes move by inconsistent, non-pinned amounts
        relative to both zero and the ATM move -> REPRICED (genuine vol
        move, not a smile artifact)."""
        strikes = [93000, 95000, 97000, 99000, 101000]
        today_ivs = [40.0, 25.0, 38.0, 20.0, 45.0]
        prior_ivs = [30.0, 30.0, 30.0, 30.0, 30.0]
        today_rows = [_row(k, "C", iv) for k, iv in zip(strikes, today_ivs)]
        prior_rows = [_row(k, "C", iv) for k, iv in zip(strikes, prior_ivs)]

        calc = FixedStrikeVolCalculator(
            today_rows=today_rows,
            prior_rows=prior_rows,
            spot_today=97000.0,
            spot_prior=97000.0,
            atm_iv_today=32.0,
            atm_iv_prior=30.0,  # d_atm = +2.0
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.regime == "REPRICED"

    def test_small_atm_move_falls_through_to_repriced_per_literal_spec_ladder(self):
        """institutional_metrics_spec.md section 7(b)'s ladder gates BOTH
        sticky labels on |d_atm| > 1.0; when the ATM barely moved, neither
        condition is reachable and the literal "otherwise" branch applies.
        Documented here so a future reader sees this is a deliberate literal
        reading of the spec's ladder, not an oversight."""
        strikes = [95000, 97000, 99000]
        today_rows = [_row(k, "C", 30.05) for k in strikes]
        prior_rows = [_row(k, "C", 30.0) for k in strikes]

        calc = FixedStrikeVolCalculator(
            today_rows=today_rows,
            prior_rows=prior_rows,
            spot_today=97000.0,
            spot_prior=97000.0,
            atm_iv_today=30.05,
            atm_iv_prior=30.0,  # d_atm = +0.05, well under the 1.0 threshold
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.regime == "REPRICED"


class TestStalePriorGuard:
    def test_t7_3_stale_prior_returns_indeterminate(self):
        """T7.3: prior_rows dated 4 days back -> regime INDETERMINATE, and
        the actual prior date must still be surfaced (never silently treated
        as a valid '1d' comparison)."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=FOUR_DAYS_AGO,
        )
        result = calc.calculate()

        assert result.regime == "INDETERMINATE"
        assert result.stale_prior is True
        assert result.prior_date == FOUR_DAYS_AGO
        assert result.expected_prior_date == YESTERDAY

    def test_missing_prior_date_is_stale(self):
        """No prior date at all (never seen a snapshot) must be treated
        identically to a too-old one -- not a crash, not a silent None-vs-
        None false match."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[],
            spot_today=64182.0,
            spot_prior=None,
            atm_iv_today=33.00,
            atm_iv_prior=None,
            today_date=TODAY,
            prior_date=None,
        )
        result = calc.calculate()

        assert result.regime == "INDETERMINATE"
        assert result.stale_prior is True
        assert result.prior_date is None


class TestDegenerateDataCases:
    def test_zero_prior_day_data_at_all(self):
        """Prior day exists (date matches expected) but has literally zero
        rows -- e.g. the daemon ran that hour but the API returned nothing.
        Every today strike is unmatched; no fabricated comparison."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50), _row(66000, "C", 33.0)],
            prior_rows=[],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.rows == ()
        assert result.n_strikes_matched == 0
        assert result.n_strikes_unmatched == 2
        # stale_prior is False here (the DATE is correct -- data is just
        # empty); regime is still INDETERMINATE because there is nothing in
        # the ATM region to evaluate the ladder against.
        assert result.stale_prior is False
        assert result.regime == "INDETERMINATE"

    def test_strike_new_today_not_present_yesterday(self):
        """A strike listed today that did not exist in yesterday's chain
        (freshly listed) -- excluded from the matrix, counted as unmatched,
        never fabricated as a d_iv=0 row."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50), _row(70000, "C", 20.0)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        matched_strikes = {row.strike for row in result.rows}
        assert matched_strikes == {65000.0}
        assert result.n_strikes_matched == 1
        assert result.n_strikes_unmatched == 1

    def test_strike_existed_yesterday_but_expired_off_today(self):
        """Inverse of the above: a strike present yesterday but gone today
        (settled/delisted) -- same treatment, unmatched, not a fabricated
        row."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", 32.00), _row(60000, "C", 40.0)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        matched_strikes = {row.strike for row in result.rows}
        assert matched_strikes == {65000.0}
        assert result.n_strikes_matched == 1
        assert result.n_strikes_unmatched == 1

    def test_all_identical_ivs_both_days(self):
        """Every strike's IV, and the ATM IV, is byte-for-byte identical
        across both days -- a genuinely flat/quiet market. d_atm == 0.0
        (falls under the 1.0-point threshold), so per the literal ladder
        this renders REPRICED, not a divide-by-zero or crash."""
        strikes = [95000, 97000, 99000]
        today_rows = [_row(k, "C", 30.0) for k in strikes]
        prior_rows = [_row(k, "C", 30.0) for k in strikes]

        calc = FixedStrikeVolCalculator(
            today_rows=today_rows,
            prior_rows=prior_rows,
            spot_today=97000.0,
            spot_prior=97000.0,
            atm_iv_today=30.0,
            atm_iv_prior=30.0,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.d_atm == pytest.approx(0.0)
        assert all(row.d_iv == pytest.approx(0.0) for row in result.rows)
        assert result.regime == "REPRICED"

    def test_null_mark_iv_today_excludes_strike_from_match(self):
        """A strike present on both days by key, but with mark_iv missing
        TODAY -- must not be matched using yesterday's value alone (that
        would silently compare a real number against nothing)."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", None)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.rows == ()
        assert result.n_strikes_matched == 0
        assert result.n_strikes_unmatched == 1

    def test_null_mark_iv_prior_excludes_strike_from_match(self):
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", None)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.rows == ()
        assert result.n_strikes_matched == 0
        assert result.n_strikes_unmatched == 1

    def test_missing_atm_iv_forces_indeterminate(self):
        """d_atm cannot be computed without both ATM IVs -- attribution is
        impossible, never silently defaulted to a fabricated 0.0 move."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=None,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.d_atm is None
        assert result.regime == "INDETERMINATE"
        # The per-strike d_iv arithmetic is still computed and shown --
        # only the ATM-relative column and the regime label degrade.
        assert len(result.rows) == 1
        assert result.rows[0].d_vs_atm is None
        assert result.rows[0].d_iv == pytest.approx(2.50)

    def test_missing_spot_today_leaves_moneyness_none_and_forces_indeterminate(self):
        """Without spot_today, ATM-region membership cannot be determined
        for any strike -- the ladder has nothing to evaluate, so
        INDETERMINATE rather than defaulting every strike into the region
        or excluding the whole matrix."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=None,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.rows[0].moneyness_pct is None
        assert result.regime == "INDETERMINATE"
        # d_iv/d_vs_atm are unaffected by a missing spot -- only moneyness
        # and therefore regime attribution degrade.
        assert result.rows[0].d_iv == pytest.approx(2.50)
        assert result.rows[0].d_vs_atm == pytest.approx(-0.50)

    def test_zero_spot_prior_does_not_crash_spot_move_pct(self):
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=0.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.spot_move_pct is None

    def test_strikes_outside_atm_region_excluded_from_ladder(self):
        """A far-OTM matched strike (>10% from spot) with a wildly
        different d_iv than the pinned ATM-region strikes must not corrupt
        the regime attribution -- spec: 'wings are too illiquid to
        attribute'."""
        atm_strikes = [95000, 97000, 99000]
        far_strike = 150000  # >10% from spot=97000

        today_rows = [_row(k, "C", 30.0) for k in atm_strikes] + [_row(far_strike, "C", 90.0)]
        prior_rows = [_row(k, "C", 30.0) for k in atm_strikes] + [_row(far_strike, "C", 10.0)]

        calc = FixedStrikeVolCalculator(
            today_rows=today_rows,
            prior_rows=prior_rows,
            spot_today=97000.0,
            spot_prior=97000.0,
            atm_iv_today=32.0,
            atm_iv_prior=30.0,  # d_atm = +2.0
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        # The far strike's huge d_iv (+80) would break any pinned-ladder
        # check if it leaked into the ATM-region evaluation.
        assert result.regime == "STICKY_STRIKE"
        assert len(result.rows) == 4  # still reported in the table

    def test_duplicate_strike_option_type_within_one_side_does_not_crash(self):
        """Defensive: a duplicate (strike, option_type) key on one side
        (should not occur given the DB's unique constraint, but the
        calculator must not raise) -- last value wins, no fabricated
        average."""
        calc = FixedStrikeVolCalculator(
            today_rows=[_row(65000, "C", 34.50), _row(65000, "C", 99.0)],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert len(result.rows) == 1
        assert result.rows[0].iv_today == 99.0

    def test_rows_sorted_by_strike_then_option_type(self):
        calc = FixedStrikeVolCalculator(
            today_rows=[
                _row(66000, "P", 30.0), _row(65000, "C", 30.0),
                _row(65000, "P", 30.0), _row(64000, "C", 30.0),
            ],
            prior_rows=[
                _row(66000, "P", 30.0), _row(65000, "C", 30.0),
                _row(65000, "P", 30.0), _row(64000, "C", 30.0),
            ],
            spot_today=65000.0,
            spot_prior=65000.0,
            atm_iv_today=30.0,
            atm_iv_prior=30.0,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        ordered = [(row.strike, row.option_type) for row in result.rows]
        assert ordered == [
            (64000.0, "C"), (65000.0, "C"), (65000.0, "P"), (66000.0, "P"),
        ]

    def test_empty_today_rows_produces_no_rows_and_no_crash(self):
        calc = FixedStrikeVolCalculator(
            today_rows=[],
            prior_rows=[_row(65000, "C", 32.00)],
            spot_today=64182.0,
            spot_prior=64182.0,
            atm_iv_today=33.00,
            atm_iv_prior=30.00,
            today_date=TODAY,
            prior_date=YESTERDAY,
        )
        result = calc.calculate()

        assert result.rows == ()
        assert result.n_strikes_matched == 0
        assert result.n_strikes_unmatched == 1


class TestComputeNearestStrikeAtmIv:
    """
    Neither ``daily_oi_snapshots`` nor (currently, until deployed)
    ``snapshots`` carries a ``delta`` column, so the delta-interpolated ATM
    read used elsewhere (``VolatilitySurfaceCalculator.
    interpolate_iv_at_delta`` at |delta|=0.50, section 3(b)) is not
    available for this historical source. This helper computes ATM IV the
    same way on BOTH the today and prior chain -- nearest call/put strike to
    spot, averaged -- so the day-over-day ATM comparison is never a mix of
    two different ATM definitions.
    """

    def test_averages_closest_call_and_put(self):
        rows = [
            {"strike": 95000, "option_type": "C", "mark_iv": 30.0},
            {"strike": 97000, "option_type": "C", "mark_iv": 32.0},  # closest call
            {"strike": 97000, "option_type": "P", "mark_iv": 34.0},  # closest put
            {"strike": 99000, "option_type": "P", "mark_iv": 28.0},
        ]
        atm = compute_nearest_strike_atm_iv(rows, spot=97100.0)
        assert atm == pytest.approx((32.0 + 34.0) / 2.0)

    def test_calls_only_returns_call_iv_alone(self):
        rows = [{"strike": 97000, "option_type": "C", "mark_iv": 32.0}]
        atm = compute_nearest_strike_atm_iv(rows, spot=97000.0)
        assert atm == pytest.approx(32.0)

    def test_empty_rows_returns_none(self):
        assert compute_nearest_strike_atm_iv([], spot=97000.0) is None

    def test_none_spot_returns_none(self):
        rows = [{"strike": 97000, "option_type": "C", "mark_iv": 32.0}]
        assert compute_nearest_strike_atm_iv(rows, spot=None) is None

    def test_zero_spot_returns_none(self):
        rows = [{"strike": 97000, "option_type": "C", "mark_iv": 32.0}]
        assert compute_nearest_strike_atm_iv(rows, spot=0.0) is None

    def test_rows_with_null_mark_iv_are_ignored(self):
        rows = [
            {"strike": 97000, "option_type": "C", "mark_iv": None},
            {"strike": 98000, "option_type": "C", "mark_iv": 31.0},
        ]
        atm = compute_nearest_strike_atm_iv(rows, spot=97000.0)
        assert atm == pytest.approx(31.0)
