"""
Unit tests for OnChainAnalysisService._calculate_inferred_dealer_positioning
(institutional_metrics_spec.md section 2 / task C3: service wiring, D9 gate).

Repository is a MagicMock -- verifies the T0 decision (max(first trade seen,
2026-04-25 coverage-stable date)), the D9 gate combination (coverage >= 0.95
AND violation_rate <= 0.05, both boundary-inclusive), and the "additive-only,
never crash the pipeline" guard (matches GexDexCalculator._calculate_gamma_
profile's established precedent).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.results.dealer_inventory_results import DealerInventoryResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

_COVERAGE_STABLE_MS = int(datetime(2026, 4, 25, tzinfo=timezone.utc).timestamp() * 1000)


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


def _instruments():
    return [
        {"strike": 70000.0, "option_type": "C", "gamma": 0.00002, "delta": 0.5, "open_interest": 500.0},
        {"strike": 70000.0, "option_type": "P", "gamma": 0.00002, "delta": -0.5, "open_interest": 300.0},
    ]


def _make_repo(
    first_trade_ts=None,
    flow_rows=None,
    coverage=(0, 0),
):
    repo = MagicMock()
    repo.get_first_trade_timestamp.return_value = first_trade_ts
    repo.get_signed_taker_flow_by_strike.return_value = flow_rows or []
    repo.get_trade_hour_coverage.return_value = coverage
    return repo


class TestT0Decision:
    def test_first_trade_before_coverage_stable_date_uses_coverage_stable_date(self):
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS - 1000 * 3600 * 24 * 30)  # 30 days earlier
        service = _make_service(repo)

        service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        called_since = repo.get_signed_taker_flow_by_strike.call_args[0][2]
        assert called_since == _COVERAGE_STABLE_MS

    def test_first_trade_after_coverage_stable_date_uses_first_trade(self):
        later_ts = _COVERAGE_STABLE_MS + 1000 * 3600 * 24 * 10  # 10 days later
        repo = _make_repo(first_trade_ts=later_ts)
        service = _make_service(repo)

        service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        called_since = repo.get_signed_taker_flow_by_strike.call_args[0][2]
        assert called_since == later_ts

    def test_no_trades_ever_falls_back_to_coverage_stable_date(self):
        """Zero-trades-ever edge case: get_first_trade_timestamp returns None
        -- T0 must still be well-defined (not crash)."""
        repo = _make_repo(first_trade_ts=None)
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        called_since = repo.get_signed_taker_flow_by_strike.call_args[0][2]
        assert called_since == _COVERAGE_STABLE_MS
        assert result is not None
        assert result.render_inferred is False  # coverage (0,0) -> 0.0, gate fails, no crash


class TestD9GatePass:
    def test_gate_passes_renders_inferred(self):
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result.render_inferred is True
        assert result.coverage == pytest.approx(1.0)
        assert result.violation_rate == pytest.approx(0.0)
        assert result.unavailable_reason is None
        assert result.total_inferred_gex != 0.0

    def test_gate_passes_at_coverage_boundary(self):
        """Coverage exactly 0.95, violation_rate 0.0 -- coverage's own
        boundary-inclusive check per spec's literal `>= 0.95`."""
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        # OI = 500 on the call leg (see _instruments()) -- no violation from
        # this row; craft coverage to sit exactly at the boundary instead.
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(95, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result.coverage == pytest.approx(0.95)
        assert result.render_inferred is True

    def test_gate_passes_at_violation_rate_boundary(self):
        """
        Fix round (Minor #2): the original version of this test claimed to
        test the `violation_rate <= 0.05` boundary but its fixture actually
        had violation_rate = 0.0 (a single leg, no violation) -- it never
        exercised that boundary at all. Rebuilt with 20 legs, exactly 1
        violating (1/20 = 0.05 exactly), coverage = 1.0, so the ONLY thing
        sitting at its boundary is violation_rate itself.
        """
        strikes = [70000.0 + i * 1000.0 for i in range(20)]
        instruments = [
            {"strike": s, "option_type": "C", "gamma": 0.00001, "delta": 0.1, "open_interest": 100.0}
            for s in strikes
        ]
        flow_rows = [
            {"strike": s, "option_type": "C", "taker_net": -50.0, "gross_volume": 50.0, "trade_count": 1}
            for s in strikes[:19]
        ]
        # The 20th leg violates: |taker_net| 150 > OI 100.
        flow_rows.append(
            {"strike": strikes[19], "option_type": "C", "taker_net": -150.0, "gross_volume": 150.0, "trade_count": 1}
        )
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", instruments, 70000.0)

        assert result.violation_rate == pytest.approx(0.05)
        assert result.render_inferred is True


class TestD9GateFail:
    def test_low_coverage_fails_gate(self):
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(80, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result.render_inferred is False
        assert result.coverage == pytest.approx(0.80)
        assert "coverage" in result.unavailable_reason
        assert "80.0%" in result.unavailable_reason

    def test_high_violation_rate_fails_gate(self):
        """T2.2-style: one leg's |taker_net| exceeds its OI."""
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -900.0, "gross_volume": 900.0, "trade_count": 10},
        ]
        # OI = 500 on the call leg -- |taker_net| 900 > 500 -> violation.
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result.render_inferred is False
        assert result.violation_rate == pytest.approx(1.0)
        assert "violations" in result.unavailable_reason

    def test_all_legs_missing_oi_reference_fails_gate_not_vacuous_pass(self):
        """
        Fix round 2 (Important): if every flow-row leg lacks an OI
        reference, n_strikes == 0 and violation_rate reads 0.0 -- "zero
        violations, clean pass" -- when the truth is "nothing was actually
        checked". Coverage passes cleanly here (100/100) to isolate this
        specific failure mode from a coverage-driven one: this must fail
        the gate on the exclusion condition alone.
        """
        # Neither strike appears in _instruments() (which only has 70000) --
        # every flow row leg has no OI/Greeks reference at all.
        flow_rows = [
            {"strike": 99999.0, "option_type": "C", "taker_net": -500.0, "gross_volume": 500.0, "trade_count": 5},
            {"strike": 88888.0, "option_type": "P", "taker_net": 300.0, "gross_volume": 300.0, "trade_count": 3},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result is not None
        assert result.coverage == pytest.approx(1.0)  # coverage itself is fine
        assert result.violation_rate == pytest.approx(0.0)  # vacuously "clean" on 0 checked legs
        assert result.render_inferred is False  # must NOT read the vacuous 0.0 as a pass
        assert "insufficient OI reference" in result.unavailable_reason
        assert "2/2" in result.unavailable_reason  # both legs excluded

    def test_high_exclusion_fraction_fails_gate_even_with_clean_remainder(self):
        """
        Fix round 2 (Important): 3 of 10 legs (30%) lack an OI reference --
        above the 20% threshold -- even though the 7 checked legs are
        perfectly clean (0 violations). The checked remainder is no longer
        a representative sample of the book, so this must still fail.
        """
        checked_strikes = [70001.0 + i for i in range(7)]
        instruments = [
            {"strike": s, "option_type": "C", "gamma": 0.00002, "delta": 0.5, "open_interest": 100.0}
            for s in checked_strikes
        ]
        flow_rows = [
            {"strike": s, "option_type": "C", "taker_net": -50.0, "gross_volume": 50.0, "trade_count": 1}
            for s in checked_strikes
        ]
        # 3 legs with trade history but no corresponding instrument (no OI
        # reference at all) -- 3/10 = 30% exclusion, above the 20% threshold.
        for s in (80001.0, 80002.0, 80003.0):
            flow_rows.append(
                {"strike": s, "option_type": "C", "taker_net": -50.0, "gross_volume": 50.0, "trade_count": 1}
            )

        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", instruments, 70000.0)

        assert result.coverage == pytest.approx(1.0)
        assert result.violation_rate == pytest.approx(0.0)  # the 7 checked legs are clean
        assert result.render_inferred is False  # must still fail on exclusion fraction
        assert "insufficient OI reference" in result.unavailable_reason
        assert "3/10" in result.unavailable_reason
        assert "30.0%" in result.unavailable_reason

    def test_low_exclusion_fraction_at_or_below_threshold_does_not_block_pass(self):
        """Sanity check for the other side of the threshold: 2 of 10 legs
        (20%, at the threshold) excluded, clean remainder, coverage clean --
        must still pass (threshold is `> 0.20`, not `>= 0.20`)."""
        checked_strikes = [70001.0 + i for i in range(8)]
        instruments = [
            {"strike": s, "option_type": "C", "gamma": 0.00002, "delta": 0.5, "open_interest": 100.0}
            for s in checked_strikes
        ]
        flow_rows = [
            {"strike": s, "option_type": "C", "taker_net": -50.0, "gross_volume": 50.0, "trade_count": 1}
            for s in checked_strikes
        ]
        for s in (80001.0, 80002.0):
            flow_rows.append(
                {"strike": s, "option_type": "C", "taker_net": -50.0, "gross_volume": 50.0, "trade_count": 1}
            )

        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", instruments, 70000.0)

        assert result.render_inferred is True

    def test_zero_trades_expiry_gates_to_assumed_view_not_crash(self):
        """Edge case (task brief): expiry with zero trades must gate to the
        assumed view, not crash. Distinct from the all-legs-excluded case
        above: zero flow rows means zero exclusions too (nothing to
        exclude), so this must be caught by the explicit
        `no_trades_for_this_expiry` check (fix round 3), not by the
        exclusion-fraction check. Uses low currency-wide coverage here too
        (belt-and-suspenders with the round-3 test below, which uses HIGH
        coverage specifically to isolate the new check from this one)."""
        repo = _make_repo(first_trade_ts=None, flow_rows=[], coverage=(0, 50))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result is not None
        assert result.render_inferred is False
        assert result.coverage == pytest.approx(0.0)
        assert result.violation_rate == pytest.approx(0.0)  # no legs to violate
        # The strike in the chain still gets a 0-contribution row
        # (calculate() never gates), even though the report will not render
        # it. _instruments() has one strike (70000) with both a call and a
        # put leg -- one merged DealerInventoryStrikeRow, same convention as
        # GexDexStrikeRow.
        assert len(result.strike_rows) == 1

    def test_zero_trades_for_this_expiry_fails_even_with_high_currency_wide_coverage(self):
        """
        Fix round 3 (Important): the round-1 fix made `coverage` currency-
        wide (collector health), not per-expiry. A currently-listed expiry
        that has genuinely never traded since T0 has empty flow_rows, so
        n_strikes_checked == 0 AND legs_excluded_no_oi == 0 -- NEITHER
        round-2 guard fires (the hard floor needs legs_excluded_no_oi > 0;
        0/0 exclusion rate is 0.0, under the 20% threshold). Before this
        fix, a HIGH currency-wide coverage (the collector is healthy) would
        make this render_inferred=True with zero legs actually checked for
        THIS specific expiry -- the same "validated nothing, reported
        clean" pattern as round 2's bug, one level up. Coverage is
        deliberately set high here (97%) to prove the fix checks this
        expiry's own flow_rows directly, not any coverage-based proxy for
        it.
        """
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=[], coverage=(97, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "99JUL99", _instruments(), 70000.0)

        assert result is not None
        assert result.coverage == pytest.approx(0.97)  # currency-wide coverage IS high
        assert result.violation_rate == pytest.approx(0.0)  # vacuously "clean" -- 0 legs checked
        assert result.render_inferred is False  # must NOT read high coverage as a pass for this expiry
        assert "no trade history for this expiry" in result.unavailable_reason
        assert result.strike_rows[0].strike == 70000.0
        assert result.strike_rows[0].dealer_net_c == 0.0
        assert result.strike_rows[0].dealer_net_p == 0.0


class TestGuardNeverCrashesPipeline:
    def test_unexpected_repository_exception_returns_none_and_logs(self, caplog):
        repo = MagicMock()
        repo.get_first_trade_timestamp.side_effect = RuntimeError("boom")
        service = _make_service(repo)

        import logging
        with caplog.at_level(logging.ERROR):
            result = service._calculate_inferred_dealer_positioning(
                "BTC", "31JUL26", _instruments(), 70000.0
            )

        assert result is None
        assert any("dealer" in record.message.lower() for record in caplog.records)

    def test_no_repository_configured_returns_none_without_error_log(self, caplog):
        """
        Fix round (Minor #4): matches the established "no repository ->
        skip DB-dependent work" convention already used elsewhere in this
        module (e.g. _apply_pcr_percentile_classification). Before this
        fix, running without a repository hit the broad except-Exception
        guard on every call (AttributeError from `None.get_first_trade_
        timestamp(...)`), logging a full ERROR traceback for an expected,
        gracefully-handled condition rather than an unexpected failure.
        """
        service = _make_service(repository=None)

        import logging
        with caplog.at_level(logging.ERROR):
            result = service._calculate_inferred_dealer_positioning(
                "BTC", "31JUL26", _instruments(), 70000.0
            )

        assert result is None
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)


