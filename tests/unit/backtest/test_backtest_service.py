"""BacktestService with a fake repository: entry rules, skips and no lookahead."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from coding.core.analytics.backtest.structures import PUT_CREDIT_SPREAD, SHORT_STRANGLE
from coding.service.backtest.backtest_service import BacktestService

NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)
ENTRY = datetime(2026, 6, 1, 9, tzinfo=timezone.utc)


def rows(expiration="5JUN26", forward=70000.0):
    out = []
    for strike in range(50000, 90001, 1000):                                   # a dense chain, like the real one
        for option_type in ("C", "P"):
            intrinsic = max(forward - strike, 0) if option_type == "C" else max(strike - forward, 0)
            mark = max(intrinsic / forward, 0.002) + 0.004
            out.append({"instrument_name": f"BTC-{expiration}-{strike}-{option_type}", "expiration": expiration, "strike": float(strike),
                        "option_type": option_type, "open_interest": 10.0, "mark_iv": 55.0, "underlying_price": forward,
                        "mark_price": mark, "volume": 1.0, "volume_usd": 1.0, "bid_price": mark - 0.001, "ask_price": mark + 0.001})
    return out


def make_repository(captured_at=ENTRY - timedelta(minutes=10), chain=None, index=(70000.0, None), settlement=(70500.0, 300)):
    repository = MagicMock()
    repository.get_option_chain_snapshot_at_or_before.return_value = None if chain is False else (captured_at, chain or rows())
    repository.get_index_price_at_or_before.return_value = (index[0], captured_at - timedelta(minutes=2))
    repository.get_settlement_index_price.return_value = settlement
    return repository


def run(repository, structure=SHORT_STRANGLE, **kwargs):
    return BacktestService(repository).run("BTC", structure, date(2026, 6, 1), date(2026, 6, 1), now_utc=NOW, **kwargs)


def test_one_trade_is_entered_settled_and_priced_from_the_snapshot_only():
    repository = make_repository()
    result = run(repository)

    assert len(result.trades) == 1 and result.summary.skipped == {}
    trade = result.trades[0]
    assert trade.expiration == "5JUN26" and trade.settlement_price == 70500.0 and trade.spot_at_entry == 70000.0
    assert all(leg.action == "sell" for leg in trade.legs) and len(trade.legs) == 2
    repository.get_option_chain_snapshot_at_or_before.assert_called_once_with("BTC", ENTRY)            # the entry instant, nothing later
    assert repository.get_settlement_index_price.call_args.args[1] == datetime(2026, 6, 5, 8, tzinfo=timezone.utc)


def test_the_expiry_nearest_the_target_is_chosen_and_too_close_ones_are_not():
    chain = rows("2JUN26") + rows("8JUN26") + rows("26JUN26")                  # 1.9, 7.9 and 25.9 days away
    assert run(make_repository(chain=chain), target_days_to_expiry=7.0).trades[0].expiration == "8JUN26"
    assert run(make_repository(chain=chain), target_days_to_expiry=1.0, minimum_days_to_expiry=2.0).trades[0].expiration == "8JUN26"


def test_two_expiries_equally_far_from_the_target_pick_the_earlier_one_every_time():
    captured_at = datetime(2026, 6, 1, 8, tzinfo=timezone.utc)                # 4JUN26 is exactly 3 days away, 6JUN26 exactly 5
    chain = rows("6JUN26") + rows("4JUN26")
    for _ in range(3):
        expiration, _ = BacktestService._choose_expiry(chain, captured_at, 4.0, 2.0)
        assert expiration == "4JUN26"


def test_the_trade_carries_the_settlement_time():
    assert run(make_repository()).trades[0].settlement_at == datetime(2026, 6, 5, 8, tzinfo=timezone.utc)


def test_a_snapshot_older_than_45_minutes_is_not_used():
    result = run(make_repository(captured_at=ENTRY - timedelta(minutes=50)))
    assert result.trades == () and result.summary.skipped == {"no fresh snapshot": 1}


def test_no_snapshot_no_settlement_and_unsettled_expiry_are_skipped_with_their_reason():
    assert run(make_repository(chain=False)).summary.skipped == {"no snapshot": 1}
    assert run(make_repository(settlement=None)).summary.skipped == {"no settlement price": 1}
    assert run(make_repository(settlement=(70500.0, 2))).summary.skipped == {"no settlement price": 1}       # too few trades
    early = BacktestService(make_repository()).run("BTC", SHORT_STRANGLE, date(2026, 6, 1), date(2026, 6, 1), now_utc=datetime(2026, 6, 3, tzinfo=timezone.utc))
    assert early.summary.skipped == {"expiry not settled yet": 1}


def test_an_index_price_far_older_than_the_snapshot_is_not_used():
    repository = make_repository()
    repository.get_index_price_at_or_before.return_value = (70000.0, ENTRY - timedelta(hours=3))
    assert run(repository).summary.skipped == {"no index price at the snapshot": 1}


def test_a_chain_without_iv_and_without_quotes_gives_a_reason():
    chain = [dict(row, mark_iv=None, mark_price=None) for row in rows()]
    assert list(run(make_repository(chain=chain)).summary.skipped) == ["no quoted put near the wanted delta to sell"]


def test_days_after_now_are_not_entered():
    result = BacktestService(make_repository()).run("BTC", PUT_CREDIT_SPREAD, date(2026, 6, 1), date(2026, 6, 30), now_utc=ENTRY)
    assert len(result.trades) + sum(result.summary.skipped.values()) == 1
