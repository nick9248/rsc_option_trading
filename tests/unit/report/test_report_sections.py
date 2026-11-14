"""Tests of the report section builders with fake services (no database, no files outside tmp_path)."""

import base64
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from coding.core.analytics.menthorq_replication_calculator import is_monthly_expiry, parse_expiry_settlement
from coding.core.analytics.open_interest.expiry_open_interest import open_interest_by_expiry
from coding.service.on_chain.expiry_open_interest_service import ExpiryOpenInterestReport
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, StrikeLevelRow
from coding.core.analytics.option_flow.dealer_levels_figure import build_dealer_levels_figure
from coding.core.analytics.option_flow.level_overview import LevelMark, LevelOverview
from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike
from coding.core.analytics.report.section_fragments import millions_text
from coding.core.analytics.futures_flow.taker_flow import TakerFlowDay
from coding.service.futures_flow.taker_flow_chart_service import TakerFlowFigureData
from coding.core.analytics.option_flow.block_structure import BiggestLeg, BlockStructure
from coding.service.option_flow.option_flow_service import BlockStructureReport, OptionFlowService, StrikeFlowReport
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

    def __init__(self, trades, newest_trade=NOW - timedelta(minutes=5), expiries=None, blocks=()):
        self.trades = trades
        self.newest_trade = newest_trade
        self.expiries = expiries if expiries is not None else sorted({trade.expiration for trade in trades})
        self.ends = []
        self.blocks = blocks              # a list of BlockStructure, or an exception to raise
        self.block_calls = []

    def block_structures(self, currency, hours, end=None, top=10):
        self.block_calls.append((currency, hours, end, top))
        if isinstance(self.blocks, Exception):
            raise self.blocks
        return BlockStructureReport(end - timedelta(hours=hours), end, list(self.blocks), len(self.blocks))

    def strike_flow(self, currency, hours, expiry, end=None):
        self.ends.append(end)
        return StrikeFlowReport(currency, end - timedelta(hours=hours), end, expiry, aggregate_by_strike(self.trades, expiry),
                                self.newest_trade, end - timedelta(days=30))

    def traded_expirations(self, currency, hours, end=None):
        self.ends.append(end)
        return list(self.expiries)


def make_block(block_trade_id):
    leg = BiggestLeg("25DEC26", 120_000.0, "C", "buy", 10.0, 1_200_000.0)
    return BlockStructure(block_trade_id, 1, 1, ["25DEC26"], 1, 0, 1, 0, 1_200_000.0, [leg], "single leg", NOW)


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


def test_option_flow_names_the_expiry_that_carries_the_biggest_bar_and_lists_the_largest_blocks(tmp_path, monkeypatch):
    # 120,000 call bought: 99 contracts on 25DEC26 and 1 on 26MAR27 -> one expiry carries 99%
    trades = [OptionTrade("25DEC26", 120_000.0, "C", "buy", 99.0, False), OptionTrade("26MAR27", 120_000.0, "C", "buy", 1.0, False),
              OptionTrade("25DEC26", 100_000.0, "C", "buy", 5.0, True)]
    service = FakeOptionFlowService(trades, expiries=["25DEC26", "26MAR27"], blocks=[make_block("BLOCK-306229")])
    install_open_interest(monkeypatch)
    section = build_option_flow_section(make_context(tmp_path), service)
    html_text = section.body_html
    assert html_text.index("data-switcher=\"option-flow\"") < html_text.index("Which expiry carries the largest bars") \
        < html_text.index("Largest blocks of the window")
    assert ">25DEC26</button> 99% (41 days)" in html_text and ">26MAR27</button> 1% (132 days)" in html_text
    assert 'data-switcher-target="option-flow" data-control="expiry" data-value="25DEC26"' in html_text
    assert "not explained" not in html_text                  # `single` holds every traded expiry
    assert "BLOCK-306229" in html_text and "off the order book" in html_text
    assert service.block_calls == [("BTC", 24, NOW, 10)]


def test_option_flow_bars_table_uses_every_traded_expiry_not_only_the_kept_ones(tmp_path, monkeypatch):
    service, labels = fourteen_expiry_service()          # 12 kept; the strike 68,000 bars are made of all 14
    section = build_option_flow_section(make_context(tmp_path), service)
    assert "not explained" not in section.body_html


def test_option_flow_section_survives_a_failing_blocks_query_and_says_so(tmp_path):
    service = FakeOptionFlowService(make_trades("27NOV26", 68_000.0, 1.0), blocks=RuntimeError("blocks query failed"))
    section = build_option_flow_section(make_context(tmp_path), service)
    assert not section.failed
    assert "Not available: blocks query failed" in section.body_html and "Which expiry carries the largest bars" in section.body_html
    assert section.switchers["option-flow"]["views"]