class TestMissingGreeksDisclosure:
    """
    Task Wave-H-E: the service builds ``greeks_by_instrument`` for
    ``DealerInventoryCalculator`` -- it must preserve a None gamma/delta
    rather than collapsing it to 0.0 one layer up (which would make the
    calculator's own ``is None`` check dead code, defeating the disclosure
    before it can run). ``DealerInventoryResult.instruments_missing_gamma``/
    ``oi_missing_gamma`` must reflect the real gap.
    """

    def test_missing_gamma_on_an_instrument_is_disclosed_on_the_result(self):
        instruments = [
            {"strike": 70000.0, "option_type": "C", "gamma": None, "delta": 0.5, "open_interest": 500.0},
            {"strike": 70000.0, "option_type": "P", "gamma": 0.00002, "delta": -0.5, "open_interest": 300.0},
        ]
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", instruments, 70000.0)

        assert result is not None
        assert result.instruments_missing_gamma == 1
        assert result.oi_missing_gamma == pytest.approx(500.0)

    def test_no_missing_greeks_discloses_zero(self):
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result is not None
        assert result.instruments_missing_gamma == 0
        assert result.oi_missing_gamma == pytest.approx(0.0)

    def test_genuinely_zero_gamma_on_an_instrument_is_not_disclosed_as_missing(self):
        """A real, exact-zero gamma/delta (e.g. deep OTM) must survive the
        service's own dict-building step without being miscounted."""
        instruments = [
            {"strike": 70000.0, "option_type": "C", "gamma": 0.0, "delta": 0.0, "open_interest": 500.0},
            {"strike": 70000.0, "option_type": "P", "gamma": 0.00002, "delta": -0.5, "open_interest": 300.0},
        ]
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", instruments, 70000.0)

        assert result is not None
        assert result.instruments_missing_gamma == 0
        assert result.oi_missing_gamma == pytest.approx(0.0)


