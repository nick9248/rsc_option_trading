"""
Direct unit tests for OnChainMetricsCalculator.calculate_max_pain
(Wave G task G2-F, fix 1).

Wave G fresh audit finding: calculate_max_pain's docstring described the
INVERSE of what its own code computes (docstring had call/put loss terms
swapped relative to the code, and swapped which variable was the candidate
settlement price vs. the strike). The code itself was already correct --
this was a stale/inverted docstring, not a calculation bug -- but grep
across tests/ found zero tests that call calculate_max_pain directly and
check its output against an independently-computed expected value. Every
existing max_pain-related test either hand-constructs a MaxPainResult to
test a formatter, or is a golden-master snapshot comparison -- neither
would catch a regression if the formula were ever accidentally inverted.

This file hand-computes the expected max-pain strike for a small, fixed
open-interest distribution (5 strikes, calls and puts) using the standard
max-pain formula:

    pain(S) = sum over strikes K of
              [ max(0, S - K) * call_OI(K) + max(0, K - S) * put_OI(K) ]

    max_pain_strike = argmin over candidate S of pain(S)

and asserts calculate_max_pain returns exactly that strike -- a test that
would fail immediately if the call/put terms were ever swapped again.
"""

from coding.core.analytics.on_chain_analyzer import OnChainMetricsCalculator

# Fixed OI distribution: 5 strikes, deliberately NOT symmetric so a swapped
# call/put formula (or swapped candidate/strike roles) produces a different
# argmin, not just a different pain value at the same strike.
CALL_OI = {85.0: 10.0, 90.0: 20.0, 95.0: 5.0, 100.0: 30.0, 105.0: 15.0}
PUT_OI = {85.0: 25.0, 90.0: 10.0, 95.0: 5.0, 100.0: 20.0, 105.0: 10.0}


def _hand_computed_pain(candidate: float) -> float:
    """Independent re-implementation of the standard max-pain formula,
    written from the formula's definition (not copied from the function
    under test), to compute the expected total pain at one candidate
    settlement price."""
    total = 0.0
    for strike in CALL_OI:
        call_oi = CALL_OI[strike]
        put_oi = PUT_OI[strike]
        total += max(0.0, candidate - strike) * call_oi
        total += max(0.0, strike - candidate) * put_oi
    return total


def _strike_data():
    return {
        strike: {"call_oi": CALL_OI[strike], "put_oi": PUT_OI[strike]}
        for strike in CALL_OI
    }


class TestCalculateMaxPainHandComputed:
    def test_pain_by_strike_matches_hand_computed_values(self):
        """
        Hand-computed pain at every candidate strike (verified by hand,
        shown in the module docstring's formula):

            S= 85: 0 + 50 + 50 + 300 + 200 = 600
            S= 90: 50 + 0 + 25 + 200 + 150 = 425
            S= 95: 100 + 100 + 0 + 100 + 100 = 400
            S=100: 150 + 200 + 25 + 0 + 50 = 425
            S=105: 200 + 300 + 50 + 150 + 0 = 700

        Unique minimum at S=95 -- deliberately not a symmetric distribution,
        so an inverted formula (call/put swapped, or candidate/strike
        swapped) would shift the argmin away from 95, not just change the
        pain value there.
        """
        calculator = OnChainMetricsCalculator(data=[], currency="BTC")
        result = calculator.calculate_max_pain(_strike_data())

        expected_pain = {
            85.0: 600.0,
            90.0: 425.0,
            95.0: 400.0,
            100.0: 425.0,
            105.0: 700.0,
        }
        for strike, expected in expected_pain.items():
            assert result["pain_by_strike"][strike] == expected, (
                f"pain at strike {strike}: expected {expected}, "
                f"got {result['pain_by_strike'][strike]}"
            )

        # Cross-check the fixture's hand-computed helper against the same
        # expected values, so the helper itself is trustworthy.
        for strike in expected_pain:
            assert _hand_computed_pain(strike) == expected_pain[strike]

    def test_max_pain_strike_is_the_argmin(self):
        """
        The strike with minimum total pain (95, pain=400) must be returned
        as max_pain_strike. This is the assertion that would catch a
        regression if call_pain/put_pain (or candidate/strike) were ever
        swapped inside calculate_max_pain -- an inverted formula produces a
        different argmin over this deliberately asymmetric distribution.
        """
        calculator = OnChainMetricsCalculator(data=[], currency="BTC")
        result = calculator.calculate_max_pain(_strike_data())

        assert result["max_pain_strike"] == 95.0
        assert result["min_pain_value"] == 400.0

    def test_empty_strike_data_returns_none(self):
        """Documented edge case: no strikes -> no max pain strike."""
        calculator = OnChainMetricsCalculator(data=[], currency="BTC")
        result = calculator.calculate_max_pain({})

        assert result["max_pain_strike"] is None
        assert result["pain_by_strike"] == {}
        assert result["min_pain_value"] == 0

    def test_single_strike_is_its_own_max_pain(self):
        """With only one strike, pain there is whatever OI exists at other
        strikes settling through it -- with a single strike there are no
        other strikes, so pain is always 0 and that strike is max pain."""
        calculator = OnChainMetricsCalculator(data=[], currency="BTC")
        result = calculator.calculate_max_pain(
            {100.0: {"call_oi": 50.0, "put_oi": 50.0}}
        )

        assert result["max_pain_strike"] == 100.0
        assert result["min_pain_value"] == 0.0