def test_option_flow_without_blocks_says_none_are_stored(tmp_path):
    section = build_option_flow_section(make_context(tmp_path), FakeOptionFlowService(make_trades("27NOV26", 68_000.0, 1.0)))
    assert "No block trades are stored in the window." in section.body_html


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
    """Stands in for the service: ``figure_data`` returns fixed data (the section no longer writes any file)."""

    rolling_points = [(NOW - timedelta(hours=3), 0.95), (NOW - timedelta(hours=2), 1.10), (NOW - timedelta(hours=1), 1.02)]
    days = [TakerFlowDay(date(2026, 11, 13), 3e6, 1e6)]
    warnings = ["hour missing"]

    def __init__(self, repository, symbols, clock):
        self.symbols = symbols
        self.clock = clock

    def figure_data(self, symbol):
        return TakerFlowFigureData(symbol, "BTC", [], list(self.rolling_points), list(self.days), list(self.warnings),
                                   NOW - timedelta(hours=1))


def test_futures_section_embeds_the_interactive_figure_and_the_day_table(tmp_path, monkeypatch):
    # Changed: was a PNG data URI in the body; now a Plotly figure drawn by a control-free switcher, and the day table stays.
    monkeypatch.setattr(report_sections, "TakerFlowChartService", FakeTakerFlowChartService)
    section = build_futures_flow_section(make_context(tmp_path))
    assert "data:image/png" not in section.body_html
    figure = section.figures["futures-flow-rolling-ratio"]
    ratio = next(trace for trace in figure["data"] if "rolling 24 h ratio" in trace.get("name", ""))
    assert ratio["y"] == [0.95, 1.10, 1.02]
    assert section.switchers["futures-flow"]["views"][""] == {"figure": "futures-flow-rolling-ratio"}
    assert 'data-switcher="futures-flow"' in section.body_html
    assert "2026-11-13" in section.body_html and "2.0M USD" in section.body_html
    assert section.warnings == ["hour missing"]
    assert "10:00 UTC" in section.as_of            # the newest hour 09:00 plus one hour


def test_futures_section_says_why_no_trend_line_with_the_real_minimum_and_maximum(tmp_path, monkeypatch):
    monkeypatch.setattr(report_sections, "TakerFlowChartService", FakeTakerFlowChartService)
    body = build_futures_flow_section(make_context(tmp_path)).body_html
    assert "No trend lines are drawn" in body and "about 0.95 to 1.10 over these 30 days" in body


def test_futures_section_fails_when_there_is_nothing_to_draw(tmp_path, monkeypatch):
    # Changed: was "the service wrote no picture"; now "no complete day / no rolling point" with the service's warnings.
    class NoDays(FakeTakerFlowChartService):
        days = []
    monkeypatch.setattr(report_sections, "TakerFlowChartService", NoDays)
    with pytest.raises(LookupError, match="hour missing"):
        build_futures_flow_section(make_context(tmp_path))


def test_futures_section_fails_for_a_currency_without_a_perpetual(tmp_path):
    with pytest.raises(LookupError, match="no Binance perpetual"):
        build_futures_flow_section(make_context(tmp_path, currency="SOL"))


# ---------------------------------------------------------------- open interest per expiry

OPEN_INTEREST_BY_EXPIRY = {"25DEC26": 100_000.0, "27NOV26": 60_000.0, "26MAR27": 40_000.0, "20NOV26": 20_000.0,
                           "18NOV26": 10_000.0, "15NOV26": 5_000.0}


def make_open_interest_report(open_interest=None):
    chain = [{"expiration": expiry, "option_type": "C", "strike": 70_000.0, "open_interest": value}
             for expiry, value in (open_interest or OPEN_INTEREST_BY_EXPIRY).items()]
    return ExpiryOpenInterestReport("BTC", NOW - timedelta(minutes=10), 67_000.0, open_interest_by_expiry(chain, 67_000.0, NOW))


def install_open_interest(monkeypatch, open_interest=None, error=None):
    """Replaces the service the context builds; ``built`` records every construction."""
    built = []

    class FakeOpenInterestService:
        def __init__(self, repository, clock):
            built.append(self)

        def open_interest(self, currency):
            if error is not None:
                raise error
            return make_open_interest_report(open_interest)

    monkeypatch.setattr(report_sections, "ExpiryOpenInterestService", FakeOpenInterestService)
    return built


