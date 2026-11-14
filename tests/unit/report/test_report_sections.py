"""Tests of the report section builders with fake services (no database, no files outside tmp_path)."""

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from coding.core.analytics.menthorq_replication_calculator import is_monthly_expiry, parse_expiry_settlement
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, StrikeLevelRow
from coding.core.analytics.option_flow.dealer_levels_figure import build_dealer_levels_figure
from coding.core.analytics.option_flow.level_overview import LevelMark, LevelOverview
from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike
from coding.core.analytics.report.section_fragments import millions_text
from coding.service.futures_flow.taker_flow_chart_service import DATA_FILE_NAME, TakerFlowChartResult
from coding.service.option_flow.option_flow_service import OptionFlowService, StrikeFlowReport
from coding.service.report import report_sections
from coding.service.report.report_sections import (
    ALL_EXPIRATIONS, SectionContext, build_dealer_levels_section, build_futures_flow_section, build_levels_section,
    build_menthorq_section, build_option_flow_section, dealer_expiries, figure_json)

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
FAKE_PNG = b"\x89PNG fake"


def make_context(tmp_path, currency="BTC"):
    return SectionContext(currency, NOW, repository=object(), working_directory=tmp_path / "x")


# ---------------------------------------------------------------- option flow

def make_trades(expiry, strike, amount):
    return [OptionTrade(expiry, strike, "C", "buy", amount, False), OptionTrade(expiry, strike, "P", "sell", amount, True)]


class FakeOptionFlowService:
    """Strike flow of a fixed trade list, 14 traded expiries; records the ``end`` of every call."""

    build_figure = staticmethod(OptionFlowService.build_figure)

    def __init__(self, trades, newest_trade=NOW - timedelta(minutes=5), expiries=None):
        self.trades = trades
        self.newest_trade = newest_trade
        self.expiries = expiries if expiries is not None else sorted({trade.expiration for trade in trades})
        self.ends = []

    def strike_flow(self, currency, hours, expiry, end=None):
        self.ends.append(end)
        return StrikeFlowReport(currency, end - timedelta(hours=hours), end, expiry, aggregate_by_strike(self.trades, expiry),
                                self.newest_trade, end - timedelta(days=30))

    def traded_expirations(self, currency, hours, end=None):
        self.ends.append(end)
        return list(self.expiries)


def fourteen_expiry_service():
    labels = [f"{day}DEC26" for day in range(1, 15)]
    # notional of expiry number n is n * 1000 * 68000 per leg pair: the larger the number the more traded
    trades = [trade for number, label in enumerate(labels, start=1) for trade in make_trades(label, 68_000.0, float(number))]
    return FakeOptionFlowService(trades, expiries=labels), labels


def test_option_flow_has_all_view_and_the_twelve_most_traded_single_expiries(tmp_path):
    service, labels = fourteen_expiry_service()
    section = build_option_flow_section(make_context(tmp_path), service)
    views = section.switchers["option-flow"]["views"]
    scopes = {key.split("|")[0] for key in views}
    assert scopes == {ALL_EXPIRATIONS} | set(labels[2:])          # the two least traded (1DEC26, 2DEC26) are dropped
    assert len(scopes) == 13
    assert {key.split("|")[1] for key in views} == {"notional", "contracts", "trades"}
    assert len(views) == 13 * 3


def test_option_flow_all_notional_view_is_the_figure_of_the_service(tmp_path):
    service, _ = fourteen_expiry_service()
    section = build_option_flow_section(make_context(tmp_path), service)
    view = section.switchers["option-flow"]["views"][f"{ALL_EXPIRATIONS}|notional"]
    drawn = section.figures[view["figure"]]
    expected = figure_json(OptionFlowService.build_figure(service.strike_flow("BTC", 24, None, end=NOW), "notional"))
    assert [trace["x"] for trace in drawn["data"]] == [trace["x"] for trace in expected["data"]]
    assert [trace["y"] for trace in drawn["data"]] == [trace["y"] for trace in expected["data"]]


def test_option_flow_caption_shows_the_summed_notional_of_calls_bought(tmp_path):
    service, labels = fourteen_expiry_service()
    section = build_option_flow_section(make_context(tmp_path), service)
    caption = section.switchers["option-flow"]["views"][f"{ALL_EXPIRATIONS}|notional"]["caption"]
    calls_bought = sum(number * 68_000.0 for number in range(1, 15))
    assert millions_text(calls_bought) in caption and "All expirations" in caption


