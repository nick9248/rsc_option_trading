"""
Unit tests for DealerInventoryCalculator
(institutional_metrics_spec.md section 2 / task C3).

Acceptance tests T2.1-T2.3 are hand-computed in the spec; reproduced here
verbatim, plus additional edge-case coverage (stale strikes, zero-trade
strikes, empty input).

Pure calculator -- no DB/API access, matches the spec's own contract:
``DealerInventoryCalculator(flow_rows, greeks_by_instrument, spot_price, currency)``.
``flow_rows``: list of dicts with keys strike, option_type, taker_net,
gross_volume, trade_count (the repository's ``get_signed_taker_flow_by_strike``
shape). ``greeks_by_instrument``: dict keyed (strike, option_type) -> per-
contract {"gamma", "delta"} (NOT OI-weighted -- taker_net already encodes
size).
"""

import pytest

from coding.core.analytics.dealer_inventory_calculator import DealerInventoryCalculator


def _flow_row(strike, option_type, taker_net, gross_volume=None, trade_count=1):
    return {
        "strike": strike,
        "option_type": option_type,
        "taker_net": taker_net,
        "gross_volume": gross_volume if gross_volume is not None else abs(taker_net),
        "trade_count": trade_count,
    }


class TestAcceptanceT21MirrorSign:
    """T2.1 -- single strike, mirror sign."""

    def test_dealer_net_mirrors_negated_taker_net(self):
        flow_rows = [_flow_row(70000, "C", taker_net=100)]
        greeks = {(70000, "C"): {"gamma": 0.00002, "delta": 0.5}}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        row = result["strike_data"][70000]
        assert row["dealer_net_c"] == pytest.approx(-100.0)
        assert row["dealer_net_p"] == pytest.approx(0.0)

    def test_gex_matches_documented_case(self):
        # taker_net=100 -> dealer_net=-100; GEX = -100 * 0.00002 * 64000^2 * 0.01 = -81,920.0
        flow_rows = [_flow_row(70000, "C", taker_net=100)]
        greeks = {(70000, "C"): {"gamma": 0.00002, "delta": 0.5}}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        row = result["strike_data"][70000]
        assert row["inferred_gex"] == pytest.approx(-81920.0)


class TestAcceptanceT22CoverageGate:
    """T2.2 -- coverage gate fires (violation-rate computation only; the
    render_inferred decision itself is the service's job -- D9)."""

    def test_violation_rate_one_of_ten(self):
        # 10 legs, 1 with |taker_net|=150 against OI=100 -> violation_rate = 0.10
        flow_rows = [_flow_row(60000 + i * 1000, "C", taker_net=10) for i in range(9)]
        flow_rows.append(_flow_row(70000, "P", taker_net=150))
        oi_by_instrument = {(60000 + i * 1000, "C"): 100 for i in range(9)}
        oi_by_instrument[(70000, "P")] = 100

        greeks = {k: {"gamma": 0.00001, "delta": 0.1} for k in oi_by_instrument}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")

        coverage = calc.coverage_report(oi_by_instrument)
        assert coverage["n_strikes"] == 10
        assert coverage["n_violations"] == 1
        assert coverage["violation_rate"] == pytest.approx(0.10)

    def test_calculate_still_returns_numbers_when_gate_would_fail(self):
        """calculate() itself never gates -- D9's render decision is the
        service's job, informed by this calculator's coverage_report()."""
        flow_rows = [_flow_row(70000, "P", taker_net=150)]
        greeks = {(70000, "P"): {"gamma": 0.00001, "delta": -0.5}}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")

        result = calc.calculate()
        assert 70000 in result["strike_data"]
        assert result["strike_data"][70000]["dealer_net_p"] == pytest.approx(-150.0)


