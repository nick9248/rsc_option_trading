"""
Task Wave-J-A Fix 1 (BLOCKER) / Fix 2: direct unit tests for
``ProspectiveCollector._enrich_with_greeks``.

Fix 1: this method used to collapse a genuinely-missing delta/gamma to a
literal ``0`` (``"delta": delta or 0`` / ``"gamma": gamma or 0``) instead of
``None``. ``GexDexCalculator._aggregate_by_strike``'s completeness tracking
(Task G2-A) -- ``instruments_missing_gamma``/``oi_missing_gamma`` -- reads
``gamma_raw is None`` as "missing"; a literal ``0`` reads as "confirmed zero
exposure", so that tracking was structurally always 0 on this daemon path,
whatever the real completeness of the book. These tests prove a genuinely-
missing greek now reaches the enriched dict (and GexDexCalculator) as
``None``.

Fix 2: gamma is anchored on ``index_price`` (matching GexDexCalculator's S²
scaling anchor), while delta/vega/theta stay anchored on ``underlying_price``
(this expiry's forward -- bugfix_spec.md Item 7's settlement-space
convention, unchanged). These tests prove the two anchors are used
independently.
"""

from datetime import datetime, timezone

import pytest

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.service.data_collection.prospective_collector import ProspectiveCollector


def _make_collector():
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    return collector


def _parsed_instrument(name, strike, option_type, mark_iv=60.0, oi=100.0):
    """Shape of an entry in ``instruments`` (parse_instruments() output --
    no nested greeks)."""
    return {
        "instrument_name": name,
        "strike": strike,
        "option_type": option_type,
        "mark_iv": mark_iv,
        "open_interest": oi,
    }


def _raw_item(greeks=None, bid_price=0.1, ask_price=0.11):
    """Shape of a raw_by_name entry (book-summary item -- nested 'greeks',
    or no 'greeks' key at all when the exchange omitted them)."""
    item = {"bid_price": bid_price, "ask_price": ask_price}
    if greeks is not None:
        item["greeks"] = greeks
    return item


_FAR_DATED_NAME = "BTC-31DEC30-50000-C"  # far enough out that tte > 0 always