def test_option_flow_asks_for_every_window_as_of_the_report_instant(tmp_path):
    service, _ = fourteen_expiry_service()
    build_option_flow_section(make_context(tmp_path), service)
    assert service.ends and all(end == NOW for end in service.ends)


def test_option_flow_without_trades_has_no_chart_and_says_so(tmp_path):
    service = FakeOptionFlowService([], expiries=[])
    section = build_option_flow_section(make_context(tmp_path), service)
    views = section.switchers["option-flow"]["views"]
    assert section.figures == {}
    assert all("figure" not in view for view in views.values())
    assert "no trades" in views[f"{ALL_EXPIRATIONS}|notional"]["caption"]


def test_option_flow_warns_when_the_newest_trade_is_older_than_three_hours(tmp_path):
    service = FakeOptionFlowService(make_trades("27NOV26", 68_000.0, 1.0), newest_trade=NOW - timedelta(hours=3, minutes=1))
    section = build_option_flow_section(make_context(tmp_path), service)
    assert any("newest stored trade" in warning for warning in section.warnings)


def test_option_flow_has_no_stale_warning_for_a_recent_trade(tmp_path):
    service = FakeOptionFlowService(make_trades("27NOV26", 68_000.0, 1.0), newest_trade=NOW - timedelta(hours=2))
    section = build_option_flow_section(make_context(tmp_path), service)
    assert not any("newest stored trade" in warning or "no stored trades" in warning for warning in section.warnings)


def test_option_flow_warns_when_no_trade_is_stored_at_all(tmp_path):
    service = FakeOptionFlowService([], newest_trade=None, expiries=[])
    section = build_option_flow_section(make_context(tmp_path), service)
    assert any("no stored trades" in warning for warning in section.warnings)


# ---------------------------------------------------------------- dealer levels

def test_dealer_expiries_are_the_nearest_five_plus_the_nearest_monthly_with_the_monthly_first():
    labels = ["15NOV26", "16NOV26", "17NOV26", "18NOV26", "19NOV26", "20NOV26", "27NOV26", "25DEC26"]
    assert is_monthly_expiry(parse_expiry_settlement("27NOV26"))
    chosen, first = dealer_expiries(labels)
    assert chosen == labels[:5] + ["27NOV26"] and first == "27NOV26"


def test_dealer_expiries_with_the_monthly_among_the_nearest_adds_nothing():
    chosen, first = dealer_expiries(["15NOV26", "27NOV26", "04DEC26"])
    assert chosen == ["15NOV26", "27NOV26", "04DEC26"] and first == "27NOV26"


def test_dealer_expiries_without_a_monthly_select_the_soonest():
    chosen, first = dealer_expiries(["15NOV26", "16NOV26", "17NOV26"])
    assert chosen == ["15NOV26", "16NOV26", "17NOV26"] and first == "15NOV26"


def test_dealer_expiries_of_an_empty_list():
    assert dealer_expiries([]) == ([], None)


def make_levels(expiry, gate_passed=True, flow_gamma_flip=67_100.0):
    return DealerFlowLevels(
        currency="BTC", expiry=expiry, index_price=67_000.0, chain_captured_at=NOW - timedelta(minutes=10),
        flow_start=NOW - timedelta(days=60), trades_used=50,
        rows=[StrikeLevelRow(66_000.0, -3.0, 2.0, -1_000_000.0, -2_000_000.0, 10.0, 20.0),
              StrikeLevelRow(68_000.0, 4.0, -1.0, 2_500_000.0, 3_000_000.0, 30.0, 40.0)],
        flow_call_wall=68_000.0, flow_put_wall=66_000.0, flow_gamma_flip=flow_gamma_flip,
        open_interest_call_wall=68_000.0, open_interest_put_wall=66_000.0, open_interest_gamma_flip=None,
        total_flow_gex=1_500_000.0, total_open_interest_gex=1_000_000.0, coverage=1.0, violation_rate=0.0, violations=0,
        options_checked=10, options_without_open_interest=0, gate_passed=gate_passed,
        gate_reason=None if gate_passed else "too many violations")