class TestAcceptanceT23PutGammaAdds:
    """T2.3 -- put gamma adds, does not subtract (structural difference from
    the assumed-dealer call-minus-put convention)."""

    def test_call_and_put_gamma_add(self):
        flow_rows = [
            _flow_row(70000, "C", taker_net=-100),  # dealer_net_c = +100
            _flow_row(70000, "P", taker_net=-100),  # dealer_net_p = +100
        ]
        greeks = {
            (70000, "C"): {"gamma": 0.00002, "delta": 0.5},
            (70000, "P"): {"gamma": 0.00002, "delta": -0.5},
        }
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        row = result["strike_data"][70000]
        assert row["dealer_net_c"] == pytest.approx(100.0)
        assert row["dealer_net_p"] == pytest.approx(100.0)
        # (100 + 100) * 0.00002 * 64000^2 * 0.01 = +163,840.0
        assert row["inferred_gex"] == pytest.approx(163840.0)
        assert result["total_inferred_gex"] == pytest.approx(163840.0)

    def test_not_zero_like_the_assumed_view_would_give(self):
        """A test that returns 0 here means call/put subtraction leaked in."""
        flow_rows = [
            _flow_row(70000, "C", taker_net=-100),
            _flow_row(70000, "P", taker_net=-100),
        ]
        greeks = {
            (70000, "C"): {"gamma": 0.00002, "delta": 0.5},
            (70000, "P"): {"gamma": 0.00002, "delta": -0.5},
        }
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()
        assert result["strike_data"][70000]["inferred_gex"] != 0.0


