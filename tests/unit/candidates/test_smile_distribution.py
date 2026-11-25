"""The market-smile distribution: it prices what the market prices, keeps the forward, scales the width, and refuses a thin or bad chain."""

import math

import numpy as np
import pytest

from coding.core.analytics.backtest.structures import Quote
from coding.core.analytics.candidates.smile_distribution import (
    MINIMUM_SMILE_QUOTES, black_price, fit_market_smile, implied_volatility)

FORWARD, YEARS = 60_000.0, 14.0 / 365.0
SVI = (0.006, 0.05, -0.35, 0.0, 0.12)          # a skewed crypto-like smile in total variance for 14 days


def smile_volatility(strike):
    a, b, rho, m, sigma = SVI
    k = math.log(strike / FORWARD)
    return math.sqrt((a + b * (rho * (k - m) + math.sqrt((k - m) ** 2 + sigma ** 2))) / YEARS)


def chain(volatility=smile_volatility, strikes=range(46_000, 78_000, 2_000), half_spread=5.0):
    quotes = []
    for strike in strikes:
        for kind in ("call", "put"):
            value = black_price(FORWARD, strike, volatility(strike), YEARS, kind)
            bid = value - half_spread
            quotes.append(Quote(f"{kind}-{strike}", float(strike), kind, bid if bid > 0 else None, value + half_spread, 0.5))
    return quotes


def expected(distribution, strike, kind):
    prices, weights = distribution.grid()
    pay = np.maximum(prices - strike, 0) if kind == "call" else np.maximum(strike - prices, 0)
    return float((pay * weights).sum())


def test_black_price_and_implied_volatility_are_inverse_and_hand_checked_at_the_money():
    price = black_price(FORWARD, FORWARD, 0.5, YEARS, "call")
    assert price == pytest.approx(FORWARD * 0.5 * math.sqrt(YEARS) / math.sqrt(2 * math.pi), rel=0.001)      # the at-the-money approximation
    assert implied_volatility(price, FORWARD, FORWARD, YEARS, "call") == pytest.approx(0.5, abs=1e-6)
    assert implied_volatility(0.0, FORWARD, FORWARD, YEARS, "call") is None
    assert implied_volatility(FORWARD * 2, FORWARD, FORWARD, YEARS, "call") is None


def test_the_fit_recovers_a_skewed_smile_and_prices_every_option_as_the_market_does():
    smile = fit_market_smile(chain(), FORWARD, YEARS)
    assert smile is not None and smile.median_error < 0.003
    for strike in (50_000, 56_000, 60_000, 66_000):
        assert smile.volatility(strike) == pytest.approx(smile_volatility(strike), abs=0.006)
    market = smile.distribution()
    prices, weights = market.grid()
    assert weights.sum() == pytest.approx(1.0) and weights.min() >= 0 and float((prices * weights).sum()) == pytest.approx(FORWARD, rel=1e-9)
    assert np.all(np.diff(prices) > 0)
    for strike, kind in ((52_000, "put"), (58_000, "put"), (62_000, "put"), (60_000, "call"), (68_000, "call")):
        assert expected(market, strike, kind) == pytest.approx(black_price(FORWARD, strike, smile.volatility(strike), YEARS, kind), abs=6.0)


def test_at_the_markets_own_width_a_skewed_spread_has_no_gain_that_a_one_volatility_model_would_invent():
    """The artefact: selling the dearer low strike and buying the cheaper high one looks profitable at ONE volatility."""
    smile = fit_market_smile(chain(), FORWARD, YEARS)
    buy, sell = 62_000, 54_000
    market_value = black_price(FORWARD, buy, smile_volatility(buy), YEARS, "put") - black_price(FORWARD, sell, smile_volatility(sell), YEARS, "put")
    atm = smile_volatility(FORWARD)
    one_volatility = black_price(FORWARD, buy, atm, YEARS, "put") - black_price(FORWARD, sell, atm, YEARS, "put")
    assert one_volatility - market_value > 80.0                                               # the invented gain
    distribution = smile.distribution()
    assert expected(distribution, buy, "put") - expected(distribution, sell, "put") == pytest.approx(market_value, abs=10.0)


def test_a_wider_view_scales_the_spread_of_log_returns_and_keeps_the_forward():
    smile = fit_market_smile(chain(), FORWARD, YEARS)
    atm = smile.at_the_money_volatility

    def spread(distribution):
        prices, weights = distribution.grid()
        returns = np.log(prices / FORWARD)
        mean = (returns * weights).sum()
        return math.sqrt(((returns - mean) ** 2 * weights).sum())
    market, wider, narrower = smile.distribution(), smile.distribution(atm * 1.5), smile.distribution(atm * 0.5)
    assert spread(wider) == pytest.approx(1.5 * spread(market), rel=1e-9) and spread(narrower) == pytest.approx(0.5 * spread(market), rel=1e-9)
    for distribution in (market, wider, narrower, smile.distribution(atm)):
        prices, weights = distribution.grid()
        assert float((prices * weights).sum()) == pytest.approx(FORWARD, rel=1e-9)
    assert np.allclose(smile.distribution(atm).grid()[0], market.grid()[0])
    assert expected(wider, 60_000, "call") > expected(market, 60_000, "call") > expected(narrower, 60_000, "call")


def test_too_few_quotes_one_sided_quotes_or_a_curve_the_fit_cannot_follow_give_no_smile():
    three_strikes = range(58_000, 64_000, 2_000)                                             # three out-of-the-money options only
    assert len(three_strikes) < MINIMUM_SMILE_QUOTES and fit_market_smile(chain(strikes=three_strikes), FORWARD, YEARS) is None
    assert fit_market_smile(chain(strikes=range(62_000, 80_000, 2_000)), FORWARD, YEARS) is None          # no puts below the forward
    zigzag = lambda strike: 0.3 if (strike // 2_000) % 2 else 0.9
    assert fit_market_smile(chain(volatility=zigzag), FORWARD, YEARS) is None
    with pytest.raises(ValueError):
        fit_market_smile(chain(), 0.0, YEARS)
    with pytest.raises(ValueError):
        fit_market_smile(chain(), FORWARD, YEARS).distribution(0.0)
