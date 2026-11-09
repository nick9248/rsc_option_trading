"""
MenthorqLevelsBacktestService with an in-memory fake repository (no
database): missing / stale days are "not computed" with a reason, a level
change caused only by an expiry settling is attributed to it, and forward
outcomes never read past the end of the data.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple, cast

import pytest

from coding.core.analytics.menthorq_levels_backtest_calculator import (
    CHANGE_EXPIRY_SETTLEMENT,
    CHANGE_UNCHANGED,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_levels_backtest_service import MenthorqLevelsBacktestService

SPOT = 80000.0
# One old capture at 2026-10-26 23:50 UTC, then nothing until 2026-10-28 20:00,
# then a capture 10 minutes before every query. So 27 Oct has a stale 23:59
# snapshot, 28 Oct a stale 00:00 open-interest snapshot, 29 and 30 Oct are complete.
# (Dates are in the past so the service's "data end" lookup at the wall clock works.)
OLD_CAPTURE = datetime(2026, 10, 26, 23, 50, tzinfo=timezone.utc)
FRESH_FROM = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
STALE_DAY = date(2026, 10, 27)
LAST_DAY = date(2026, 10, 30)


def chain_row(name, expiration, strike, option_type, open_interest):
    return {"instrument_name": name, "expiration": expiration, "strike": strike,
            "option_type": option_type, "open_interest": open_interest, "mark_iv": 50.0,
            "underlying_price": SPOT, "mark_price": None}


# 30OCT26 settles 2026-10-30 08:00 UTC. Its large 81000 call is the Call
# Resistance on 29 Oct; after it settles only 27NOV26 is left (call 85000,
# put 80000), so the Call Resistance becomes 85000 and the HVL stays 80000.
# The 75000 call has no open interest (net GEX 0): it keeps the cumulative
# net GEX minimum (80000) off the chain edge.
CHAIN_WITHOUT_EDGE_PADDING = [
    chain_row("A", "30OCT26", 81000.0, "C", 1000.0),
    chain_row("B", "27NOV26", 85000.0, "C", 100.0),
    chain_row("C", "27NOV26", 80000.0, "P", 100.0),
]
CHAIN = CHAIN_WITHOUT_EDGE_PADDING + [chain_row("D", "27NOV26", 75000.0, "C", 0.0)]


class FakeRepository:
    """Serves ``chain`` with the capture times described above; index price until ``data_end``."""

    data_end = datetime(2026, 10, 30, 23, 59, tzinfo=timezone.utc) + timedelta(hours=30)

    def __init__(self, chain=None):
        self.chain = CHAIN if chain is None else chain

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        if as_of >= FRESH_FROM:
            return as_of - timedelta(minutes=10), [dict(row) for row in self.chain]
        if as_of >= OLD_CAPTURE:
            return OLD_CAPTURE, [dict(row) for row in self.chain]
        return None

    def get_index_price_at_or_before(self, currency, as_of):
        instant = min(as_of, self.data_end)
        return SPOT, instant - timedelta(seconds=7)


@pytest.fixture
def rows():
    service = MenthorqLevelsBacktestService(FakeRepository())
    return service.build_daily_rows("BTC", STALE_DAY, LAST_DAY, universes=("all",))


def test_missing_and_stale_days_are_not_computed_with_a_reason(rows):
    assert [row["date"] for row in rows] == ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    # 27 Oct: the newest snapshot at/before 23:59 is 26 Oct 23:50 = 24 h 9 min = 1449 min old
    # (the monitor's wording: one shared stale_input_reasons)
    assert rows[0]["status"] == "not_computed"
    assert rows[0]["reason"] == "snapshot 1449 min older than 23:59 UTC (limit 60)"
    # 28 Oct: the open-interest snapshot at/before 00:00 UTC is 26 Oct 23:50 = 1 day 0:10 = 1450 min old
    assert rows[1]["status"] == "not_computed"
    assert rows[1]["reason"] == "open-interest snapshot 1450 min older than 00:00 UTC (limit 60)"
    assert rows[1]["index_price"] is None
    assert rows[2]["status"] == "computed"


@pytest.mark.parametrize("ages", [
    {"snapshot_age": timedelta(minutes=61)},
    {"open_interest_age": timedelta(minutes=75), "index_age": timedelta(minutes=90, seconds=30)},
    {"snapshot_age": timedelta(minutes=60)},                     # exactly the limit: computed
])
def test_the_backtest_refuses_the_same_days_as_the_monitor_with_the_same_reasons(ages):
    from tests.fakes.fake_menthorq_monitor import FakeInputLoader
    from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
    loader = FakeInputLoader(**ages)
    day = date(2026, 11, 6)
    monitor_record = MenthorqMonitorService(repository=cast(DatabaseRepository, object()),
                                            input_loader=loader).compute_daily_close_record(day)
    backtest = MenthorqLevelsBacktestService(cast(DatabaseRepository, object()))
    backtest.replication_service.load_inputs = loader.load_inputs   # same fake inputs
    backtest_inputs = backtest._load_day_inputs("BTC", datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc))
    if monitor_record.has_levels:
        assert "reason" not in backtest_inputs
    else:
        assert backtest_inputs["reason"] == "; ".join(monitor_record.not_computed_reasons)


def test_level_change_from_a_settled_expiry_is_attributed_to_it(rows):
    before, after = rows[2], rows[3]
    assert before["all_call_resistance"] == 81000.0
    assert after["all_call_resistance"] == 85000.0
    assert after["all_settled_expirations"] == "30OCT26"
    assert after["all_call_resistance_without_settled"] == 85000.0
    assert after["all_call_resistance_change_attribution"] == CHANGE_EXPIRY_SETTLEMENT
    assert before["all_hvl"] == after["all_hvl"] == 80000.0
    assert after["all_hvl_change_attribution"] == CHANGE_UNCHANGED
    # the day after a not-computed day has no previous day to compare with
    assert before["all_call_resistance_change_attribution"] is None
    assert before["all_gamma_regime"] == "spot_at_hvl"


def test_hvl_at_the_chain_edge_is_not_reported_like_the_monitor():
    # Without the 75000 padding row 80000 is the LOWEST strike: the monitor's
    # rule (all_expiries_levels) leaves HVL and the regime None; the walls stay.
    service = MenthorqLevelsBacktestService(FakeRepository(CHAIN_WITHOUT_EDGE_PADDING))
    edge_rows = service.build_daily_rows("BTC", STALE_DAY, LAST_DAY, universes=("all",))
    before, after = edge_rows[2], edge_rows[3]
    assert before["all_hvl"] is None and after["all_hvl"] is None
    assert before["all_gamma_regime"] is None
    assert before["all_levels_not_computed"] == "hvl"
    assert before["all_call_resistance"] == 81000.0 and after["all_call_resistance"] == 85000.0
    assert before["all_put_support"] == 80000.0


def test_forward_outcomes_stop_at_the_end_of_the_data(rows):
    service = MenthorqLevelsBacktestService(FakeRepository())
    service.add_price_outcomes("BTC", rows)
    last = rows[3]   # 30 Oct 23:59 + 24 h is inside the data, + 72 h is not
    assert last["next_1d_realized_volatility_annualized_percent"] == 0.0   # flat price
    assert last["next_1d_absolute_log_return_percent"] == 0.0
    assert last["next_3d_realized_volatility_annualized_percent"] is None
    assert last["next_3d_final_price"] is None


class VaryingPriceRepository(DatabaseRepository):
    """Index price that changes every hour, with a gap (stale samples) and an end of data.
    Counts the index-price queries. Subclasses the real repository WITHOUT calling its
    __init__ (no connection pool), so the type checker checks the signature."""

    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    data_end = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    gap = (datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc), datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))

    def __init__(self):
        self.index_price_queries = 0

    def get_index_price_at_or_before(self, currency: str, as_of: datetime) -> Optional[Tuple[float, datetime]]:
        self.index_price_queries += 1
        instant = min(as_of, self.data_end)
        if self.gap[0] <= instant < self.gap[1]:
            instant = self.gap[0] - timedelta(hours=3)        # newest trade 3+ h old: stale sample
        hours = int((instant - self.start).total_seconds() // 3600)
        return 80000.0 + 37.0 * hours + (hours % 5) * 11.0, instant - timedelta(seconds=7)


def _old_add_price_outcomes(service, currency, rows):
    """The implementation before the single-fetch change (one query per sample per horizon)."""
    from coding.core.analytics.menthorq_levels_backtest_calculator import forward_price_outcomes
    from coding.service.on_chain.menthorq_levels_backtest_service import OUTCOME_HORIZONS_HOURS

    latest = service.repository.get_index_price_at_or_before(currency, datetime.now(timezone.utc))
    data_end = latest[1] if latest else datetime.min.replace(tzinfo=timezone.utc)
    for row in rows:
        as_of = datetime.fromisoformat(row["as_of_utc"])
        for horizon, hours in OUTCOME_HORIZONS_HOURS.items():
            prices = [row["index_price"]] + [
                service._price_at(currency, as_of + timedelta(hours=step), data_end)
                for step in range(1, hours + 1)]
            outcome = forward_price_outcomes(prices, step_hours=1)
            row[f"next_{horizon}_realized_volatility_annualized_percent"] = \
                outcome["realized_volatility_annualized_percent"]
            row[f"next_{horizon}_absolute_log_return_percent"] = outcome["absolute_log_return_percent"]
            row[f"next_{horizon}_maximum_price"] = outcome["maximum_price"]
            row[f"next_{horizon}_minimum_price"] = outcome["minimum_price"]
            row[f"next_{horizon}_final_price"] = prices[-1] if outcome["maximum_price"] is not None else None
        trailing = [service._price_at(currency, as_of - timedelta(hours=step), data_end)
                    for step in range(24, 0, -1)] + [row["index_price"]]
        row["trailing_1d_realized_volatility_annualized_percent"] = forward_price_outcomes(
            trailing, step_hours=1)["realized_volatility_annualized_percent"]


def test_single_fetch_outcomes_are_identical_to_the_old_logic_with_fewer_queries():
    """8 days 1..8 Oct (23:59): 3 Oct has no index price (not computed); 4 Oct's 1d/3d
    windows cross the 5 Oct gap; 6..8 Oct's 3d window runs past the data end."""
    def base_rows():
        rows = []
        for day in range(1, 9):
            as_of = datetime(2026, 10, day, 23, 59, tzinfo=timezone.utc)
            price = None if day == 3 else 80000.0 + day * 100.0
            rows.append({"date": as_of.date().isoformat(), "as_of_utc": as_of.isoformat(), "index_price": price})
        return rows

    old_repository, new_repository = VaryingPriceRepository(), VaryingPriceRepository()
    old_rows, new_rows = base_rows(), base_rows()
    _old_add_price_outcomes(MenthorqLevelsBacktestService(old_repository), "BTC", old_rows)
    MenthorqLevelsBacktestService(new_repository).add_price_outcomes("BTC", new_rows)

    assert new_rows == old_rows
    # Samples after the data end (9 Oct 04:59:53) are not queried. Forward samples within
    # it: 1..5 Oct all; 6 Oct 53 h; 7 Oct 29 h; 8 Oct 5 h. Trailing: always 24.
    # Old (1d + 3d + trailing per day, 3 Oct included): 1 + 5*(24+72+24) + (24+53+24)
    #   + (24+29+24) + (5+5+24) = 1 + 600 + 101 + 77 + 34 = 813.
    # New (3d + trailing per priced day, 3 Oct skipped): 1 + 4*(72+24) + (53+24) + (29+24)
    #   + (5+24) = 1 + 384 + 77 + 53 + 29 = 544.
    assert old_repository.index_price_queries == 813
    assert new_repository.index_price_queries == 544
    # the cases the series is built to cover really occur
    by_date = {row["date"]: row for row in new_rows}
    assert by_date["2026-10-03"]["next_1d_final_price"] is None
    assert by_date["2026-10-03"]["trailing_1d_realized_volatility_annualized_percent"] is None
    assert by_date["2026-10-04"]["next_1d_realized_volatility_annualized_percent"] is None   # gap
    assert by_date["2026-10-01"]["next_3d_realized_volatility_annualized_percent"] is not None
    assert by_date["2026-10-07"]["next_1d_final_price"] is not None
    assert by_date["2026-10-07"]["next_3d_final_price"] is None                               # data end


def test_primary_universe_is_only_a_label_and_every_universe_is_analysed(tmp_path):
    """run() with "monthly" and with "all" as primary universe gives the same numbers;
    only the label differs, and the JSON says the label selects nothing."""
    import json

    analyses = {}
    for label in ("monthly", "all"):
        MenthorqLevelsBacktestService(cast(DatabaseRepository, FakeRepository())).run(
            "BTC", STALE_DAY, LAST_DAY, tmp_path / label, primary_universe=label)
        analyses[label] = json.loads((tmp_path / label / "btc_backtest_analysis.json").read_text(encoding="utf-8"))

    for label, analysis in analyses.items():
        assert analysis["primary_universe_label"] == label
        assert "primary_universe" not in analysis
        assert analysis["universes_analysed"] == ["monthly", "all"]
        assert set(analysis["trend"]) == set(analysis["predictive_tests"]) == set(analysis["alerts"]) \
            == {"monthly", "all"}
        assert analysis["primary_universe_label_note"].startswith("label only")
    for key in ("coverage", "trend", "predictive_tests", "alerts", "test_count"):
        assert analyses["monthly"][key] == analyses["all"][key], key
