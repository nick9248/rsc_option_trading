"""Brief section (text, staleness, errors, fitting into the brief) and the missing-day check. No database, no network."""

from datetime import datetime, timedelta, timezone
from typing import Any, cast

from coding.core.analytics.futures_flow.taker_flow import TakerFlowHour, summarise
from coding.service.futures_flow.taker_flow_brief_section import (
    TakerFlowBriefSectionBuilder,
    brief_section_provider,
    format_taker_flow_section,
)
from coding.service.futures_flow.taker_flow_chart_service import TakerFlowChartService
from coding.service.on_chain.menthorq_monitor.brief_formatter import format_brief, telegram_length
from tests.fakes.fake_menthorq_monitor import make_record

NOW = datetime(2026, 11, 13, 14, 20, tzinfo=timezone.utc)
LAST_CLOSED = datetime(2026, 11, 13, 13, tzinfo=timezone.utc)


def hours_for(symbol: str, days: int, buy: float, sell: float, last_hour: datetime = LAST_CLOSED):
    return [TakerFlowHour(symbol, last_hour - timedelta(hours=offset), buy, sell) for offset in range(24 * days)][::-1]


class FakeRepository:
    def __init__(self, hours):
        self.hours = hours

    def get_taker_flow_hours(self, symbol, first_hour, last_hour):
        return [h for h in self.hours if h.symbol == symbol and first_hour <= h.hour_utc <= last_hour]


def test_section_shows_ratios_and_labels_it_as_not_options():
    hours = hours_for("BTCUSDT", 9, 90.0, 100.0) + hours_for("ETHUSDT", 9, 110.0, 100.0)
    text = TakerFlowBriefSectionBuilder(cast(Any, FakeRepository(hours))).build(NOW)
    assert "Futures flow (not options)" in text
    rows = [line.replace("</code></pre>", "") for line in text.splitlines()]
    btc_row = next(line for line in rows if line.startswith("BTC"))
    eth_row = next(line for line in rows if line.startswith("ETH"))
    assert btc_row.split() == ["BTC", "0.90", "0.90", "0.90", "0.90", "0.90"]
    assert eth_row.split() == ["ETH", "1.10", "1.10", "1.10", "1.10", "1.10"]
    assert "Data up to 2026-11-13 14:00 UTC." in text
    assert "DATA STALE" not in text


def test_missing_data_is_not_computed_never_zero():
    text = TakerFlowBriefSectionBuilder(cast(Any, FakeRepository([]))).build(NOW)
    assert "BTCUSDT: no hourly data stored" in text
    assert "0.00" not in text and "n/a" in text


def test_stale_data_is_flagged():
    hours = hours_for("BTCUSDT", 9, 90.0, 100.0, last_hour=LAST_CLOSED - timedelta(hours=10))
    text = TakerFlowBriefSectionBuilder(cast(Any, FakeRepository(hours)), symbols=["BTCUSDT"]).build(NOW)
    assert "DATA STALE: the newest hour ended 10 h" in text


def test_read_failure_becomes_a_not_computed_section(caplog):
    class Broken:
        def get_taker_flow_hours(self, *_):
            raise RuntimeError("relation does not exist")

    text = TakerFlowBriefSectionBuilder(cast(Any, Broken())).build(NOW)
    assert "not computed" in text and "RuntimeError" in text and "relation does not exist" not in text
    assert "could not be read" in caplog.text


def test_provider_follows_the_enabled_switch(monkeypatch):
    repository = cast(Any, FakeRepository([]))
    monkeypatch.setenv("TAKER_FLOW_ENABLED", "0")
    assert brief_section_provider(repository) is None
    monkeypatch.setenv("TAKER_FLOW_ENABLED", "1")
    assert brief_section_provider(repository) is not None
    monkeypatch.setenv("TAKER_FLOW_ENABLED", "garbage")
    assert brief_section_provider(repository) is None          # a bad setting must not stop the monitor


def test_section_is_part_of_the_brief_and_counted_in_its_length_limit():
    hours = hours_for("BTCUSDT", 9, 90.0, 100.0) + hours_for("ETHUSDT", 9, 110.0, 100.0)
    section = format_taker_flow_section([summarise("BTCUSDT", [h for h in hours if h.symbol == "BTCUSDT"]),
                                         summarise("ETHUSDT", [h for h in hours if h.symbol == "ETHUSDT"])], NOW)
    record = make_record()
    plain = format_brief(record, [], title="Weekly brief")
    with_section = format_brief(record, [], title="Weekly brief", extra_sections=[section])
    assert with_section.endswith(section) and with_section.startswith(plain[:200])
    # The limit includes the section: the option sections are shortened first.
    limit = telegram_length(with_section) - 40
    shortened = format_brief(record, [], title="Weekly brief", limit=limit, extra_sections=[section])
    assert telegram_length(shortened) <= limit and section in shortened


def test_day_note_names_each_symbol_when_their_last_complete_days_differ():
    btc = hours_for("BTCUSDT", 9, 90.0, 100.0)
    eth = [h for h in hours_for("ETHUSDT", 9, 110.0, 100.0) if h.hour_utc != datetime(2026, 11, 12, 20, tzinfo=timezone.utc)]
    text = TakerFlowBriefSectionBuilder(cast(Any, FakeRepository(btc + eth))).build(NOW)
    assert "Day = BTC 2026-11-12, ETH 2026-11-11 (UTC)." in text


def test_brief_drops_the_extra_section_not_the_whole_brief_when_nothing_else_fits():
    record = make_record()
    huge = "x" * 5000
    message = format_brief(record, [], title="Weekly brief", extra_sections=[huge])
    assert huge not in message and "extra sections omitted" in message and "Too long to send" not in message


def test_yesterday_is_not_expected_complete_before_its_last_hour_has_closed():
    from datetime import date
    from coding.core.analytics.futures_flow.taker_flow import TakerFlowDay
    check = TakerFlowChartService._days_with_missing_hours
    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    days = [TakerFlowDay(date(2026, 11, day), 1.0, 1.0) for day in range(1, 12)]     # 11-01 .. 11-11
    # 11-13 00:02: hour 23 of 11-12 closed at 00:00 but is not collected yet, 11-12 is expected -> reported
    assert check(first, datetime(2026, 11, 13, 0, 2, tzinfo=timezone.utc), days) == ["2026-11-12"]
    # 11-12 14:00: 11-11 is the last expected day, and it is complete -> nothing to report
    assert check(first, datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc), days) == []
