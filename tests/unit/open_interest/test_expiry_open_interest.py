"""Tests of the open interest per expiry calculator (pure)."""

from datetime import datetime, timezone

import pytest

from coding.core.analytics.open_interest.expiry_open_interest import open_interest_by_expiry

AS_OF = datetime(2026, 11, 14, 11, 48, tzinfo=timezone.utc)
INDEX_PRICE = 67_000.0


def row(expiration, option_type, open_interest, strike=70_000.0):
    return {"expiration": expiration, "strike": strike, "option_type": option_type, "open_interest": open_interest}


def by_expiry(rows):
    return {item.expiry: item for item in rows}


def sample_chain():
    return [row("25DEC26", "C", 100_000.0), row("25DEC26", "C", 20_000.0, 80_000.0), row("25DEC26", "P", 23_633.0),
            row("27NOV26", "C", 50_000.0), row("27NOV26", "P", 37_813.0),
            row("20NOV26", "C", 10_000.0), row("20NOV26", "P", 5_000.0)]


def test_sums_calls_puts_and_total_per_expiry():
    december = by_expiry(open_interest_by_expiry(sample_chain(), INDEX_PRICE, AS_OF))["25DEC26"]
    assert december.call_open_interest == 120_000.0
    assert december.put_open_interest == 23_633.0
    assert december.total_open_interest == 143_633.0
    assert december.rows_without_open_interest == 0


def test_notional_is_total_times_index_price():
    december = by_expiry(open_interest_by_expiry(sample_chain(), INDEX_PRICE, AS_OF))["25DEC26"]
    assert december.notional_usd == pytest.approx(143_633.0 * 67_000.0)


def test_shares_are_of_the_summed_total_and_add_to_one():
    result = open_interest_by_expiry(sample_chain(), INDEX_PRICE, AS_OF)
    grand_total = 143_633.0 + 87_813.0 + 15_000.0
    assert by_expiry(result)["25DEC26"].share == pytest.approx(143_633.0 / grand_total)
    assert sum(item.share for item in result) == pytest.approx(1.0)


def test_sorted_by_total_open_interest_descending():
    result = open_interest_by_expiry(sample_chain(), INDEX_PRICE, AS_OF)
    assert [item.expiry for item in result] == ["25DEC26", "27NOV26", "20NOV26"]


def test_days_left_counts_to_the_0800_utc_settlement():
    december = by_expiry(open_interest_by_expiry(sample_chain(), INDEX_PRICE, AS_OF))["25DEC26"]
    assert december.settlement == datetime(2026, 12, 25, 8, 0, tzinfo=timezone.utc)
    assert december.days_left == pytest.approx(40.84166667, abs=1e-6)       # 41 days minus 3 h 48 min


def test_expired_expiries_are_excluded_but_one_settling_later_today_stays():
    chain = sample_chain() + [row("14NOV26", "C", 1_000.0), row("13NOV26", "C", 9_999.0)]
    assert "14NOV26" in by_expiry(open_interest_by_expiry(chain, INDEX_PRICE, datetime(2026, 11, 14, 7, 59, tzinfo=timezone.utc)))
    result = by_expiry(open_interest_by_expiry(chain, INDEX_PRICE, AS_OF))
    assert "14NOV26" not in result and "13NOV26" not in result
    assert sum(item.share for item in result.values()) == pytest.approx(1.0)


def test_a_side_without_any_open_interest_is_none_not_zero():
    result = open_interest_by_expiry([row("25DEC26", "C", 5.0), row("25DEC26", "P", None), row("25DEC26", "P", None)],
                                     INDEX_PRICE, AS_OF)[0]
    assert result.put_open_interest is None
    assert result.call_open_interest == 5.0
    assert result.total_open_interest == 5.0               # the known side alone
    assert result.rows_without_open_interest == 2


def test_both_sides_missing_gives_no_total_notional_or_share_and_the_expiry_sorts_last():
    chain = [row("25DEC26", "C", None), row("25DEC26", "P", None), row("27NOV26", "C", 1.0)]
    result = open_interest_by_expiry(chain, INDEX_PRICE, AS_OF)
    assert [item.expiry for item in result] == ["27NOV26", "25DEC26"]
    missing = result[1]
    assert (missing.call_open_interest, missing.put_open_interest, missing.total_open_interest) == (None, None, None)
    assert missing.notional_usd is None and missing.share is None
    assert result[0].share == 1.0


def test_a_missing_value_in_a_partly_known_side_is_not_counted_as_zero():
    result = open_interest_by_expiry([row("25DEC26", "C", 4.0), row("25DEC26", "C", None, 80_000.0)], INDEX_PRICE, AS_OF)[0]
    assert result.call_open_interest == 4.0 and result.rows_without_open_interest == 1


@pytest.mark.parametrize("index_price", [None, 0.0, -5.0])
def test_no_valid_index_price_gives_no_notional(index_price):
    result = open_interest_by_expiry(sample_chain(), index_price, AS_OF)
    assert all(item.notional_usd is None for item in result)
    assert all(item.total_open_interest is not None for item in result)


def test_a_label_that_does_not_parse_is_kept_with_no_settlement_or_days_left():
    result = open_interest_by_expiry([row("PERPETUAL", "C", 3.0)], INDEX_PRICE, AS_OF)[0]
    assert result.settlement is None and result.days_left is None and result.total_open_interest == 3.0


def test_empty_chain_gives_no_rows():
    assert open_interest_by_expiry([], INDEX_PRICE, AS_OF) == []


def test_a_naive_instant_is_refused():
    with pytest.raises(ValueError):
        open_interest_by_expiry(sample_chain(), INDEX_PRICE, datetime(2026, 11, 14, 11, 48))