class TestSpotPriceValidationGate:
    """
    Task Wave-H-E: a missing/non-positive spot price must not silently
    produce a confident-looking all-zero inferred GEX. Folded into the SAME
    D9 render_inferred gate as the other disclosed failure modes (coverage,
    violation rate, OI exclusion) -- an explicit "INFERRED DEALER VIEW
    UNAVAILABLE (...)" outcome, not a fabricated measurement.
    """

    def test_none_spot_price_fails_the_gate_with_explicit_reason(self):
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), None)

        assert result is not None
        assert result.render_inferred is False
        assert "spot price" in result.unavailable_reason

    def test_zero_spot_price_fails_the_gate(self):
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 0.0)

        assert result is not None
        assert result.render_inferred is False
        assert "spot price" in result.unavailable_reason
        # Every strike's inferred_gex must be exactly 0.0 -- not rendered,
        # but computed as a real (not fabricated-looking) zero internally --
        # AND the report must never show it as if it were measured (that is
        # the formatter's job, verified separately).
        assert all(row.inferred_gex == 0.0 for row in result.strike_rows)

    def test_valid_spot_price_does_not_fail_on_this_condition(self):
        """Sanity check: a healthy spot price does not spuriously trip the
        new gate condition (gate still passes on the OTHER D9 conditions,
        same fixture as TestD9GatePass.test_gate_passes_renders_inferred)."""
        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)

        result = service._calculate_inferred_dealer_positioning("BTC", "31JUL26", _instruments(), 70000.0)

        assert result.render_inferred is True
        assert result.total_inferred_gex != 0.0


