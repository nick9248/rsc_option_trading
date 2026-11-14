"""DecisionMakerService with fakes of the existing services (no database, no network)."""

from datetime import timedelta

import pytest

from coding.core.analytics.option_flow.strike_flow import aggregate_by_strike
from coding.service.decision import decision_maker_service as module
from coding.service.decision.decision_maker_service import DecisionMakerService, summary_lines
from coding.service.futures_flow.taker_flow_chart_service import TakerFlowFigureData
from coding.service.on_chain.expiry_open_interest_service import ExpiryOpenInterestReport
from coding.service.option_flow.option_flow_service import StrikeFlowReport
from tests.unit.decision.test_evidence_extractors import CHAIN_TIME, NOW, dealer_levels, open_interest_row, trade

SEEN_INSTANTS = []
SPOT_TIME = [NOW - timedelta(minutes=30)]       # mutable so a test can age the spot


class FakeRepository:
    def __init__(self):
        self.saved = []

    def get_block_trade_legs_between(self, currency, start_ms, end_ms):
        SEEN_INSTANTS.append(("block legs end", end_ms))
        return []

    def get_liquidation_heatmap_snapshots_between(self, symbol, start, end):
        SEEN_INSTANTS.append(("liquidation end", end))
        return []

    def save_decision_snapshot(self, currency, computed_at, reference_price, report):
        self.saved.append((currency, computed_at, reference_price, report))
        return True


class FakeOpenInterestService:
    def __init__(self, repository, clock):
        self.clock = clock

    def open_interest(self, currency):
        SEEN_INSTANTS.append(("open interest", self.clock()))
        return ExpiryOpenInterestReport(currency, CHAIN_TIME, 65_000.0, [
            open_interest_row(0.4, "25DEC26", NOW.replace(month=12, day=25, hour=8), 4000.0),
            open_interest_row(0.25, "27NOV26", NOW.replace(day=27, hour=8), 2500.0)])


class FakeLoader:
    def __init__(self, repository):
        pass

    def load_inputs(self, currency, as_of):
        SEEN_INSTANTS.append(("menthorq", as_of))
        return {"chain_rows": [], "index_price": 65_000.0, "snapshot_captured_at_utc": CHAIN_TIME,
                "index_price_time_utc": SPOT_TIME[0]}


class FakeDealerService:
    def __init__(self, repository, clock):
        self.clock = clock

    def levels(self, currency, expiry):
        SEEN_INSTANTS.append(("dealer", self.clock()))
        if expiry == "25DEC26":
            raise LookupError("25DEC26 is not in the chain snapshot")
        return dealer_levels(gate_passed=False)


class FakeOptionFlowService:
    def __init__(self, repository, clock):
        pass

    def strike_flow(self, currency, hours, end=None):
        SEEN_INSTANTS.append(("option flow", end))
        return StrikeFlowReport(currency, end - timedelta(hours=hours), end, None,
                                aggregate_by_strike([trade("P", "buy", 5, strike=63_000.0)]), end - timedelta(minutes=3))


class FakeTakerService:
    def __init__(self, repository=None, days=30, clock=None):      # the real signature of TakerFlowChartService
        self.clock = clock

    def figure_data(self, symbol):
        SEEN_INSTANTS.append(("taker", self.clock()))
        return TakerFlowFigureData(symbol, "BTC", rolling_points=[(NOW - timedelta(hours=2), 1.0)])


@pytest.fixture
def service(monkeypatch):
    SEEN_INSTANTS.clear()
    SPOT_TIME[0] = NOW - timedelta(minutes=30)
    monkeypatch.setattr(module, "ExpiryOpenInterestService", FakeOpenInterestService)
    monkeypatch.setattr(module, "MenthorqInputLoader", FakeLoader)
    monkeypatch.setattr(module, "replicate_menthorq", lambda rows, spot, captured_at, currency: {
        "settings": {"expirations": ["27NOV26"]}, "iv_times_open_interest": {"levels": {}}})
    monkeypatch.setattr(module, "select_drawn_levels", lambda levels: {"call_resistance": 70_000.0, "put_support": 60_000.0,
                                                                       "hvl": None})
    monkeypatch.setattr(module, "DealerFlowLevelsService", FakeDealerService)
    monkeypatch.setattr(module, "OptionFlowService", FakeOptionFlowService)
    monkeypatch.setattr(module, "TakerFlowChartService", FakeTakerService)
    return DecisionMakerService(FakeRepository(), clock=lambda: NOW)


