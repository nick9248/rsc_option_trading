"""Tests for DefinedRiskForwardTestHarness."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from coding.service.scanner.defined_risk_forward_test_harness import DefinedRiskForwardTestHarness


class FakeRepository:
    def __init__(self):
        self.saved_rows: List[Dict[str, Any]] = []
        self.resolved: List[Dict[str, Any]] = []
        self._unresolved: List[Dict[str, Any]] = []
        self._ohlcv_by_currency: Dict[str, List[Dict[str, Any]]] = {}

    def save_defined_risk_scan(self, row: Dict[str, Any]) -> bool:
        key = (row["currency"], row["expiration"], row["structure_type"], row["scan_time"])
        if any((r["currency"], r["expiration"], r["structure_type"], r["scan_time"]) == key for r in self.saved_rows):
            return False
        row = dict(row)
        row["id"] = len(self.saved_rows) + 1
        self.saved_rows.append(row)
        self._unresolved.append(row)
        return True

    def get_unresolved_defined_risk_scans(self, currency: str, structure_type: str) -> List[Dict[str, Any]]:
        return [r for r in self._unresolved if r["currency"] == currency and r["structure_type"] == structure_type]

    def resolve_defined_risk_scan(self, scan_id, settlement_index_price, settlement_pnl_usd, settlement_return_pct, resolved_at) -> None:
        self.resolved.append({"scan_id": scan_id, "settlement_index_price": settlement_index_price,
                               "settlement_pnl_usd": settlement_pnl_usd, "settlement_return_pct": settlement_return_pct})
        self._unresolved = [r for r in self._unresolved if r["id"] != scan_id]

    def get_ohlcv_by_date_range(self, currency: str, start, end) -> List[Dict[str, Any]]:
        rows = self._ohlcv_by_currency.get(currency, [])
        return [r for r in rows if start <= r["date"] <= end]


def _ic_scan_result(expiry="1SEP26", dte=39.0):
    candidate = {
        "structure_type": "iron_condor", "short_call": 70000.0, "long_call": 72000.0,
        "short_put": 58000.0, "long_put": 56000.0,
        "cost_or_credit": 500.0, "max_loss": 1500.0, "max_profit": 500.0,
        "breakeven_lo": 57500.0, "breakeven_hi": 70500.0,
        "prob_profit": 60.0, "ev": 10.0, "deribit_url": "https://www.deribit.com/options/BTC",
    }
    return {
        "as_of": datetime.now(timezone.utc), "currency": "BTC", "index_price": 65000.0,
        "expiries": [{
            "expiry": expiry, "dte": dte, "F": 65000.0,
            "regime": {"net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.88, "gate_pass": True},
            "best": candidate, "candidates": [candidate],
        }],
        "excluded": [],
    }


def _bf_scan_result(expiry="1SEP26", dte=39.0):
    candidate = {
        "structure_type": "butterfly", "k1": 63000.0, "k2": 65000.0, "k3": 67000.0,
        # max_loss deliberately different from cost_or_credit here (unlike real butterfly
        # candidates, where they're equal by construction) so this test can distinguish
        # which field resolve_due's butterfly branch actually divides by.
        "cost_or_credit": 400.0, "max_loss": 999.0, "max_profit": 1600.0,
        "breakeven_lo": 63400.0, "breakeven_hi": 66600.0,
        "prob_profit": 45.0, "ev": 20.0, "deribit_url": "https://www.deribit.com/options/BTC",
    }
    return {
        "as_of": datetime.now(timezone.utc), "currency": "BTC", "index_price": 65000.0,
        "expiries": [{
            "expiry": expiry, "dte": dte, "F": 65000.0,
            "regime": {"net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.88, "gate_pass": True},
            "best": candidate, "candidates": [candidate],
        }],
        "excluded": [],
    }


class TestRecordScan:
    def test_records_one_row_per_included_expiry(self):
        repo = FakeRepository()
        harness = DefinedRiskForwardTestHarness(repository=repo)
        inserted = harness.record_scan(_ic_scan_result(), structure_type="iron_condor", scan_time=datetime.now(timezone.utc))
        assert inserted == 1
        assert repo.saved_rows[0]["structure_type"] == "iron_condor"
        assert repo.saved_rows[0]["short_call"] == 70000.0

    def test_skips_expiries_with_no_best_candidate(self):
        repo = FakeRepository()
        harness = DefinedRiskForwardTestHarness(repository=repo)
        result = _ic_scan_result()
        result["expiries"][0]["best"] = None
        inserted = harness.record_scan(result, structure_type="iron_condor", scan_time=datetime.now(timezone.utc))
        assert inserted == 0


class TestResolveDue:
    def test_resolves_matured_expiry_with_known_settlement(self):
        repo = FakeRepository()
        past_expiry = "1JAN26"
        repo._ohlcv_by_currency["BTC"] = [{"date": datetime(2026, 1, 1, 8, 0), "close": 65000.0}]
        harness = DefinedRiskForwardTestHarness(repository=repo)
        harness.record_scan(_ic_scan_result(expiry=past_expiry), structure_type="iron_condor", scan_time=datetime.now(timezone.utc))

        resolved_count = harness.resolve_due("BTC", "iron_condor")
        assert resolved_count == 1
        # settlement 65000 is between short strikes (58000..70000) -> full credit kept -> pnl = 500.0
        assert repo.resolved[0]["settlement_pnl_usd"] == 500.0
        # return on capital-at-risk (max_loss=1500), NOT credit: 500/1500*100
        assert abs(repo.resolved[0]["settlement_return_pct"] - (500.0 / 1500.0 * 100.0)) < 0.01

    def test_resolves_matured_butterfly_expiry_using_cost_or_credit_as_capital(self):
        repo = FakeRepository()
        past_expiry = "1JAN26"
        # settlement 66000 is between k2 (65000) and k3 (67000) -> partial payout, not max/zero.
        repo._ohlcv_by_currency["BTC"] = [{"date": datetime(2026, 1, 1, 8, 0), "close": 66000.0}]
        harness = DefinedRiskForwardTestHarness(repository=repo)
        harness.record_scan(_bf_scan_result(expiry=past_expiry), structure_type="butterfly", scan_time=datetime.now(timezone.utc))

        resolved_count = harness.resolve_due("BTC", "butterfly")
        assert resolved_count == 1
        # payout = max(66000-63000,0) - 2*max(66000-65000,0) + max(66000-67000,0) = 3000 - 2000 + 0 = 1000
        # pnl = payout - cost_or_credit = 1000 - 400 = 600
        assert repo.resolved[0]["settlement_pnl_usd"] == 600.0
        # return on capital-at-risk for a debit structure is cost_or_credit (400), NOT max_loss: 600/400*100
        assert abs(repo.resolved[0]["settlement_return_pct"] - (600.0 / 400.0 * 100.0)) < 0.01

    def test_leaves_unresolved_when_settlement_not_yet_available(self):
        repo = FakeRepository()
        future_expiry = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%d%b%y").upper()
        harness = DefinedRiskForwardTestHarness(repository=repo)
        harness.record_scan(_ic_scan_result(expiry=future_expiry), structure_type="iron_condor", scan_time=datetime.now(timezone.utc))
        resolved_count = harness.resolve_due("BTC", "iron_condor")
        assert resolved_count == 0

    def test_no_pending_rows_returns_zero(self):
        repo = FakeRepository()
        harness = DefinedRiskForwardTestHarness(repository=repo)
        assert harness.resolve_due("BTC", "iron_condor") == 0
