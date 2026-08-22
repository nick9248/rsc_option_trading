"""
Unit tests for VRPCalculator.calculate_realized_volatility's determinism fix
(institutional_metrics_spec.md Wave G Task G2-C, confirmed live by an
independent audit).

Before the fix: ``reference_time`` defaulted to a naive ``datetime.now()``
when the caller didn't pass one, and bar timestamps were converted via bare
``datetime.fromtimestamp(ts)`` (machine-LOCAL interpretation of a UTC epoch).
Comparing a naive "now" against a naive-but-actually-local-shifted bar time
made the 20d RV window boundary depend on the calling machine's local
timezone and on reference_time's wall-clock hour -- the audit measured a
2.76-vol-point swing (27.30% vs 24.54%) from that alone, with identical
underlying price data.

After the fix: ``reference_time`` is a required, explicit, timezone-aware
UTC parameter (Core never reads the wall clock itself, matching
``ExposureProfileCalculator``'s convention), and bar timestamps are
converted via ``datetime.fromtimestamp(ts, tz=timezone.utc)``. Two
timezone-aware representations of the exact same real instant now always
produce the identical result.
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.historical_normalizer import MIN_OBS as VRP_MIN_OBS
from coding.core.analytics.vrp_calculator import VRPCalculator


def _daily_bars(days: int, anchor_utc: datetime, base_price: float = 60000.0):
    """
    One bar per day, stamped at ``anchor_utc``'s hour (Deribit settlement is
    08:00 UTC) going backward from ``anchor_utc``, as a Unix epoch (seconds,
    UTC -- unambiguous). Deterministic pseudo-volatility via a sine wave, no
    randomness, so results are reproducible across runs.
    """
    bars = []
    for i in range(days, -1, -1):
        bar_time = anchor_utc - timedelta(days=i)
        price = base_price * (1 + 0.02 * math.sin(i * 0.7))
        bars.append({"timestamp": bar_time.timestamp(), "close": price})
    return bars


class TestReferenceTimeIsRequired:
    def test_omitting_reference_time_raises(self):
        """No default at all -- Core must never silently read the wall clock."""
        calc = VRPCalculator(currency="BTC")
        price_history = _daily_bars(40, datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc))
        with pytest.raises(TypeError):
            calc.calculate_realized_volatility(price_history, window_days=20)

    def test_naive_reference_time_raises_instead_of_silently_miscomputing(self):
        """
        A naive reference_time compared against the timezone-aware bar
        conversion below must fail loudly (TypeError), never silently
        produce a wrong, machine-local-timezone-dependent number -- the
        exact failure mode the audit found.
        """
        calc = VRPCalculator(currency="BTC")
        price_history = _daily_bars(40, datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc))
        naive_reference_time = datetime(2026, 8, 8, 8, 0)  # no tzinfo
        with pytest.raises(TypeError):
            calc.calculate_realized_volatility(
                price_history, window_days=20, reference_time=naive_reference_time
            )


class TestDeterminism:
    def test_result_is_identical_across_timezone_representations_of_same_instant(self):
        """
        The core determinism proof: reference_time values that represent
        the exact SAME real instant, merely expressed in different
        timezones (a stand-in for "the calling machine's local timezone
        setting", without actually mutating the test process's OS
        timezone), must produce bit-identical realized volatility. Before
        the fix, this was false -- the naive-local comparison made the
        window boundary sensitive to reference_time's naive wall-clock
        hour, which is exactly what changes when you re-express the same
        instant in a different timezone.
        """
        calc = VRPCalculator(currency="BTC")
        anchor_utc = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
        price_history = _daily_bars(40, anchor_utc)

        reference_utc = anchor_utc
        reference_minus5 = anchor_utc.astimezone(timezone(timedelta(hours=-5)))
        reference_plus9 = anchor_utc.astimezone(timezone(timedelta(hours=9)))

        # Sanity: these really are different naive wall-clock hours, i.e. a
        # faithful stand-in for "different machine-local timezone".
        assert reference_utc.hour != reference_minus5.replace(tzinfo=None).hour
        assert reference_utc.hour != reference_plus9.replace(tzinfo=None).hour

        rv_utc = calc.calculate_realized_volatility(
            price_history, window_days=20, reference_time=reference_utc
        )
        rv_minus5 = calc.calculate_realized_volatility(
            price_history, window_days=20, reference_time=reference_minus5
        )
        rv_plus9 = calc.calculate_realized_volatility(
            price_history, window_days=20, reference_time=reference_plus9
        )

        assert rv_utc == rv_minus5
        assert rv_utc == rv_plus9

    def test_result_is_identical_across_repeated_calls_with_equivalent_instants(self):
        """Same proof, smaller window, different anchor hour (not settlement-
        aligned) -- the fix must hold regardless of what hour "now" is."""
        calc = VRPCalculator(currency="BTC")
        anchor_utc = datetime(2026, 8, 8, 14, 37, tzinfo=timezone.utc)
        price_history = _daily_bars(15, anchor_utc)

        reference_a = anchor_utc
        reference_b = anchor_utc.astimezone(timezone(timedelta(hours=3)))

        rv_a = calc.calculate_realized_volatility(price_history, window_days=10, reference_time=reference_a)
        rv_b = calc.calculate_realized_volatility(price_history, window_days=10, reference_time=reference_b)

        assert rv_a == rv_b


class TestBarTimestampConversionIsUtcExplicit:
    def test_bar_at_exact_window_boundary_is_included_consistently(self):
        """
        A bar exactly ``window_days`` before reference_time sits ON the
        cutoff (``>=``) and must be included regardless of which timezone
        representation reference_time uses -- proving the bar-side
        conversion (``datetime.fromtimestamp(ts, tz=timezone.utc)``) and the
        reference_time-side value are compared in the same frame every time.
        """
        calc = VRPCalculator(currency="BTC")
        anchor_utc = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)

        # Three bars: one exactly at the 20-day cutoff, two more within the
        # window. (calculate_realized_volatility only requires
        # len(price_history) >= 2 and >= 2 filtered bars -- no need for the
        # 11-bar floor calculate_realized_volatility_multi_window imposes --
        # but a single log return has zero stdev by definition, so use 3
        # bars to get a genuinely non-zero, comparable RV.)
        boundary_bar_time = anchor_utc - timedelta(days=20)
        price_history = [
            {"timestamp": boundary_bar_time.timestamp(), "close": 60000.0},
            {"timestamp": (boundary_bar_time + timedelta(days=1)).timestamp(), "close": 60500.0},
            {"timestamp": (boundary_bar_time + timedelta(days=2)).timestamp(), "close": 59800.0},
        ]

        rv_utc = calc.calculate_realized_volatility(
            price_history, window_days=20, reference_time=anchor_utc
        )
        rv_other_tz = calc.calculate_realized_volatility(
            price_history, window_days=20,
            reference_time=anchor_utc.astimezone(timezone(timedelta(hours=-8))),
        )

        assert rv_utc > 0.0
        assert rv_utc == rv_other_tz


# ---------------------------------------------------------------------------
# Wave H Task H-D: the four "fabricated default instead of None" sites.
#
# Before this fix, on insufficient/empty input:
#   - calculate_realized_volatility returned 0.0
#   - calculate_average_iv returned 0.0
#   - calculate_iv_percentile returned 50.0 ("Default to median"), and had
#     no MIN_OBS gate at all otherwise
#   - calculate_vrp force-set vrp_percentage to 0.0 whenever realized_vol
#     was <= 0 (whether that 0.0 came from the RV fabrication above, or a
#     directly-passed non-positive value), producing a signal of NEUTRAL
#     even when vrp_absolute (IV - RV) was large and non-zero.
#
# These tests reproduce the insufficient-data / self-contradiction cases
# and assert the honest None-based contract this task establishes instead.
# ---------------------------------------------------------------------------


class TestCalculateRealizedVolatilityInsufficientData:
    def test_empty_price_history_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_realized_volatility(
            [], reference_time=datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
        ) is None

    def test_single_bar_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        price_history = [{"timestamp": datetime(2026, 8, 8, tzinfo=timezone.utc).timestamp(), "close": 60000.0}]
        assert calc.calculate_realized_volatility(
            price_history, reference_time=datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
        ) is None

    def test_bars_outside_window_return_none(self):
        """2+ bars exist, but none survive the window filter -- still None,
        not a fabricated 0.0 that would read as 'zero realized volatility'."""
        calc = VRPCalculator(currency="BTC")
        anchor = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
        stale_bars = [
            {"timestamp": (anchor - timedelta(days=90)).timestamp(), "close": 60000.0},
            {"timestamp": (anchor - timedelta(days=89)).timestamp(), "close": 60500.0},
        ]
        assert calc.calculate_realized_volatility(
            stale_bars, window_days=10, reference_time=anchor
        ) is None

    def test_sufficient_history_still_computes_a_real_value(self):
        """Happy path: this isn't a None-only regression guard."""
        calc = VRPCalculator(currency="BTC")
        anchor = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
        bars = _daily_bars(40, anchor)
        rv = calc.calculate_realized_volatility(bars, window_days=20, reference_time=anchor)
        assert rv is not None
        assert rv > 0.0


