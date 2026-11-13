"""Buy/sell volume per strike: aggregation by hand, the chart, the service and the repository query."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, cast

import psycopg2
import pytest

from coding.core.analytics.option_flow.strike_flow import SERIES, OptionTrade, aggregate_by_strike, series_key
from coding.core.analytics.option_flow.strike_flow_figure import UNITS, build_strike_flow_figure, cell_value, nice_ticks
from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository
from coding.service.option_flow.option_flow_service import OptionFlowService


def trade(strike=70_000.0, kind="C", direction="buy", amount=2.0, block=False, expiry="27NOV26"):
    return OptionTrade(expiry, strike, kind, direction, amount, block)


def test_a_trade_goes_into_the_series_of_its_type_side_and_block_flag():
    flow = aggregate_by_strike([
        trade(kind="C", direction="buy"), trade(kind="C", direction="sell"), trade(kind="P", direction="buy"),
        trade(kind="P", direction="sell"), trade(kind="C", direction="buy", block=True),
        trade(kind="C", direction="sell", block=True), trade(kind="P", direction="buy", block=True),
        trade(kind="P", direction="sell", block=True)])
    assert flow.trades_used == 8 and flow.trades_skipped == 0 and flow.strikes == [70_000.0]
    assert [flow.cell(70_000.0, series.key).trades for series in SERIES] == [1] * 8


def test_contracts_and_notional_add_up_by_hand():
    # Two calls sold at 70,000: 2 BTC and 3 BTC -> 5 contracts, notional = 5 x 70,000 = 350,000 (contracts times the strike).
    flow = aggregate_by_strike([trade(direction="sell", amount=2.0), trade(direction="sell", amount=3.0),
                                trade(direction="buy", amount=1.0, kind="P")])
    cell = flow.cell(70_000.0, "call_sell")
    assert (cell.contracts, cell.notional_usd, cell.trades) == (5.0, 350_000.0, 2)
    assert flow.cell(70_000.0, "put_buy").notional_usd == 70_000.0
    empty = flow.cell(80_000.0, "call_sell")
    assert (empty.contracts, empty.notional_usd, empty.trades) == (0.0, 0.0, 0)


def test_it_reproduces_the_laevitas_tooltip_at_108000():
    # Laevitas, 24 h to 12 Nov 2026, strike 108,000: Calls Buy $140.4K and Calls Sell $10.8K. Our stored trades there are
    # calls bought for 1.0 and 0.3 BTC and calls sold for 0.1 BTC: 1.3 x 108,000 = 140,400 and 0.1 x 108,000 = 10,800.
    flow = aggregate_by_strike([trade(strike=108_000.0, amount=1.0), trade(strike=108_000.0, amount=0.3),
                                trade(strike=108_000.0, amount=0.1, direction="sell")])
    assert flow.cell(108_000.0, "call_buy").notional_usd == pytest.approx(140_400.0)
    assert flow.cell(108_000.0, "call_sell").notional_usd == pytest.approx(10_800.0)
    assert all(flow.cell(108_000.0, series.key).trades == 0 for series in SERIES
               if series.key not in ("call_buy", "call_sell"))


def test_one_expiry_can_be_selected_and_strikes_are_sorted():
    flow = aggregate_by_strike([trade(strike=80_000.0, expiry="27NOV26"), trade(strike=60_000.0, expiry="27NOV26"),
                                trade(strike=70_000.0, expiry="25DEC26")], expiry="27NOV26")
    assert flow.strikes == [60_000.0, 80_000.0] and flow.trades_used == 2


def test_trades_that_cannot_be_used_are_skipped_and_counted_never_zero_filled():
    flow = aggregate_by_strike([trade(direction="hold"), trade(kind="X"), trade(amount=0.0), trade(amount=-1.0),
                                trade(strike=0.0), trade()])
    assert flow.trades_used == 1 and flow.trades_skipped == 5


def test_option_type_and_direction_are_read_case_insensitively():
    flow = aggregate_by_strike([trade(kind="c", direction="SELL")])
    assert flow.cell(70_000.0, "call_sell").trades == 1


def figure_bars(figure):
    """name -> (x values, strikes as text) of every bar trace."""
    return {bar.name: (list(bar.x), list(bar.y)) for bar in figure.data}


def test_figure_draws_calls_left_puts_right_in_millions_with_blocked_next_to_the_axis():
    flow = aggregate_by_strike([trade(strike=70_000.0, kind="C", direction="buy", amount=10.0),
                                trade(strike=70_000.0, kind="C", direction="buy", amount=5.0, block=True),
                                trade(strike=72_000.0, kind="P", direction="sell", amount=2.0)])
    figure = build_strike_flow_figure(flow, "title")
    bars = figure_bars(figure)
    assert bars["Calls Buy"] == ([-0.7, 0.0], ["70,000", "72,000"])                  # 10 x 70,000 = $0.7M, to the left
    assert bars["Call Buy Blocked"][0] == [-0.35, 0.0]                              # 5 x 70,000 = $0.35M
    assert bars["Puts Sell"][0] == [0.0, 0.144]                                      # 2 x 72,000 = $0.144M, to the right
    names = [bar.name for bar in figure.data]
    assert names.index("Call Buy Blocked") < names.index("Calls Buy")                # blocked first = next to the axis
    assert len(names) == 8 and figure.layout.barmode == "relative" and figure.layout.hovermode == "y unified"
    assert list(figure.layout.yaxis.categoryarray) == ["70,000", "72,000"]


def test_figure_hover_values_are_positive_and_follow_the_unit():
    flow = aggregate_by_strike([trade(strike=70_000.0, kind="C", direction="sell", amount=3.0)])
    hover = {bar.name: list(bar.customdata) for bar in build_strike_flow_figure(flow, "t").data}
    assert hover["Calls Sell"] == [pytest.approx(0.21)]                           # 3 x 70,000 = $0.21M, positive though drawn left
    contracts = build_strike_flow_figure(flow, "t", unit="contracts")
    assert dict((bar.name, list(bar.x)) for bar in contracts.data)["Calls Sell"] == [-3.0]
    trades = build_strike_flow_figure(flow, "t", unit="trades")
    assert dict((bar.name, list(bar.x)) for bar in trades.data)["Calls Sell"] == [-1.0]


def test_figure_rejects_an_unknown_unit_and_handles_no_trades():
    with pytest.raises(ValueError):
        build_strike_flow_figure(aggregate_by_strike([]), "t", unit="dollars")
    empty = build_strike_flow_figure(aggregate_by_strike([]), "t")
    assert len(empty.data) == 8 and all(len(bar.x) == 0 for bar in empty.data)
    assert set(UNITS) == {"notional", "contracts", "trades"}
    assert cell_value(aggregate_by_strike([trade()]).cell(70_000.0, "call_buy"), "trades") == 1.0


def test_nice_ticks_reach_the_limit_with_round_steps():
    assert nice_ticks(0.0) == [0.0]
    ticks = nice_ticks(83.0)
    assert ticks[0] == 0.0 and ticks[-1] >= 83.0 and len(ticks) <= 8
    assert len({round(b - a, 9) for a, b in zip(ticks, ticks[1:])}) == 1


class FakeRepository:
    def __init__(self, trades: List[OptionTrade]):
        self.trades, self.calls = trades, []

    def get_option_trades_between(self, currency, start_ts, end_ts, expiration=None):
        self.calls.append((currency, start_ts, end_ts, expiration))
        return self.trades

    def get_latest_trade_time(self, currency):
        return datetime(2026, 11, 13, 21, 21, tzinfo=timezone.utc)

    def get_traded_expirations(self, currency, start_ts, end_ts):
        return ["25DEC26", "13NOV26", "bad", "27NOV26"]


NOW = datetime(2026, 11, 13, 21, 25, tzinfo=timezone.utc)


def test_the_service_reads_the_window_before_now_and_writes_png_and_json(tmp_path):
    repository = FakeRepository([trade(direction="sell", amount=2.0)])
    service = OptionFlowService(cast(Any, repository), clock=lambda: NOW)
    report = service.generate_chart(tmp_path, "BTC", 24)
    assert repository.calls == [("BTC", int((NOW - timedelta(hours=24)).timestamp() * 1000), int(NOW.timestamp() * 1000), None)]
    assert sorted(path.name for path in report.files) == ["BTC_strike_flow_24h_all.json", "BTC_strike_flow_24h_all.png"]
    import json
    numbers = json.loads((tmp_path / "BTC_strike_flow_24h_all.json").read_text(encoding="utf-8"))
    assert numbers["strikes"]["70000"]["call_sell"] == {"contracts": 2.0, "notional_usd": 140000.0, "trades": 1}   # 2 x 70,000
    assert numbers["trades_used"] == 1 and numbers["window_end_utc"] == NOW.isoformat()


def test_the_service_lists_traded_expirations_soonest_first_and_builds_a_titled_figure():
    service = OptionFlowService(FakeRepository([trade()]), clock=lambda: NOW)
    assert service.traded_expirations() == ["13NOV26", "27NOV26", "25DEC26", "bad"]      # unparseable labels go last
    report = service.strike_flow(expiry="27NOV26")
    figure = service.build_figure(report)
    assert "Last 24H 27NOV26" in figure.layout.title.text
    assert "Newest stored trade: 2026-11-13 21:21 UTC" in figure.layout.annotations[-1].text


def test_the_service_can_end_the_window_at_a_chosen_time_and_needs_a_timezone(tmp_path):
    repository = FakeRepository([trade()])
    service = OptionFlowService(cast(Any, repository), clock=lambda: NOW)
    end = datetime(2026, 11, 12, 14, 19, 51, tzinfo=timezone.utc)
    report = service.generate_chart(tmp_path, "BTC", 24, "27NOV26", end=end)
    assert repository.calls[0][2] == int(end.timestamp() * 1000) and repository.calls[0][3] == "27NOV26"
    assert report.files[0].name == "BTC_strike_flow_24h_27NOV26_to_20261112T1419.png"
    with pytest.raises(ValueError):
        service.strike_flow("BTC", 24, None, datetime(2026, 11, 12, 14, 19))


# ---- repository query on a throw-away database (never option_trading) ---------------------------------------------------

@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_option_flow_{uuid.uuid4().hex[:8]}"
    assert scratch_name != "option_trading"
    admin_connection: Dict[str, Any] = {**base_connection, "database": "postgres"}
    try:
        admin = psycopg2.connect(connect_timeout=3, **admin_connection)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {scratch_name}")
    scratch_connection: Dict[str, Any] = {**base_connection, "database": scratch_name}
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""CREATE TABLE historical_trades (
                id serial PRIMARY KEY, trade_id varchar(40), trade_timestamp bigint, currency varchar(10), expiration varchar(10),
                strike numeric, option_type char(1), direction varchar(10), amount numeric, index_price numeric,
                block_trade_id varchar(40))""")
    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=int(scratch_connection["port"]), database=scratch_name,
        user=scratch_connection["user"], password=scratch_connection["password"]))
    try:
        yield repository, scratch_connection
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def test_repository_query_returns_the_window_the_expiry_and_the_block_flag(repo):
    repository, connection_settings = repo
    rows = [("a", 1_000, "BTC", "27NOV26", 70_000, "C", "sell", 2.5, 70_500.0, None),
            ("b", 2_000, "BTC", "27NOV26", 80_000, "P", "buy", 10.0, 70_600.0, "block-1"),
            ("c", 3_000, "BTC", "25DEC26", 70_000, "C", "buy", 1.0, 70_700.0, None),
            ("d", 4_000, "ETH", "27NOV26", 3_000, "C", "buy", 1.0, 3_000.0, None),
            ("e", 9_000, "BTC", "27NOV26", 70_000, "C", "buy", 1.0, 70_800.0, None)]       # outside the window
    with psycopg2.connect(**connection_settings) as connection:
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO historical_trades (trade_id, trade_timestamp, currency, expiration, strike, option_type, "
                               "direction, amount, index_price, block_trade_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    everything = repository.get_option_trades_between("BTC", 0, 5_000)
    assert sorted((t.strike, t.option_type, t.direction, t.amount, t.is_block) for t in everything) == [
        (70_000.0, "C", "buy", 1.0, False), (70_000.0, "C", "sell", 2.5, False), (80_000.0, "P", "buy", 10.0, True)]
    only_november = repository.get_option_trades_between("BTC", 0, 5_000, "27NOV26")
    assert len(only_november) == 2 and all(t.expiration == "27NOV26" for t in only_november)
    assert sorted(repository.get_traded_expirations("BTC", 0, 5_000)) == ["25DEC26", "27NOV26"]
    assert repository.get_latest_trade_time("BTC") == datetime.fromtimestamp(9.0, tz=timezone.utc)
    assert repository.get_latest_trade_time("SOL") is None
    flow = aggregate_by_strike(everything)
    assert flow.cell(80_000.0, "put_buy_block").notional_usd == 10.0 * 80_000.0


def test_series_key_finds_the_series_of_a_kind():
    assert series_key("C", "sell", True) == "call_sell_block"
    assert series_key("P", "buy", False) == "put_buy"
    assert all(series_key(series.option_type, series.direction, series.blocked) == series.key for series in SERIES)


def test_the_laevitas_bar_length_is_the_screen_plus_the_blocked_volume():
    # Strike 70,000 puts sold, 24 h to 12 Nov 14:49 UTC (Deribit's history server): $31.66M on screen and $50.90M in blocks;
    # Laevitas draws one bar of about $83M (82.56). The same by hand: 452 BTC on screen and 727 BTC in a block at 70,000.
    flow = aggregate_by_strike([trade(kind="P", direction="sell", amount=400.0), trade(kind="P", direction="sell", amount=52.0),
                                trade(kind="P", direction="sell", amount=727.0, block=True)])
    screen = flow.cell(70_000.0, "put_sell").notional_usd
    blocked = flow.cell(70_000.0, "put_sell_block").notional_usd
    assert (screen, blocked) == (452.0 * 70_000, 727.0 * 70_000)
    assert (screen + blocked) / 1e6 == pytest.approx(82.53)