def test_report_is_built_from_every_source_that_can_be_computed_and_lists_the_others(service):
    report = service.build_report("BTC")
    assert report.as_of == NOW and report.reference_price == 65_000.0 and report.currency == "BTC"
    ids = {item.id for item in report.evidence}
    assert {"menthorq.put_support", "dealer.27NOV26.open_interest.put_wall", "option_flow.screen_net"} <= ids
    assert not any(item_id.startswith("dealer.25DEC26") for item_id in ids)
    assert report.not_computed == [
        "dealer GEX 25DEC26: 25DEC26 is not in the chain snapshot",
        "futures taker ratio: BTCUSDT taker ratio: only 1 rolling points in 30 days (at least 240 needed for the band)",
        "liquidation estimate: no BTCUSDT snapshot computed in the last 24 h"]
    assert report.context["open_interest_by_expiry"][0]["expiry"] == "25DEC26"
    assert any("Not computed: liquidation estimate" in item.text for item in report.decisions)


def test_every_source_sees_the_same_instant(service):
    service.build_report("BTC")
    instants = [value for name, value in SEEN_INSTANTS if name in ("open interest", "menthorq", "dealer", "option flow", "taker")]
    assert instants and all(value == NOW for value in instants)
    assert ("block legs end", int(NOW.timestamp() * 1000)) in SEEN_INSTANTS
    liquidation_end = next(value for name, value in SEEN_INSTANTS if name == "liquidation end")
    assert NOW < liquidation_end <= NOW + timedelta(milliseconds=1)


def test_without_a_spot_there_is_no_report(service, monkeypatch):
    class NoChain(FakeOpenInterestService):
        def open_interest(self, currency):
            raise LookupError("No BTC option-chain snapshot")
    monkeypatch.setattr(module, "ExpiryOpenInterestService", NoChain)
    with pytest.raises(LookupError):
        service.build_report("BTC")


def test_a_programming_error_in_a_source_is_not_swallowed(service, monkeypatch):
    class Broken(FakeOptionFlowService):
        def strike_flow(self, currency, hours, end=None):
            raise TypeError("bug")
    monkeypatch.setattr(module, "OptionFlowService", Broken)
    with pytest.raises(TypeError):
        service.build_report("BTC")


def test_save_snapshot_stores_the_report_dict_under_its_key(service):
    report = service.build_report("BTC")
    assert service.save_snapshot(report) is True
    currency, computed_at, reference_price, stored = service.repository.saved[0]
    assert (currency, computed_at, reference_price) == ("BTC", NOW, 65_000.0) and stored == report.to_dict()


def test_summary_lines_are_readable_and_carry_the_disclaimer(service):
    lines = summary_lines(service.build_report("BTC"))
    assert lines[0] == "BTC decision report as of 2026-11-14 12:00 UTC, spot 65,000.00"
    assert lines[1].startswith("Score scale: 0-100, uncalibrated")
    assert lines[-1].startswith("Decision support only")


def test_an_old_spot_is_flagged_and_its_time_is_in_the_context(service):
    report = service.build_report("BTC")
    assert report.context["spot_time"] == (NOW - timedelta(minutes=30)).isoformat() and report.context["caveats"] == []
    SPOT_TIME[0] = NOW - timedelta(hours=5)
    report = service.build_report("BTC")
    assert report.context["caveats"][0].startswith("the index price is from 2026-11-14 07:00 UTC, 5.0 h old (limit 3 h)")
    assert any(item.text.startswith("Data caveat: the index price is from") for item in report.decisions)
