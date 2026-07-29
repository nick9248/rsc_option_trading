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
        exclude) -- this must gate on coverage == 0.0, not on the
        exclusion-fraction check."""
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
