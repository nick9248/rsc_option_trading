"""``MenthorqReplicationService.replicate_daily_close``: the daily-close numbers without writing any file (in-memory fake repository)."""

from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from coding.core.analytics.menthorq_chart_summary import data_notes, data_warnings
from coding.service.on_chain.menthorq_replication_service import MenthorqReplicationService

NOW = datetime(2026, 11, 7, 9, 48, 59, tzinfo=timezone.utc)
CLOSE = datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)


class FakeRepository:
    """One four-row chain at or before any instant (index 100, IV 50 %); records the requested instants."""

    def __init__(self):
        self.snapshot_requests = []

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        self.snapshot_requests.append(as_of)
        return as_of, [
            {"instrument_name": "BTC-27NOV26-100-C", "expiration": "27NOV26", "strike": 100.0, "option_type": "C",
             "open_interest": 10.0, "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-27NOV26-90-P", "expiration": "27NOV26", "strike": 90.0, "option_type": "P",
             "open_interest": 30.0, "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None},
            # a weekly and a monthly that is not a quarterly, so the three expiry universes differ
            {"instrument_name": "BTC-20NOV26-100-C", "expiration": "20NOV26", "strike": 100.0, "option_type": "C",
             "open_interest": 4.0, "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-29JAN27-100-C", "expiration": "29JAN27", "strike": 100.0, "option_type": "C",
             "open_interest": 6.0, "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None}]

    def get_index_price_at_or_before(self, currency, as_of):
        return 100.0, as_of - timedelta(seconds=30)


def test_daily_close_replications_per_universe_at_the_close_instants_without_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repository = FakeRepository()
    output = MenthorqReplicationService(cast(Any, repository)).replicate_daily_close("BTC", date(2026, 11, 6), NOW)
    assert repository.snapshot_requests == [CLOSE, datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)]
    assert output["as_of_utc"] == "2026-11-06T23:59:00+00:00" and "charts" not in output
    assert set(output["configurations"]) == {"all__gamma_at_index", "monthly__gamma_at_index",
                                            "quarterly_plus_front_month__gamma_at_index"}
    assert output["primary_configuration"] == "monthly__gamma_at_index"
    # hand-calculated levels of the chain (call resistance 100, put support 90, HVL 90), as in test_menthorq_replication_service
    universes = {name.split("__")[0]: output["configurations"][name]["replication"]["settings"]["expirations"]
                 for name in output["configurations"]}
    assert universes["all"] == ["20NOV26", "27NOV26", "29JAN27"]                  # the universes must not be mixed up
    assert universes["monthly"] == ["27NOV26", "29JAN27"]
    assert universes["quarterly_plus_front_month"] == ["27NOV26"]
    assert [entry["expiration"] for entry in output["per_expiry_iv_times_open_interest"]] == universes["all"]
    entry = next(item for item in output["per_expiry_iv_times_open_interest"] if item["expiration"] == "27NOV26")
    assert entry["expiration"] == "27NOV26" and entry["levels"] == {"call_resistance": 100.0, "put_support": 90.0, "hvl": 90.0}
    assert entry["by_strike"][100.0]["call"] == pytest.approx(0.5 * 10.0) and "chart" not in entry
    assert list(tmp_path.iterdir()) == []                  # nothing was written
    assert data_warnings(output) == [] and data_notes(output) == []     # the summary functions read this shape


def test_daily_close_of_a_day_that_has_not_ended_is_rejected():
    with pytest.raises(ValueError, match="has not ended"):
        MenthorqReplicationService(cast(Any, FakeRepository())).replicate_daily_close(
            "BTC", date(2026, 11, 7), datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))
