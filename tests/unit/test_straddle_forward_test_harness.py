"""
Unit tests for StraddleForwardTestHarness (increment 2, Part 2).

FakeRepository stands in for DatabaseRepository -- no live DB, no network.
Mirrors the test style of test_straddle_scan_service.py.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from coding.service.scanner.straddle_forward_test_harness import StraddleForwardTestHarness


class FakeRepository:
    def __init__(self, ohlcv_rows: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self.saved_rows: List[Dict[str, Any]] = []
        self.save_returns_true = True
        self.unresolved: List[Dict[str, Any]] = []
        self.resolved_calls: List[Dict[str, Any]] = []
        self._ohlcv_rows = ohlcv_rows or {}

    def save_straddle_scan(self, row: Dict[str, Any]) -> bool:
        self.saved_rows.append(row)
        return self.save_returns_true

    def get_unresolved_straddle_scans(self, currency: str) -> List[Dict[str, Any]]:
        return self.unresolved

    def get_ohlcv_by_date_range(self, currency, start, end) -> List[Dict[str, Any]]:
        key = start.strftime("%Y-%m-%d")
        return self._ohlcv_rows.get(key, [])

    def resolve_straddle_scan(self, scan_id, settlement_index_price, settlement_pnl_usd,
                               settlement_return_pct, resolved_at) -> None:
        self.resolved_calls.append({
            "scan_id": scan_id,
            "settlement_index_price": settlement_index_price,
            "settlement_pnl_usd": settlement_pnl_usd,
            "settlement_return_pct": settlement_return_pct,
            "resolved_at": resolved_at,
        })


def _scan_result(expiries: List[Dict[str, Any]], as_of: Optional[datetime] = None) -> Dict[str, Any]:
    return {
        "as_of": as_of or datetime(2026, 7, 17, 12, 34, tzinfo=timezone.utc),
        "currency": "BTC",
        "index_price": 118432.50,
        "expiries": expiries,
        "excluded": [{"expiry": "01JAN99", "dte": 1.0, "reason": "excluded reason"}],
    }


def _entry(expiry="25SEP26", dte=68.0, F=118432.50, **overrides) -> Dict[str, Any]:
    best = {
        "strike": 118000.0, "cost_usd": 4820.0,
        "breakeven_down": 113180.0, "breakeven_up": 122820.0,
        "call_ask_usd": 2500.0, "put_ask_usd": 2320.0,
        "min_pnl_score": 1240.0,
        "deribit_url": "https://www.deribit.com/options/BTC/BTC-25SEP26-118000-C",
    }
    entry = {
        "expiry": expiry, "dte": dte, "F": F, "atm_iv": 64.8,
        "iv_percentile": 8.2, "iv_percentile_n_obs": 1405, "iv_percentile_window_days": 112.0,
        "rv": 39.4, "rv_iv_ratio": 0.61, "vrp": 25.4,
        "best": best, "candidates": [best],
    }
    entry.update(overrides)
    return entry


# ── record_scan ────────────────────────────────────────────────────────────────

class TestRecordScan:
    def test_records_one_row_per_included_expiry_only(self):
        repo = FakeRepository()
        harness = StraddleForwardTestHarness(repository=repo)
        result = _scan_result([_entry("25SEP26"), _entry("30OCT26", dte=103.0)])

        inserted = harness.record_scan(result, scan_time=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc))

        assert inserted == 2
        assert len(repo.saved_rows) == 2
        # Never records the excluded list
        assert all(r["expiration"] != "01JAN99" for r in repo.saved_rows)

    def test_row_fields_come_from_best_candidate_and_entry(self):
        repo = FakeRepository()
        harness = StraddleForwardTestHarness(repository=repo)
        result = _scan_result([_entry()])

        harness.record_scan(result, scan_time=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc))

        row = repo.saved_rows[0]
        assert row["currency"] == "BTC"
        assert row["expiration"] == "25SEP26"
        assert row["dte"] == 68.0
        assert row["future_price"] == 118432.50
        assert row["index_price"] == 118432.50
        assert row["strike"] == 118000.0
        assert row["cost_usd"] == 4820.0
        assert row["breakeven_down"] == 113180.0
        assert row["breakeven_up"] == 122820.0
        assert row["atm_iv"] == 64.8
        assert row["iv_percentile"] == 8.2
        assert row["iv_percentile_n_obs"] == 1405
        assert row["iv_percentile_window_days"] == 112.0
        assert row["rv"] == 39.4
        assert row["rv_iv_ratio"] == 0.61
        assert row["vrp"] == 25.4
        assert row["min_pnl_score"] == 1240.0
        assert row["deribit_url"].startswith("https://www.deribit.com")

    def test_defaults_scan_time_to_truncated_as_of(self):
        repo = FakeRepository()
        harness = StraddleForwardTestHarness(repository=repo)
        result = _scan_result([_entry()], as_of=datetime(2026, 7, 17, 12, 34, 56, tzinfo=timezone.utc))

        harness.record_scan(result)

        assert repo.saved_rows[0]["scan_time"] == datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)

    def test_dedup_skip_not_counted_as_inserted(self):
        repo = FakeRepository()
        repo.save_returns_true = False
        harness = StraddleForwardTestHarness(repository=repo)
        result = _scan_result([_entry()])

        inserted = harness.record_scan(result, scan_time=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc))

        assert inserted == 0

    def test_no_expiries_records_nothing(self):
        repo = FakeRepository()
        harness = StraddleForwardTestHarness(repository=repo)
        result = _scan_result([])

        inserted = harness.record_scan(result, scan_time=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc))

        assert inserted == 0
        assert repo.saved_rows == []


# ── resolve_due ────────────────────────────────────────────────────────────────

class TestResolveDue:
    def test_resolves_expiry_whose_settlement_date_has_passed(self):
        repo = FakeRepository(ohlcv_rows={"2026-07-10": [{"date": datetime(2026, 7, 10, 8, 0), "close": 120500.0}]})
        repo.unresolved = [{
            "id": 1, "expiration": "10JUL26", "scan_time": datetime(2026, 7, 1, 12, 0),
            "strike": 118000.0, "cost_usd": 4820.0,
        }]
        harness = StraddleForwardTestHarness(repository=repo)

        count = harness.resolve_due("BTC")

        assert count == 1
        assert len(repo.resolved_calls) == 1
        call = repo.resolved_calls[0]
        assert call["scan_id"] == 1
        assert call["settlement_index_price"] == 120500.0
        # Hand-verified: |120500 - 118000| - 4820 = 2500 - 4820 = -2320
        assert call["settlement_pnl_usd"] == pytest.approx(-2320.0)
        # settlement_return_pct = pnl / cost * 100 = -2320/4820*100 = -48.13%
        assert call["settlement_return_pct"] == pytest.approx(-2320.0 / 4820.0 * 100, rel=1e-6)

    def test_future_expiry_not_yet_resolved(self):
        far_future_expiry = (datetime.now(timezone.utc) + timedelta(days=200)).strftime("%d%b%y").upper()
        repo = FakeRepository()
        repo.unresolved = [{
            "id": 2, "expiration": far_future_expiry, "scan_time": datetime.now(timezone.utc),
            "strike": 100000.0, "cost_usd": 3000.0,
        }]
        harness = StraddleForwardTestHarness(repository=repo)

        count = harness.resolve_due("BTC")

        assert count == 0
        assert repo.resolved_calls == []

    def test_missing_settlement_price_leaves_unresolved_no_crash(self):
        """Settlement date has passed but ohlcv row not collected yet -> retry next cycle."""
        repo = FakeRepository(ohlcv_rows={})  # nothing available
        repo.unresolved = [{
            "id": 3, "expiration": "01JAN26", "scan_time": datetime(2025, 12, 1, 12, 0),
            "strike": 90000.0, "cost_usd": 2000.0,
        }]
        harness = StraddleForwardTestHarness(repository=repo)

        count = harness.resolve_due("BTC")

        assert count == 0
        assert repo.resolved_calls == []

    def test_unparseable_expiration_skipped_no_crash(self):
        repo = FakeRepository()
        repo.unresolved = [{
            "id": 4, "expiration": "NOT-A-DATE", "scan_time": datetime(2026, 1, 1),
            "strike": 90000.0, "cost_usd": 2000.0,
        }]
        harness = StraddleForwardTestHarness(repository=repo)

        count = harness.resolve_due("BTC")

        assert count == 0

    def test_no_pending_rows_returns_zero(self):
        repo = FakeRepository()
        harness = StraddleForwardTestHarness(repository=repo)
        assert harness.resolve_due("BTC") == 0