class TestEdgeCases:
    def test_strike_in_chain_with_zero_trades_still_listed(self):
        """Strike present in the chain (greeks_by_instrument) with no flow
        row -> dealer_net = 0, contributes 0, still listed (spec §2(c))."""
        greeks = {
            (70000, "C"): {"gamma": 0.00002, "delta": 0.5},
            (70000, "P"): {"gamma": 0.00001, "delta": -0.3},
        }
        calc = DealerInventoryCalculator([], greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        assert 70000 in result["strike_data"]
        row = result["strike_data"][70000]
        assert row["dealer_net_c"] == 0.0
        assert row["dealer_net_p"] == 0.0
        assert row["inferred_gex"] == 0.0
        assert row["inferred_dex"] == 0.0

    def test_strike_traded_but_absent_from_chain_is_dropped_and_flagged_stale(self):
        """Strike with trade history but no current-chain Greeks (expired/
        delisted mid-window) -> dropped from strike_data, counted in
        stale_strikes diagnostic, never crashes."""
        flow_rows = [_flow_row(99999, "C", taker_net=42)]
        greeks = {}  # 99999/C has no Greeks -- not in the current chain
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        assert 99999 not in result["strike_data"]
        assert len(result["stale_strikes"]) == 1
        assert result["stale_strikes"][0]["strike"] == 99999
        assert result["stale_strikes"][0]["option_type"] == "C"

    def test_empty_everything_does_not_crash(self):
        calc = DealerInventoryCalculator([], {}, spot_price=64000, currency="BTC")
        result = calc.calculate()
        assert result["strike_data"] == {}
        assert result["total_inferred_gex"] == 0.0
        assert result["total_inferred_dex"] == 0.0
        assert result["key_levels"]["call_resistance"] is None
        assert result["key_levels"]["put_support"] is None
        assert result["key_levels"]["hvl"] is None

    def test_coverage_report_zero_strikes_no_zero_division(self):
        calc = DealerInventoryCalculator([], {}, spot_price=64000, currency="BTC")
        coverage = calc.coverage_report({})
        assert coverage["n_strikes"] == 0
        assert coverage["n_violations"] == 0
        assert coverage["violation_rate"] == 0.0
        assert coverage["legs_excluded_no_oi"] == 0

    def test_coverage_report_excludes_legs_with_no_oi_reference(self):
        """
        Fix round (Important #3): a leg with flow but NO entry in
        oi_by_instrument (e.g. its get_ticker() call failed transiently
        upstream and it was dropped from instruments_with_greeks) must be
        excluded from n_strikes/violation_rate entirely -- not defaulted to
        a 0 OI reference, which would make ANY nonzero flow on it look like
        a violation purely from missing data.
        """
        flow_rows = [
            _flow_row(70000, "C", taker_net=50),          # has an OI reference, no violation
            _flow_row(99999, "P", taker_net=1),           # NO OI reference at all -- must be excluded
        ]
        oi_by_instrument = {(70000, "C"): 100}  # 99999/P deliberately absent
        greeks = {(70000, "C"): {"gamma": 0.00001, "delta": 0.1}}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")

        coverage = calc.coverage_report(oi_by_instrument)
        assert coverage["n_strikes"] == 1  # only the referenced leg counted
        assert coverage["n_violations"] == 0
        assert coverage["violation_rate"] == pytest.approx(0.0)
        assert coverage["legs_excluded_no_oi"] == 1

    def test_coverage_report_all_legs_missing_oi_reference_gives_zero_rate_not_crash(self):
        """Every leg lacks an OI reference -> n_strikes = 0, violation_rate =
        0.0 (not a ZeroDivisionError, and NOT 100% violated)."""
        flow_rows = [_flow_row(99999, "P", taker_net=500)]
        calc = DealerInventoryCalculator(flow_rows, {}, spot_price=64000, currency="BTC")

        coverage = calc.coverage_report({})
        assert coverage["n_strikes"] == 0
        assert coverage["n_violations"] == 0
        assert coverage["violation_rate"] == 0.0
        assert coverage["legs_excluded_no_oi"] == 1

    def test_coverage_report_worst_strikes_sorted_by_excess(self):
        flow_rows = [
            _flow_row(70000, "C", taker_net=150),  # excess vs OI=100 -> 50
            _flow_row(71000, "C", taker_net=300),  # excess vs OI=100 -> 200
        ]
        oi_by_instrument = {(70000, "C"): 100, (71000, "C"): 100}
        greeks = {k: {"gamma": 0.00001, "delta": 0.1} for k in oi_by_instrument}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")

        coverage = calc.coverage_report(oi_by_instrument)
        assert coverage["n_violations"] == 2
        worst = coverage["worst_strikes"]
        assert worst[0]["strike"] == 71000
        assert worst[0]["excess"] == pytest.approx(200.0)
        assert worst[1]["strike"] == 70000

    def test_call_resistance_and_put_support_recomputed_on_inferred_sign(self):
        """
        Fix round (Minor #3): the original fixture used the SAME taker_net
        magnitude (500) on both legs, which is a genuine tie in
        inferred_gex (409,600.0 == 409,600.0) -- the old assertion
        (`cr["strike"] in (70000, 60000)`) was silently accepting either
        answer because it never noticed the fixture didn't actually
        distinguish a winner. Rebuilt with different magnitudes (500 vs
        200) so there is one unambiguous larger inferred_gex, and the
        expected value is asserted exactly:
        inferred_gex(70000) = 500 * 0.00002 * 64000^2 * 0.01 = 409,600.0
        inferred_gex(60000) = 200 * 0.00002 * 64000^2 * 0.01 = 163,840.0
        """
        flow_rows = [
            _flow_row(70000, "C", taker_net=-500),  # dealer_net_c = +500
            _flow_row(60000, "P", taker_net=-200),  # dealer_net_p = +200
        ]
        greeks = {
            (70000, "C"): {"gamma": 0.00002, "delta": 0.5},
            (60000, "P"): {"gamma": 0.00002, "delta": -0.5},
        }
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        assert result["strike_data"][70000]["inferred_gex"] == pytest.approx(409600.0)
        assert result["strike_data"][60000]["inferred_gex"] == pytest.approx(163840.0)

        cr = result["key_levels"]["call_resistance"]
        assert cr is not None
        assert cr["strike"] == 70000
        assert cr["inferred_gex"] == pytest.approx(409600.0)

    def test_dex_uses_dealer_net_times_delta_not_oi_weighted(self):
        flow_rows = [_flow_row(70000, "C", taker_net=-40)]  # dealer_net_c = +40
        greeks = {(70000, "C"): {"gamma": 0.00002, "delta": 0.5}}
        calc = DealerInventoryCalculator(flow_rows, greeks, spot_price=64000, currency="BTC")
        result = calc.calculate()

        row = result["strike_data"][70000]
        assert row["inferred_dex"] == pytest.approx(40 * 0.5)