class TestCalculateAverageIvInsufficientData:
    def test_empty_options_data_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_average_iv([]) is None

    def test_nothing_passes_moneyness_filter_returns_none(self):
        """Real options exist, but none are within the ATM moneyness band --
        still None, not a fabricated 0.0 that would read as 'options are
        free'."""
        calc = VRPCalculator(currency="BTC")
        deep_otm_options = [
            {"strike": 200000.0, "underlying_price": 60000.0, "mark_iv": 0.80},
            {"strike": 10000.0, "underlying_price": 60000.0, "mark_iv": 0.90},
        ]
        assert calc.calculate_average_iv(deep_otm_options, moneyness_filter=(0.9, 1.1)) is None

    def test_sufficient_options_still_computes_a_real_average(self):
        calc = VRPCalculator(currency="BTC")
        options = [
            {"strike": 60000.0, "underlying_price": 60000.0, "mark_iv": 0.60},
            {"strike": 61000.0, "underlying_price": 60000.0, "mark_iv": 0.64},
        ]
        avg_iv = calc.calculate_average_iv(options)
        assert avg_iv is not None
        assert avg_iv == pytest.approx(0.62)


class TestCalculateIvPercentileMinObsGate:
    def test_empty_history_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_iv_percentile(0.40, []) is None

    def test_below_min_obs_returns_none(self):
        """Task H-D: previously there was NO gate at all here -- even one
        historical observation produced a confident 0th/100th percentile.
        MIN_OBS - 1 observations must still be insufficient."""
        calc = VRPCalculator(currency="BTC")
        iv_history = [0.30] * (VRP_MIN_OBS - 1)
        assert calc.calculate_iv_percentile(0.40, iv_history) is None

    def test_one_observation_no_longer_yields_a_confident_percentile(self):
        """The exact bug the task brief calls out: a single historical
        observation used to yield a 0th or 100th percentile with full
        confidence. It must now be None."""
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_iv_percentile(0.40, [0.30]) is None

    def test_at_min_obs_computes_a_real_percentile(self):
        calc = VRPCalculator(currency="BTC")
        # 30 observations, all below current_iv -> 100th percentile.
        iv_history = [0.30] * VRP_MIN_OBS
        percentile = calc.calculate_iv_percentile(0.40, iv_history)
        assert percentile == pytest.approx(100.0)

    def test_above_min_obs_computes_correct_midrank_percentile(self):
        calc = VRPCalculator(currency="BTC")
        iv_history = [0.20] * 15 + [0.50] * 15  # 30 obs, half below/half above 0.40
        percentile = calc.calculate_iv_percentile(0.40, iv_history)
        assert percentile == pytest.approx(50.0)