class TestServiceWiring:
    def test_fetch_greeks_and_store_gex_dex_wires_dealer_inventory_into_builder(self):
        from coding.service.on_chain.analysis_builder import OnChainAnalysisBuilder

        flow_rows = [
            {"strike": 70000.0, "option_type": "C", "taker_net": -100.0, "gross_volume": 100.0, "trade_count": 10},
        ]
        repo = _make_repo(first_trade_ts=_COVERAGE_STABLE_MS, flow_rows=flow_rows, coverage=(100, 100))
        service = _make_service(repo)
        service.api.get_ticker.return_value = {
            "greeks": {"delta": 0.5, "gamma": 0.00002, "theta": 0, "vega": 0},
            "mark_iv": 60.0,
            "underlying_price": 70000.0,
        }

        analyzer = MagicMock()
        analyzer.get_expirations.return_value = ["31JUL26"]
        analyzer.parsed_data = {
            "31JUL26": [
                {"instrument_name": "BTC-31JUL26-70000-C", "strike": 70000.0, "option_type": "C"},
            ]
        }
        analyzer.enriched_instruments = {}
        analyzer.index_price = 70000.0
        analyzer.currency = "BTC"

        builder = OnChainAnalysisBuilder("BTC", 70000.0, analyzer.parsed_data)

        service._fetch_greeks_and_store_gex_dex(analyzer, lambda msg: None, builder)

        acc = builder._per_expiration["31JUL26"]
        assert acc.dealer_inventory is not None
        assert isinstance(acc.dealer_inventory, DealerInventoryResult)