def test_open_interest_is_computed_once_and_is_the_same_object_for_every_section(tmp_path, monkeypatch):
    built = install_open_interest(monkeypatch)
    monkeypatch.setattr(report_sections, "MenthorqReplicationService", FakeMenthorqService)
    monkeypatch.setattr(report_sections, "data_warnings", lambda output: [])
    monkeypatch.setattr(report_sections, "data_notes", lambda output: [])
    context = make_context(tmp_path)
    build_menthorq_section(context)
    build_option_flow_section(context, FakeOptionFlowService([], expiries=[]))
    build_levels_section(context, FakeLevelService())
    assert len(built) == 1
    assert context.open_interest()[0] is context.open_interest()[0]


def test_failed_open_interest_is_computed_once_and_shown_as_a_note_not_as_a_failure(tmp_path, monkeypatch):
    built = install_open_interest(monkeypatch, error=LookupError("no snapshot"))
    context = make_context(tmp_path)
    section = build_option_flow_section(context, FakeOptionFlowService(make_trades("27NOV26", 68_000.0, 1.0)))
    assert "Open interest per expiry not available: no snapshot" in section.body_html
    assert not section.failed and "27NOV26" in section.body_html
    build_levels_section(context, FakeLevelService())
    assert len(built) == 1


def test_menthorq_per_expiry_choices_are_ordered_by_open_interest_with_labels(tmp_path, monkeypatch):
    install_open_interest(monkeypatch)
    monkeypatch.setattr(report_sections, "MenthorqReplicationService", FakeMenthorqService)
    monkeypatch.setattr(report_sections, "data_warnings", lambda output: [])
    monkeypatch.setattr(report_sections, "data_notes", lambda output: [])
    section = build_menthorq_section(make_context(tmp_path))
    assert "<h3>Open interest per expiry</h3>" in section.body_html
    assert section.body_html.index("Open interest per expiry") < section.body_html.index("IV x OI of one expiry")
    configuration = section.switchers["menthorq-expiry"]
    assert configuration["selected"] == {"expiry": "25DEC26"}                  # the largest first, FakeMenthorqService lists 27NOV26 first
    assert section.body_html.index('data-value="25DEC26"') < section.body_html.index('data-value="27NOV26"')
    assert "25DEC26, 41 days, 6.70B USD OI, 43%" in section.body_html


def test_menthorq_without_open_interest_keeps_its_order_and_says_why(tmp_path, monkeypatch):
    install_open_interest(monkeypatch, error=LookupError("no snapshot"))
    monkeypatch.setattr(report_sections, "MenthorqReplicationService", FakeMenthorqService)
    monkeypatch.setattr(report_sections, "data_warnings", lambda output: [])
    monkeypatch.setattr(report_sections, "data_notes", lambda output: [])
    section = build_menthorq_section(make_context(tmp_path))
    assert section.switchers["menthorq-expiry"]["selected"] == {"expiry": "27NOV26"}
    assert "Open interest per expiry not available: no snapshot" in section.body_html


def test_option_flow_always_precomputes_the_top_open_interest_expiries_that_traded(tmp_path, monkeypatch):
    service, labels = fourteen_expiry_service()          # 1DEC26 and 2DEC26 are the least traded: dropped without open interest
    install_open_interest(monkeypatch, {"1DEC26": 900.0, "2DEC26": 800.0, "14DEC26": 700.0, "13DEC26": 600.0, "5DEC26": 1.0})
    section = build_option_flow_section(make_context(tmp_path), service)
    scopes = {key.split("|")[0] for key in section.switchers["option-flow"]["views"]}
    # the four by open interest, the other eight by notional (12DEC26 down to 5DEC26); 3DEC26 and 4DEC26 are out
    assert scopes == {ALL_EXPIRATIONS, "1DEC26", "2DEC26", "14DEC26", "13DEC26"} | {f"{day}DEC26" for day in range(5, 13)}


def test_option_flow_choices_are_ordered_by_open_interest_then_by_settlement_with_expired_labelled(tmp_path, monkeypatch):
    trades = [trade for label in ("13NOV26", "15NOV26", "27NOV26", "25DEC26", "20NOV26") for trade in make_trades(label, 68_000.0, 1.0)]
    service = FakeOptionFlowService(trades, expiries=["13NOV26", "15NOV26", "20NOV26", "25DEC26", "27NOV26"])
    install_open_interest(monkeypatch)
    section = build_option_flow_section(make_context(tmp_path), service)
    html_text = section.body_html
    positions = [html_text.index(f'value="{label}"') for label in ("25DEC26", "27NOV26", "20NOV26", "15NOV26", "13NOV26")]
    assert positions == sorted(positions)
    assert "13NOV26, expired" in html_text and "25DEC26, 41 days, 6.70B USD OI, 43%" in html_text