class TestFix1MissingGreeksAreNoneNotZero:
    def test_no_mark_iv_and_no_exchange_greeks_yields_none(self):
        """
        BS fallback cannot run without mark_iv -- the exact 'declined to
        run' case Fix 1 targets. Old code emitted 0; must now be None.
        """
        collector = _make_collector()
        instruments = [_parsed_instrument(_FAR_DATED_NAME, 50000.0, "C", mark_iv=None)]
        raw_by_name = {_FAR_DATED_NAME: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(instruments, raw_by_name, underlying_price=95000.0)

        assert len(enriched) == 1
        assert enriched[0]["delta"] is None
        assert enriched[0]["gamma"] is None
        assert enriched[0]["vega"] is None
        assert enriched[0]["theta"] is None

    def test_already_settled_expiry_yields_none_not_zero(self):
        """
        An expiry past its 08:00 UTC settlement (tte <= 0) but still listed
        -- the daily-occurrence scenario the task brief calls out -- must
        not silently contribute gamma=0/delta=0 at full open interest.
        """
        collector = _make_collector()
        past_name = "BTC-01JAN20-50000-C"  # long-settled
        instruments = [_parsed_instrument(past_name, 50000.0, "C", mark_iv=60.0, oi=500.0)]
        raw_by_name = {past_name: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(instruments, raw_by_name, underlying_price=95000.0)

        assert enriched[0]["delta"] is None
        assert enriched[0]["gamma"] is None
        # OI is preserved untouched -- only the greek is unknown.
        assert enriched[0]["open_interest"] == 500.0

    def test_negative_strike_yields_none_not_zero(self):
        """
        Guard rigor must match _compute_bs_gamma's: explicit numeric-range
        checks, not truthiness (`not -50000` is False in Python).
        """
        collector = _make_collector()
        instruments = [_parsed_instrument(_FAR_DATED_NAME, -50000.0, "C", mark_iv=60.0)]
        raw_by_name = {_FAR_DATED_NAME: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(instruments, raw_by_name, underlying_price=95000.0)

        assert enriched[0]["gamma"] is None
        assert enriched[0]["delta"] is None

    def test_absurd_mark_iv_yields_none_not_zero(self):
        collector = _make_collector()
        instruments = [_parsed_instrument(_FAR_DATED_NAME, 50000.0, "C", mark_iv=1e10)]
        raw_by_name = {_FAR_DATED_NAME: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(instruments, raw_by_name, underlying_price=95000.0)

        assert enriched[0]["gamma"] is None
        assert enriched[0]["delta"] is None

    def test_sane_inputs_still_compute_normally(self):
        """Guardrail didn't break the ordinary (working) case."""
        collector = _make_collector()
        instruments = [_parsed_instrument(_FAR_DATED_NAME, 50000.0, "C", mark_iv=60.0)]
        raw_by_name = {_FAR_DATED_NAME: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(
            instruments, raw_by_name, underlying_price=95000.0, index_price=95000.0,
        )

        assert enriched[0]["gamma"] is not None
        assert enriched[0]["gamma"] > 0
        assert enriched[0]["delta"] is not None

    def test_genuine_zero_greek_from_exchange_is_preserved_not_overwritten(self):
        """
        A real, exactly-zero delta/gamma from the exchange (deep OTM leg)
        is a legitimate value -- must not be treated as "missing" and
        replaced by a BS recomputation. Explicit None-checks, not
        `nested.get(...) or inst.get(...)` truthiness.
        """
        collector = _make_collector()
        instruments = [_parsed_instrument(_FAR_DATED_NAME, 50000.0, "C", mark_iv=60.0)]
        raw_by_name = {
            _FAR_DATED_NAME: _raw_item(greeks={"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}),
        }

        enriched = collector._enrich_with_greeks(instruments, raw_by_name, underlying_price=95000.0)

        assert enriched[0]["delta"] == 0.0
        assert enriched[0]["gamma"] == 0.0
        assert enriched[0]["vega"] == 0.0
        assert enriched[0]["theta"] == 0.0


class TestFix1EndToEndReachesGexDexCalculatorAsNone:
    def test_missing_gamma_counted_by_gex_dex_calculator_completeness_tracking(self):
        """
        The actual motivating scenario: a genuinely-missing greek from
        _enrich_with_greeks must increment GexDexCalculator's
        instruments_missing_gamma/oi_missing_gamma -- structurally
        impossible when the old code fed it a literal 0.
        """
        collector = _make_collector()
        instruments = [
            _parsed_instrument("BTC-31DEC30-50000-C", 50000.0, "C", mark_iv=None, oi=300.0),
            _parsed_instrument("BTC-31DEC30-55000-C", 55000.0, "C", mark_iv=60.0, oi=200.0),
        ]
        raw_by_name = {
            "BTC-31DEC30-50000-C": _raw_item(greeks=None),
            "BTC-31DEC30-55000-C": _raw_item(greeks=None),
        }

        enriched = collector._enrich_with_greeks(
            instruments, raw_by_name, underlying_price=95000.0, index_price=95000.0,
        )

        gex_calc = GexDexCalculator(instruments=enriched, spot_price=95000.0)
        result = gex_calc.calculate()

        assert result.instruments_missing_gamma == 1
        assert result.oi_missing_gamma == pytest.approx(300.0)


class TestFix2GammaAnchoredOnIndexNotForward:
    def test_gamma_uses_index_price_delta_vega_theta_use_forward_price(self):
        """
        GexDexCalculator scales gamma by index_price ** 2 (its S² term).
        Gamma's VALUE must therefore be computed at index_price, not the
        forward -- while delta/vega/theta stay forward-anchored
        (bugfix_spec.md Item 7's settlement-space convention, unchanged).
        """
        collector = _make_collector()
        forward_price = 100000.0  # deliberately far from index to make the
        index_price = 95000.0     # anchor mismatch, if present, obvious.
        instruments = [_parsed_instrument(_FAR_DATED_NAME, 50000.0, "C", mark_iv=60.0)]
        raw_by_name = {_FAR_DATED_NAME: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(
            instruments, raw_by_name, underlying_price=forward_price, index_price=index_price,
        )

        bs = BlackScholesCalculator()
        parsed = bs.parse_instrument_name(_FAR_DATED_NAME)
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        tte = bs.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])

        expected_at_forward = bs.calculate_greeks(
            spot_price=forward_price, strike_price=50000.0, time_to_expiry=tte,
            implied_volatility=0.60, option_type="call",
        )
        expected_at_index = bs.calculate_greeks(
            spot_price=index_price, strike_price=50000.0, time_to_expiry=tte,
            implied_volatility=0.60, option_type="call",
        )

        # Sanity: the two anchors must actually produce different gamma
        # values for this scenario, or the test wouldn't be discriminating.
        assert expected_at_forward["gamma"] != pytest.approx(expected_at_index["gamma"])

        assert enriched[0]["gamma"] == pytest.approx(expected_at_index["gamma"], rel=1e-9)
        assert enriched[0]["delta"] == pytest.approx(expected_at_forward["delta"], rel=1e-9)
        assert enriched[0]["vega"] == pytest.approx(expected_at_forward["vega"], rel=1e-9)
        assert enriched[0]["theta"] == pytest.approx(expected_at_forward["theta"], rel=1e-9)

    def test_gamma_falls_back_to_forward_price_when_index_price_omitted(self):
        """Backward-compat: callers that only have one anchor still work,
        with gamma forward-anchored too (pre-Fix-2 behavior)."""
        collector = _make_collector()
        forward_price = 100000.0
        instruments = [_parsed_instrument(_FAR_DATED_NAME, 50000.0, "C", mark_iv=60.0)]
        raw_by_name = {_FAR_DATED_NAME: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(
            instruments, raw_by_name, underlying_price=forward_price,
        )

        bs = BlackScholesCalculator()
        parsed = bs.parse_instrument_name(_FAR_DATED_NAME)
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        tte = bs.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
        expected = bs.calculate_greeks(
            spot_price=forward_price, strike_price=50000.0, time_to_expiry=tte,
            implied_volatility=0.60, option_type="call",
        )

        assert enriched[0]["gamma"] == pytest.approx(expected["gamma"], rel=1e-9)

    def test_gex_dex_reflects_index_anchored_gamma_end_to_end(self):
        """
        The GEX value GexDexCalculator produces must match a gamma computed
        at the index anchor -- proves the anchor consistency end to end,
        not just at the enrichment step.
        """
        collector = _make_collector()
        forward_price = 100000.0
        index_price = 95000.0
        name = "BTC-31DEC30-50000-C"
        instruments = [_parsed_instrument(name, 50000.0, "C", mark_iv=60.0, oi=100.0)]
        raw_by_name = {name: _raw_item(greeks=None)}

        enriched = collector._enrich_with_greeks(
            instruments, raw_by_name, underlying_price=forward_price, index_price=index_price,
        )

        gex_calc = GexDexCalculator(instruments=enriched, spot_price=index_price)
        result = gex_calc.calculate()

        bs = BlackScholesCalculator()
        parsed = bs.parse_instrument_name(name)
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        tte = bs.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
        expected_gamma = bs.calculate_greeks(
            spot_price=index_price, strike_price=50000.0, time_to_expiry=tte,
            implied_volatility=0.60, option_type="call",
        )["gamma"]
        expected_gex = expected_gamma * 100.0 * index_price ** 2 * 0.01

        assert result.total_net_gex == pytest.approx(expected_gex, rel=1e-9)
