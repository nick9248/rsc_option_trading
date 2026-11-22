"""Facts of an expiry as of an instant: stored rows only, freshness, empty zeros as missing, rendering."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.strategy_pipeline.facts import render_facts
from coding.core.analytics.realised_volatility import realised_volatility
from coding.service.strategy_pipeline.facts_service import FactsAsOfService

AS_OF = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
EXPIRY = "30OCT26"


def analysis_row(hour=AS_OF - timedelta(hours=1), **overrides):
    return {"snapshot_hour": hour, "created_at": hour + timedelta(minutes=22), "max_pain_strike": 78000.0, "put_call_ratio_oi": 0.51,
            "put_call_ratio_volume": 2.39, "total_call_oi": 98828.0, "total_put_oi": 49927.0, "total_net_gex": 27541076.0, "total_net_dex": 8462.0,
            "call_resistance_strike": 90000.0, "put_support_strike": 78000.0, "hvl_level": 90000.0, "itm_call_oi_pct": 13.8, "itm_put_oi_pct": 12.0,
            **overrides}


def volatility_row(hour=AS_OF - timedelta(hours=1), **overrides):
    return {"snapshot_hour": hour, "atm_iv": 34.96, "skew_25d": None, "put_25d_iv": None, "call_25d_iv": 31.95, "vrp_absolute": 0.0455,
            "iv_percentile_365d": 24.0, "expected_daily_move": 1613.0, "expected_weekly_move": 4274.0, "pc_atm_ratio": 0.0,
            "pc_near_otm_ratio": 2.93, "pc_far_otm_ratio": None, "net_vanna": -848.0, "net_charm": 13968.0, **overrides}


def daily_closes(count=45):
    closes, price = [], 80000.0
    for index in range(count):
        price *= 1.01 if index % 2 == 0 else 0.99
        closes.append({"date": datetime(2026, 10, 20) - timedelta(days=count - index), "close": price})
    return closes


def make_service(analysis=None, volatility=None, flow=None, dvol=None):
    repository = MagicMock()
    repository.get_onchain_analysis_snapshot_at_or_before.return_value = analysis_row() if analysis is None else analysis or None
    repository.get_onchain_volatility_snapshot_at_or_before.return_value = volatility_row() if volatility is None else volatility or None
    repository.get_flow_delta_window.return_value = flow or {"hours_present": 24, "newest_hour": AS_OF - timedelta(hours=1), "hiro_usd": -33770066.0,
                                                             "premium_usd": -291084.0, "gross_delta_usd": 116458260.0, "trade_count": 1432}
    repository.get_dvol_history_before.return_value = [30.0, 35.0, 40.0, 38.0] if dvol is None else dvol
    repository.get_latest_dvol_candle_start_before.return_value = AS_OF - timedelta(hours=1)
    repository.get_ohlcv_by_date_range.return_value = daily_closes()
    return FactsAsOfService(repository), repository


def build(service):
    return service.build("BTC", EXPIRY, AS_OF, 81357.8, 81524.59)


def test_the_facts_carry_the_stored_numbers():
    service, _ = make_service()
    facts = build(service)
    assert facts.max_pain_strike == 78000.0 and facts.total_call_oi == 98828.0 and facts.atm_iv_pct == 34.96
    assert facts.dvol == 38.0 and facts.dvol_percentile_365d == pytest.approx(75.0)                 # 38 is at or above 3 of 4 closes
    assert facts.flow_hours == 24 and facts.flow_hiro_usd == -33770066.0 and facts.days_to_expiry == pytest.approx(9.0 + 23.0 / 24.0)         # 20 Oct 09:00 to 30 Oct 08:00 UTC
    assert facts.realised_vol_30d is not None and facts.realised_vol_10d is not None


def test_nothing_newer_than_as_of_is_asked_for():
    service, repository = make_service()
    build(service)
    assert repository.get_onchain_analysis_snapshot_at_or_before.call_args.args == ("BTC", EXPIRY, AS_OF)
    assert repository.get_onchain_volatility_snapshot_at_or_before.call_args.args == ("BTC", EXPIRY, AS_OF)
    assert repository.get_flow_delta_window.call_args.args == ("BTC", EXPIRY, AS_OF)
    assert repository.get_dvol_history_before.call_args.args[:2] == ("BTC", AS_OF)
    end = repository.get_ohlcv_by_date_range.call_args.args[2]
    assert end < datetime(2026, 10, 20, 0, 0)                            # only candles of days that had finished


def test_a_stored_row_older_than_three_hours_is_treated_as_missing():
    service, _ = make_service(analysis=analysis_row(hour=AS_OF - timedelta(hours=5)), volatility=volatility_row(hour=AS_OF - timedelta(hours=4)))
    facts = build(service)
    assert facts.analysis_hour is None and facts.max_pain_strike is None and facts.volatility_hour is None and facts.atm_iv_pct is None


def test_a_missing_row_gives_none_not_zero():
    service, _ = make_service(analysis=False, volatility=False)
    facts = build(service)
    assert facts.max_pain_strike is None and facts.atm_iv_pct is None and facts.put_call_ratio_oi is None


def test_an_empty_bucket_stored_as_zero_is_not_a_measured_zero():
    service, _ = make_service(analysis=analysis_row(total_net_gex=0.0))
    facts = build(service)
    assert facts.pc_atm_ratio is None and facts.pc_near_otm_ratio == 2.93 and facts.total_net_gex_usd is None


def test_the_stored_variance_risk_premium_is_shown_in_vol_points():
    service, _ = make_service()
    assert build(service).vrp_absolute_points == pytest.approx(4.55)


def test_other_zero_filled_columns_are_not_recorded_either():
    service, _ = make_service(analysis=analysis_row(total_call_oi=0.0, total_put_oi=0.0, put_call_ratio_oi=0.0, total_net_dex=0.0, total_volume=0.0,
                                                    put_call_ratio_volume=0.0, max_pain_strike=51000.0, itm_call_oi_pct=0.0),
                              volatility=volatility_row(net_vanna=0.0, expected_daily_move=0.0))
    facts = build(service)
    assert facts.put_call_ratio_oi is None and facts.put_call_ratio_volume is None and facts.max_pain_strike is None
    assert facts.itm_call_oi_pct is None and facts.total_net_dex is None and facts.net_vanna is None and facts.expected_daily_move_usd is None


def test_a_stale_dvol_and_a_stale_flow_are_not_shown_as_current():
    service, repository = make_service()
    repository.get_latest_dvol_candle_start_before.return_value = AS_OF - timedelta(days=5)
    repository.get_flow_delta_window.return_value = {"hours_present": 24, "newest_hour": AS_OF - timedelta(hours=14), "hiro_usd": 5.0,
                                                     "premium_usd": 1.0, "gross_delta_usd": 1.0, "trade_count": 3}
    facts = build(service)
    assert facts.dvol is None and facts.dvol_percentile_365d is None and facts.flow_hours == 0 and facts.flow_hiro_usd is None


def test_the_utc_day_is_used_for_candles_whatever_the_zone_of_the_instant():
    service, repository = make_service()
    plus_three = timezone(timedelta(hours=3))
    service.build("BTC", EXPIRY, datetime(2026, 10, 20, 1, 0, tzinfo=plus_three), 1.0)          # 19 Oct 22:00 UTC
    assert repository.get_ohlcv_by_date_range.call_args.args[2] < datetime(2026, 10, 19, 0, 0)


def test_a_naive_instant_and_a_closed_expiry_are_refused():
    service, _ = make_service()
    with pytest.raises(ValueError):
        service.build("BTC", EXPIRY, datetime(2026, 10, 20, 9), 1.0)
    with pytest.raises(ValueError):
        service.build("BTC", "19OCT26", AS_OF, 1.0)
    with pytest.raises(ValueError):
        service.build("BTC", "bad", AS_OF, 1.0)


def test_the_text_says_what_was_not_recorded_and_shows_what_was():
    service, _ = make_service(volatility=False, flow={"hours_present": 3, "newest_hour": AS_OF - timedelta(hours=1), "hiro_usd": 1.0, "premium_usd": 1.0, "gross_delta_usd": 1.0, "trade_count": 5})
    text = render_facts(build(service))
    assert "expiry 30OCT26, facts as of 2026-10-20 09:00 UTC" in text and "Max pain strike: 78,000" in text
    assert "ATM implied volatility: not recorded" in text and "not available" in text
    assert "Option flow of the last 24 hours" in text and "not recorded (3 of the last 24 hourly rows exist)" in text
    assert "convention, not measured" in text


def test_realised_volatility_ignores_gaps_in_the_candles():
    closes = daily_closes()
    assert realised_volatility(closes, 30) == pytest.approx(0.01 * (365 ** 0.5), rel=0.05)         # alternating +1% and -1%: a daily standard deviation of about 1%
    gapped = [candle for index, candle in enumerate(closes) if index % 2 == 0]
    assert realised_volatility(gapped, 30) is None


def test_a_candle_is_used_only_when_its_day_is_over_and_settled():
    from coding.core.analytics.realised_volatility import last_completed_candle_start
    # the candle starting 2026-10-19 08:00 UTC finishes at 2026-10-20 08:00; the collector's last refresh is certainly in by 09:00
    assert last_completed_candle_start(datetime(2026, 10, 20, 9, tzinfo=timezone.utc)) == datetime(2026, 10, 19, 8)
    assert last_completed_candle_start(datetime(2026, 10, 20, 8, 59, tzinfo=timezone.utc)) == datetime(2026, 10, 19, 7, 59)       # a minute earlier: the candle of 18 Oct is the newest
    assert last_completed_candle_start(datetime(2026, 10, 20, 10, tzinfo=timezone(timedelta(hours=1)))) == datetime(2026, 10, 19, 8)
    with pytest.raises(ValueError):
        last_completed_candle_start(datetime(2026, 10, 20, 8))


def test_the_facts_read_candles_up_to_the_last_complete_one_and_not_the_running_candle():
    service, repository = make_service()
    build(service)
    start, end = repository.get_ohlcv_by_date_range.call_args.args[1:3]
    assert end == AS_OF.replace(tzinfo=None) - timedelta(days=1, hours=1) and start < end


def test_daily_returns_never_join_the_two_sides_of_a_missing_candle():
    service, repository = make_service()
    candles = daily_closes(100)
    del candles[60]                                           # one missing day
    repository.get_ohlcv_by_date_range.return_value = candles
    returns = service.daily_log_returns("BTC", AS_OF)
    assert len(returns) == 38                                  # only the 39 candles after the gap form an unbroken run: 38 returns
