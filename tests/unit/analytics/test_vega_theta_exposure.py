"""Vega and theta exposure: hand-computed sums, missing greeks left out and counted, nothing to sum is None."""

from coding.core.analytics.greek_exposure.vega_theta_exposure import calculate_vega_theta_exposure


def test_the_exposure_is_the_sum_of_open_interest_times_greek_and_the_dealer_is_the_negative():
    instruments = [
        {"open_interest": 10.0, "vega": 70.0, "theta": -55.0},      # 700 and -550
        {"open_interest": 4.0, "vega": 50.0, "theta": -40.0},       # 200 and -160
    ]
    result = calculate_vega_theta_exposure(instruments)

    assert result.holder_vega_usd_per_vol_point == 900.0
    assert result.holder_theta_usd_per_day == -710.0
    assert result.dealer_vega_usd_per_vol_point == -900.0 and result.dealer_theta_usd_per_day == 710.0
    assert (result.instruments_used, result.instruments_missing) == (2, 0)


def test_an_instrument_without_greeks_is_left_out_and_counted_not_taken_as_zero_greeks():
    instruments = [{"open_interest": 10.0, "vega": 70.0, "theta": -55.0}, {"open_interest": 99.0, "vega": None, "theta": None}]
    result = calculate_vega_theta_exposure(instruments)
    assert (result.holder_vega_usd_per_vol_point, result.instruments_used, result.instruments_missing) == (700.0, 1, 1)


def test_instruments_without_open_interest_do_not_count_at_all():
    assert calculate_vega_theta_exposure([{"open_interest": 0, "vega": None, "theta": None}]) is None


def test_nothing_to_sum_is_none_not_zero():
    assert calculate_vega_theta_exposure([]) is None
    assert calculate_vega_theta_exposure([{"open_interest": 5.0, "vega": None, "theta": 1.0}]) is None
