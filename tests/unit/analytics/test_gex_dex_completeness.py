"""
Task G2-A (Wave G fresh audit, bug 2): a partial option chain (instruments
dropped due to API errors/rate-limiting, or instruments that came back
from the ticker fetch but with a null/missing gamma) silently contributed
zero to GEX/DEX while its open interest still counted -- with no
disclosure, and the report claimed "full book" regardless.

Live evidence (task brief): 31 of 830 instruments (3.7%) were dropped due
to rate-limiting; weighted by open interest, one expiry lost 34.49% of its
OI-weighted representation, and the report still printed
"EVIDENCE: OI/GEX from full book" for that exact expiry.

``GexDexCalculator._aggregate_by_strike`` is the specific method these
tests target: ``gamma = item.get("gamma") or 0`` / ``oi = item.get(
"open_interest") or 0`` defaulted a null/missing gamma to 0 while still
summing the instrument's OI into the strike total -- indistinguishable
from a strike that genuinely has zero gamma exposure. These tests prove
the new completeness bookkeeping (``GexDexResult.instruments_missing_gamma``
/ ``oi_missing_gamma``, mirroring the existing ``legs_skipped``-style
convention on ``GexDexKeyLevels``) and the fix (instruments still counted
toward OI, but tracked as a disclosed gap rather than a silent zero).
"""

import pytest

from coding.core.analytics.gex_dex_calculator import GexDexCalculator


def _strike_row(result, strike):
    return next(r for r in result.strike_rows if r.strike == strike)


class TestCompletenessTrackingOnAggregateByStrike:
    def test_no_missing_gamma_defaults_to_zero(self):
        """Sanity: a fully-complete chain reports zero gap."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.5, "open_interest": 500},
            {"strike": 70000, "option_type": "P", "gamma": 0.00004, "delta": -0.4, "open_interest": 200},
        ]
        result = GexDexCalculator(instruments, spot_price=70000).calculate()

        assert result.instruments_missing_gamma == 0
        assert result.oi_missing_gamma == pytest.approx(0.0)

    def test_missing_gamma_instrument_tracked_with_its_oi(self):
        """
        A known OI split: 500 OI with real gamma, 300 OI with null gamma.
        The null-gamma instrument's OI must still land in the strike's
        call_oi (existing, correct behavior) AND be counted separately as
        the completeness gap (the fix).
        """
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.5, "open_interest": 500},
            # Ticker fetch succeeded (this instrument IS in the list) but
            # gamma came back null/missing -- e.g. BS-gamma computation
            # failed for a missing mark_iv. delta present, gamma absent.
            {"strike": 70000, "option_type": "C", "gamma": None, "delta": 0.5, "open_interest": 300},
        ]
        result = GexDexCalculator(instruments, spot_price=70000).calculate()

        row = _strike_row(result, 70000)
        # OI still fully counted (existing, correct behavior -- unchanged).
        assert row.call_oi == pytest.approx(800.0)
        # Gamma contribution from the missing-gamma leg is 0, as before --
        # only the completeness bookkeeping is new.
        assert row.call_gamma == pytest.approx(0.00003 * 500)

        assert result.instruments_missing_gamma == 1
        assert result.oi_missing_gamma == pytest.approx(300.0)

    def test_missing_delta_also_tracked(self):
        """Bug brief explicitly calls out delta as similarly relevant."""
        instruments = [
            {"strike": 70000, "option_type": "P", "gamma": 0.00004, "delta": None, "open_interest": 150},
        ]
        result = GexDexCalculator(instruments, spot_price=70000).calculate()

        assert result.instruments_missing_gamma == 1
        assert result.oi_missing_gamma == pytest.approx(150.0)

    def test_multiple_missing_instruments_sum_oi_and_count(self):
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.5, "open_interest": 500},
            {"strike": 70000, "option_type": "C", "gamma": None, "delta": None, "open_interest": 300},
            {"strike": 71000, "option_type": "P", "gamma": None, "delta": -0.3, "open_interest": 200},
        ]
        result = GexDexCalculator(instruments, spot_price=70000).calculate()

        assert result.instruments_missing_gamma == 2
        assert result.oi_missing_gamma == pytest.approx(500.0)

    def test_missing_gamma_with_zero_oi_still_counted_by_instrument_not_by_oi(self):
        """A missing-gamma instrument with zero OI contributes no OI to the
        gap magnitude but is still counted in the instrument tally -- the
        two fields measure different things (how many vs. how much)."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": None, "delta": 0.1, "open_interest": 0},
        ]
        result = GexDexCalculator(instruments, spot_price=70000).calculate()

        assert result.instruments_missing_gamma == 1
        assert result.oi_missing_gamma == pytest.approx(0.0)


class TestAggregateAcrossExpirationsSumsCompleteness:
    def test_completeness_fields_sum_across_expirations(self):
        expiry_a = GexDexCalculator(
            [
                {"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.5, "open_interest": 500},
                {"strike": 70000, "option_type": "C", "gamma": None, "delta": None, "open_interest": 300},
            ],
            spot_price=70000,
        ).calculate()
        expiry_b = GexDexCalculator(
            [
                {"strike": 71000, "option_type": "P", "gamma": None, "delta": -0.2, "open_interest": 400},
            ],
            spot_price=70000,
        ).calculate()

        aggregate = GexDexCalculator.aggregate_across_expirations(
            {"31JUL26": expiry_a, "28AUG26": expiry_b}, spot_price=70000,
        )

        assert aggregate.instruments_missing_gamma == 2
        assert aggregate.oi_missing_gamma == pytest.approx(700.0)

    def test_aggregate_with_no_missing_gamma_anywhere(self):
        expiry_a = GexDexCalculator(
            [{"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.5, "open_interest": 500}],
            spot_price=70000,
        ).calculate()

        aggregate = GexDexCalculator.aggregate_across_expirations(
            {"31JUL26": expiry_a}, spot_price=70000,
        )

        assert aggregate.instruments_missing_gamma == 0
        assert aggregate.oi_missing_gamma == pytest.approx(0.0)