def test_option_flow_open_interest_table_shows_eight_rows_and_the_rest_as_a_sentence(tmp_path, monkeypatch):
    install_open_interest(monkeypatch, {f"{day}DEC26": 1000.0 * (11 - day) for day in range(1, 11)})
    section = build_option_flow_section(make_context(tmp_path), FakeOptionFlowService([], expiries=[]))
    table = section.body_html.split("</table>")[0]
    assert table.count("<tr>") == 1 + 8
    total = sum(1000.0 * (11 - day) for day in range(1, 11))
    remaining = (2000.0 + 1000.0) / total * 100
    assert f"2 more expiries hold the remaining {remaining:.0f}%." in section.body_html


# ---------------------------------------------------------------- levels per expiry

class FakeExpiryLevelService:
    """Returns an overview that carries the expiry it was asked for; ``failing`` expiries raise LookupError."""

    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)

    def overview(self, currency, expiry, hours):
        self.calls.append((currency, expiry, hours))
        if expiry in self.failing:
            raise LookupError("no dealer data")
        return LevelOverview("BTC", 67_000.0, expiry, [LevelMark("MenthorQ replica", f"call resistance {expiry}", 70_000.0, "resistance")])


def test_levels_levels_expiries_are_the_top_four_by_open_interest_plus_the_nearest_monthly():
    rows = {row.expiry: row for row in make_open_interest_report().rows}
    chosen, first = report_sections.levels_expiries(rows, NOW)
    assert chosen == ["25DEC26", "27NOV26", "26MAR27", "20NOV26"] and first == "27NOV26"
    rows = {row.expiry: row for row in make_open_interest_report({**OPEN_INTEREST_BY_EXPIRY, "27NOV26": 1.0}).rows}
    chosen, first = report_sections.levels_expiries(rows, NOW)
    assert chosen == ["25DEC26", "26MAR27", "20NOV26", "18NOV26", "27NOV26"] and first == "27NOV26"      # five views at most


def test_levels_section_has_a_page_view_per_expiry_and_starts_on_the_nearest_monthly(tmp_path, monkeypatch):
    install_open_interest(monkeypatch)
    service = FakeExpiryLevelService()
    section = build_levels_section(make_context(tmp_path), service)
    assert [call[1] for call in service.calls] == ["25DEC26", "27NOV26", "26MAR27", "20NOV26"]
    assert all(call[0] == "BTC" and call[2] == 24 for call in service.calls)
    configuration = section.switchers["levels"]
    assert configuration["selected"] == {"expiry": "27NOV26"}
    assert configuration["views"]["25DEC26"] == {"page": "levels-page-25DEC26"}
    assert set(section.pages) == {f"levels-page-{expiry}" for expiry in ("25DEC26", "27NOV26", "26MAR27", "20NOV26")}
    assert "call resistance 25DEC26" in section.pages["levels-page-25DEC26"]
    assert "Dealer lanes are shown for the 4 expiries with the most open interest and the nearest monthly expiry; " \
           "the other lanes do not depend on the expiry." in section.body_html
    assert "Open interest per expiry" in section.body_html and "27NOV26" in section.as_of


def test_levels_section_skips_an_expiry_whose_overview_fails_and_fails_when_all_do(tmp_path, monkeypatch):
    install_open_interest(monkeypatch)
    section = build_levels_section(make_context(tmp_path), FakeExpiryLevelService(failing=["26MAR27"]))
    assert set(section.switchers["levels"]["views"]) == {"25DEC26", "27NOV26", "20NOV26"}
    assert any(note.startswith("Not computed: 26MAR27") for note in section.notes)
    with pytest.raises(LookupError, match="no expiry could be computed"):
        build_levels_section(make_context(tmp_path), FakeExpiryLevelService(failing=list(OPEN_INTEREST_BY_EXPIRY)))


def test_levels_section_without_open_interest_falls_back_to_the_single_page_with_a_note(tmp_path, monkeypatch):
    install_open_interest(monkeypatch, error=LookupError("no snapshot"))
    service = FakeExpiryLevelService()
    section = build_levels_section(make_context(tmp_path), service)
    assert service.calls == [("BTC", None, 24)] and set(section.pages) == {"levels-page"}
    assert "Open interest per expiry not available: no snapshot" in section.body_html