class TestCalculateVrpNoneHandling:
    def test_none_implied_vol_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_vrp(None, 0.50) is None

    def test_none_realized_vol_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_vrp(0.80, None) is None

    def test_both_none_returns_none(self):
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_vrp(None, None) is None

    def test_self_contradiction_case_is_closed(self):
        """The reproducible self-contradiction this task closes: a real,
        non-zero IV fed alongside a zero/negative RV used to compute
        vrp_absolute = IV - RV = IV (large, non-zero) while forcing
        vrp_percentage to 0.0 and signal to NEUTRAL for the SAME reading --
        "fairly priced" and "a large VRP" in the same breath. It must now
        be a single honest None, not a self-contradicting dict."""
        calc = VRPCalculator(currency="BTC")
        assert calc.calculate_vrp(0.80, 0.0) is None
        assert calc.calculate_vrp(0.80, -0.05) is None

    def test_valid_inputs_still_compute_a_real_result(self):
        calc = VRPCalculator(currency="BTC")
        result = calc.calculate_vrp(0.65, 0.50)
        assert result is not None
        assert result["vrp_absolute"] == pytest.approx(0.15)
        assert result["vrp_percentage"] == pytest.approx(30.0)
        assert result["signal"] == "EXPENSIVE"


class TestGenerateReportSectionInsufficientData:
    """Chains all four sites through the report generator with literally
    empty input, proving the end-to-end "insufficient data" text -- not a
    crash, and not a specific-looking fabricated number."""

    def test_fully_empty_input_renders_insufficient_data_not_a_crash(self):
        calc = VRPCalculator(currency="BTC")

        realized_vol = calc.calculate_realized_volatility(
            [], reference_time=datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)
        )
        implied_vol = calc.calculate_average_iv([])
        vrp_data = calc.calculate_vrp(implied_vol, realized_vol)
        iv_percentile = calc.calculate_iv_percentile(0.40, [])

        assert realized_vol is None
        assert implied_vol is None
        assert vrp_data is None
        assert iv_percentile is None

        report = calc.generate_report_section(vrp_data, iv_percentile=iv_percentile)

        assert "Insufficient data" in report
        # None of the old fabricated numbers/labels leak into the report.
        assert "50.0%" not in report
        assert "NEUTRAL" not in report
        assert "VERY_EXPENSIVE" not in report
        assert "VERY_CHEAP" not in report

    def test_sufficient_input_renders_real_numbers_end_to_end(self):
        """Happy path through the same chain: real data still produces a
        real, non-'insufficient' report."""
        calc = VRPCalculator(currency="BTC")
        anchor = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)

        realized_vol = calc.calculate_realized_volatility(
            _daily_bars(40, anchor, base_price=60000.0), window_days=20, reference_time=anchor
        )
        options = [
            {"strike": 60000.0, "underlying_price": 60000.0, "mark_iv": 0.60},
            {"strike": 61000.0, "underlying_price": 60000.0, "mark_iv": 0.64},
        ]
        implied_vol = calc.calculate_average_iv(options)
        vrp_data = calc.calculate_vrp(implied_vol, realized_vol)
        iv_history = [0.30] * VRP_MIN_OBS
        iv_percentile = calc.calculate_iv_percentile(implied_vol, iv_history)

        assert realized_vol is not None
        assert implied_vol is not None
        assert vrp_data is not None
        assert iv_percentile is not None

        report = calc.generate_report_section(vrp_data, iv_percentile=iv_percentile)

        assert "Insufficient data" not in report
        assert "Implied Volatility (IV):" in report
        assert "IV Percentile (30-day): 100.0%" in report

    def test_partial_insufficiency_iv_percentile_only_still_renders_honestly(self):
        """vrp_data is real, but iv_percentile alone is insufficient
        (e.g. too little IV history collected yet) -- the VRP section must
        still render normally, and the percentile section must say so
        explicitly rather than silently vanishing."""
        calc = VRPCalculator(currency="BTC")
        vrp_data = calc.calculate_vrp(0.65, 0.50)
        report = calc.generate_report_section(vrp_data, iv_percentile=None)

        assert "Insufficient data to compute VRP" not in report
        assert "Implied Volatility (IV):" in report
        assert "IV Percentile (30-day): insufficient history" in report
