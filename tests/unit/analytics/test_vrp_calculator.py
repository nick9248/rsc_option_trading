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