class FakeDealerService:
    build_figure = staticmethod(build_dealer_levels_figure)

    def __init__(self, expirations, failing=(), failed_check=(), without_flip=()):
        self._expirations = expirations
        self.failing = set(failing)
        self.failed_check = set(failed_check)
        self.without_flip = set(without_flip)

    def expirations(self, currency):
        return list(self._expirations)

    def levels(self, currency, expiry):
        if expiry in self.failing:
            raise LookupError("no open interest")
        return make_levels(expiry, expiry not in self.failed_check, None if expiry in self.without_flip else 67_100.0)


def test_dealer_section_lists_an_expiry_that_raised_as_not_computed_and_keeps_the_others(tmp_path):
    service = FakeDealerService(["15NOV26", "16NOV26", "27NOV26"], failing=["16NOV26"])
    section = build_dealer_levels_section(make_context(tmp_path), service)
    views = section.switchers["dealer-levels"]["views"]
    assert set(views) == {"15NOV26", "27NOV26"}
    assert any(note.startswith("Not computed: 16NOV26") and "no open interest" in note for note in section.notes)
    assert section.switchers["dealer-levels"]["selected"] == {"expiry": "27NOV26"}


def test_dealer_section_fails_when_every_expiry_raises(tmp_path):
    service = FakeDealerService(["15NOV26", "16NOV26"], failing=["15NOV26", "16NOV26"])
    with pytest.raises(LookupError, match="no expiry could be computed"):
        build_dealer_levels_section(make_context(tmp_path), service)


def test_dealer_section_fails_without_expirations(tmp_path):
    with pytest.raises(LookupError, match="option-chain snapshot"):
        build_dealer_levels_section(make_context(tmp_path), FakeDealerService([]))


def test_dealer_section_marks_an_expiry_whose_plausibility_check_failed(tmp_path):
    service = FakeDealerService(["15NOV26", "16NOV26"], failed_check=["16NOV26"])
    section = build_dealer_levels_section(make_context(tmp_path), service)
    html_of_buttons = section.body_html
    assert "16NOV26 (check failed)" in html_of_buttons and "15NOV26 (check failed)" not in html_of_buttons


def test_dealer_caption_says_not_computed_for_a_missing_level_never_zero(tmp_path):
    service = FakeDealerService(["15NOV26"], without_flip=["15NOV26"])
    section = build_dealer_levels_section(make_context(tmp_path), service)
    caption = section.switchers["dealer-levels"]["views"]["15NOV26"]["caption"]
    assert "not computed" in caption
    assert "<td class=\"num\">0</td>" not in caption


# ---------------------------------------------------------------- levels

class FakeLevelService:
    def __init__(self):
        self.calls = []

    def overview(self, currency, expiry, hours):
        self.calls.append((currency, expiry, hours))
        return LevelOverview("BTC", 67_000.0, "27NOV26", [
            LevelMark("MenthorQ replica", "call resistance", 70_000.0, "resistance"),
            LevelMark("Dealer GEX (OI)", "put support", 64_000.0, "support")])


def test_levels_section_embeds_the_page_with_data_and_the_override_style(tmp_path):
    service = FakeLevelService()
    section = build_levels_section(make_context(tmp_path), service)
    page = section.pages["levels-page"]
    assert service.calls == [("BTC", None, 24)]
    assert "<style>main { padding: 4px 0 24px !important; }</style></head>" in page
    assert "/*__DATA__*/null" not in page
    assert "call resistance" in page and "27NOV26" in section.as_of
    assert 'data-page="levels-page"' in section.body_html


# ---------------------------------------------------------------- MenthorQ

class FakeMenthorqService:
    """Writes tiny PNG files where the builder looks for them and returns the output the builder reads."""

    created = []

    def __init__(self, repository):
        self.repository = repository

    def run_menthorq_daily_close(self, currency, menthorq_date, output_directory, **kwargs):
        folder = output_directory / menthorq_date.isoformat()
        folder.mkdir(parents=True)
        charts = {}
        for chart in ("iv_times_open_interest", "net_gex_panels", "net_dex"):
            for suffix in ("", "_all_listed_expiries"):
                name = f"{chart}{suffix}.png"
                (folder / name).write_bytes(FAKE_PNG + name.encode())
                charts[f"{chart}{suffix}"] = name
        (folder / "expiry.png").write_bytes(FAKE_PNG + b"expiry")
        FakeMenthorqService.created.append((currency, menthorq_date, kwargs))
        return {
            "charts": charts,
            "per_expiry_iv_times_open_interest": [
                {"expiration": "27NOV26", "chart": "expiry.png", "levels": {"call_resistance": 70_000.0, "put_support": None, "hvl": 66_000.0}},
                {"expiration": "25DEC26", "chart": "expiry.png", "levels": None}],
            "inputs": {"snapshot_captured_at_utc": "2026-11-13T23:58:00+00:00",
                       "open_interest_captured_at_utc": "2026-11-13T00:01:00+00:00", "index_price": 67_000.0}}


