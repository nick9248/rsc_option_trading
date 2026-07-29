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

    def test_gate_passes_at_exact_boundary(self):
        """Coverage exactly 0.95 and violation_rate exactly 0.05 -- both
        inclusive per spec's literal `>= 0.95 AND <= 0.05`."""
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

    def test_zero_trades_expiry_gates_to_assumed_view_not_crash(self):
        """Edge case (task brief): expiry with zero trades must gate to the
        assumed view, not crash."""
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
