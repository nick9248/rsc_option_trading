"""Tests for defined_risk_scan_history repository methods."""
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.database.repository import DatabaseRepository


@pytest.fixture
def repo():
    return DatabaseRepository()


def _row(**overrides):
    base = {
        "scan_time": datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        "currency": "BTC", "expiration": "28AUG26", "structure_type": "iron_condor",
        "dte": 39.0, "future_price": 65000.0, "index_price": 64800.0,
        "short_call": 70000.0, "long_call": 72000.0, "short_put": 58000.0, "long_put": 56000.0,
        "k1": None, "k2": None, "k3": None,
        "cost_or_credit": 500.0, "max_loss": 1500.0, "max_profit": 500.0,
        "breakeven_lo": 57500.0, "breakeven_hi": 70500.0,
        "prob_profit": 60.0, "ev": 10.0,
        "net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.888,
        "gate_pass": True, "deribit_url": "https://www.deribit.com/options/BTC",
    }
    base.update(overrides)
    return base


class TestSaveAndDedup:
    def test_save_inserts_new_row(self, repo):
        row = _row(scan_time=datetime.now(timezone.utc))
        assert repo.save_defined_risk_scan(row) is True

    def test_save_dedups_same_cycle(self, repo):
        row = _row(scan_time=datetime.now(timezone.utc))
        repo.save_defined_risk_scan(row)
        assert repo.save_defined_risk_scan(row) is False


class TestUnresolvedAndResolve:
    def test_unresolved_scans_filtered_by_structure_type(self, repo):
        scan_time = datetime.now(timezone.utc)
        repo.save_defined_risk_scan(_row(scan_time=scan_time, expiration="1SEP26", structure_type="iron_condor"))
        repo.save_defined_risk_scan(_row(scan_time=scan_time, expiration="1SEP26", structure_type="butterfly", k1=63000.0, k2=65000.0, k3=67000.0))
        ic_pending = repo.get_unresolved_defined_risk_scans("BTC", "iron_condor")
        bf_pending = repo.get_unresolved_defined_risk_scans("BTC", "butterfly")
        assert any(r["expiration"] == "1SEP26" for r in ic_pending)
        assert any(r["expiration"] == "1SEP26" for r in bf_pending)

    def test_resolve_sets_settlement_fields(self, repo):
        scan_time = datetime.now(timezone.utc)
        repo.save_defined_risk_scan(_row(scan_time=scan_time, expiration="2SEP26"))
        pending = repo.get_unresolved_defined_risk_scans("BTC", "iron_condor")
        target = next(r for r in pending if r["expiration"] == "2SEP26")
        repo.resolve_defined_risk_scan(
            scan_id=target["id"], settlement_index_price=64000.0,
            settlement_pnl_usd=-1500.0, settlement_return_pct=-100.0,
            resolved_at=datetime.now(timezone.utc),
        )
        still_pending = repo.get_unresolved_defined_risk_scans("BTC", "iron_condor")
        assert not any(r["expiration"] == "2SEP26" for r in still_pending)


class TestAlerting:
    def test_no_prior_alert_returns_none(self, repo):
        assert repo.get_last_alert_for_defined_risk("BTC", "3SEP26", "iron_condor") is None

    def test_mark_and_fetch_last_alert(self, repo):
        scan_time = datetime.now(timezone.utc)
        repo.save_defined_risk_scan(_row(scan_time=scan_time, expiration="4SEP26"))
        repo.mark_defined_risk_scan_alert_sent("BTC", "4SEP26", "iron_condor", scan_time)
        last = repo.get_last_alert_for_defined_risk("BTC", "4SEP26", "iron_condor")
        assert last is not None
        assert last["ev"] == 10.0
