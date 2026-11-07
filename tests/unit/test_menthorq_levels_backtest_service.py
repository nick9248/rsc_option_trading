"""
MenthorqLevelsBacktestService with an in-memory fake repository (no
database): missing / stale days are "not computed" with a reason, a level
change caused only by an expiry settling is attributed to it, and forward
outcomes never read past the end of the data.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from coding.core.analytics.menthorq_levels_backtest_calculator import (
    CHANGE_EXPIRY_SETTLEMENT,
    CHANGE_UNCHANGED,
)
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
CHAIN = [
    chain_row("A", "30OCT26", 81000.0, "C", 1000.0),
    chain_row("B", "27NOV26", 85000.0, "C", 100.0),
    chain_row("C", "27NOV26", 80000.0, "P", 100.0),
]


class FakeRepository:
    """Serves CHAIN with the capture times described above; index price until ``data_end``."""

    data_end = datetime(2026, 10, 30, 23, 59, tzinfo=timezone.utc) + timedelta(hours=30)

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        if as_of >= FRESH_FROM:
            return as_of - timedelta(minutes=10), [dict(row) for row in CHAIN]
        if as_of >= OLD_CAPTURE:
            return OLD_CAPTURE, [dict(row) for row in CHAIN]
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
    assert rows[0]["status"] == "not_computed" and "day incomplete" in rows[0]["reason"]
    # 28 Oct: the open-interest snapshot at/before 00:00 UTC is 1 day 0:10 old
    assert rows[1]["status"] == "not_computed" and "open-interest snapshot" in rows[1]["reason"]
    assert rows[1]["index_price"] is None
    assert rows[2]["status"] == "computed"


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


def test_forward_outcomes_stop_at_the_end_of_the_data(rows):
    service = MenthorqLevelsBacktestService(FakeRepository())
    service.add_price_outcomes("BTC", rows)
    last = rows[3]   # 30 Oct 23:59 + 24 h is inside the data, + 72 h is not
    assert last["next_1d_realized_volatility_annualized_percent"] == 0.0   # flat price
    assert last["next_1d_absolute_log_return_percent"] == 0.0
    assert last["next_3d_realized_volatility_annualized_percent"] is None
    assert last["next_3d_final_price"] is None