def test_menthorq_section_views_carry_the_data_uris_of_the_written_files(tmp_path, monkeypatch):
    monkeypatch.setattr(report_sections, "MenthorqReplicationService", FakeMenthorqService)
    monkeypatch.setattr(report_sections, "data_warnings", lambda output: ["iv rebuilt"])
    monkeypatch.setattr(report_sections, "data_notes", lambda output: ["gap noted"])
    section = build_menthorq_section(make_context(tmp_path))
    views = section.switchers["menthorq-main"]["views"]
    assert len(views) == 6
    uri = views["net_dex|monthly"]["image"]
    assert base64.b64decode(uri.split(",", 1)[1]) == FAKE_PNG + b"net_dex.png"
    assert base64.b64decode(views["net_dex|_all_listed_expiries"]["image"].split(",", 1)[1]) == FAKE_PNG + b"net_dex_all_listed_expiries.png"
    assert FakeMenthorqService.created[-1][1].isoformat() == "2026-11-13"      # last complete UTC day
    assert section.warnings == ["iv rebuilt"] and section.notes[0] == "gap noted"


def test_menthorq_section_has_a_per_expiry_switcher_with_levels_caption(tmp_path, monkeypatch):
    monkeypatch.setattr(report_sections, "MenthorqReplicationService", FakeMenthorqService)
    monkeypatch.setattr(report_sections, "data_warnings", lambda output: [])
    monkeypatch.setattr(report_sections, "data_notes", lambda output: [])
    section = build_menthorq_section(make_context(tmp_path))
    views = section.switchers["menthorq-expiry"]["views"]
    assert set(views) == {"27NOV26", "25DEC26"}
    assert "call resistance 70,000" in views["27NOV26"]["caption"] and "HVL 66,000" in views["27NOV26"]["caption"]
    assert "put support" not in views["27NOV26"]["caption"]
    assert "levels not computed" in views["25DEC26"]["caption"]


# ---------------------------------------------------------------- futures flow

class FakeTakerFlowChartService:
    png_names = ["chart.png"]
    symbols_data = {"BTCUSDT": {"days": [{"day": "2026-11-13", "taker_buy_usd": 3e6, "taker_sell_usd": 1e6, "ratio": 3.0}]}}
    warnings = ["hour missing"]

    def __init__(self, repository, symbols, clock):
        self.symbols = symbols
        self.clock = clock

    def generate(self, working_directory):
        folder = working_directory / "taker"
        folder.mkdir(parents=True)
        files = []
        for name in self.png_names:
            (folder / name).write_bytes(FAKE_PNG)
            files.append(folder / name)
        (folder / DATA_FILE_NAME).write_text(json.dumps({"symbols": self.symbols_data}), encoding="utf-8")
        return TakerFlowChartResult(folder, files, list(self.warnings), NOW - timedelta(hours=1))


def test_futures_section_embeds_the_chart_and_the_day_table(tmp_path, monkeypatch):
    monkeypatch.setattr(report_sections, "TakerFlowChartService", FakeTakerFlowChartService)
    section = build_futures_flow_section(make_context(tmp_path))
    assert "data:image/png;base64," + base64.b64encode(FAKE_PNG).decode() in section.body_html
    assert "2026-11-13" in section.body_html and "2.0M USD" in section.body_html
    assert section.warnings == ["hour missing"]
    assert "10:00 UTC" in section.as_of            # the newest hour 09:00 plus one hour


def test_futures_section_fails_when_the_service_wrote_no_picture(tmp_path, monkeypatch):
    class NoPicture(FakeTakerFlowChartService):
        png_names = []
    monkeypatch.setattr(report_sections, "TakerFlowChartService", NoPicture)
    with pytest.raises(LookupError, match="hour missing"):
        build_futures_flow_section(make_context(tmp_path))


def test_futures_section_fails_for_a_currency_without_a_perpetual(tmp_path):
    with pytest.raises(LookupError, match="no Binance perpetual"):
        build_futures_flow_section(make_context(tmp_path, currency="SOL"))
