"""Liquidation heatmap model: hand-calculated cases (tiny synthetic data, no network)."""

from datetime import datetime, timezone
from typing import Any, cast

import numpy as np
import pytest

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import (
    STEP,
    HeatmapSettings,
    PriceCandle,
    simulate_heatmap,
)

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
# One tier (10x), no maintenance margin, 100 USD bins: liquidation levels of an entry at 10,000 are
# long 9,000 and short 11,000, so every number can be checked by hand.
SIMPLE = HeatmapSettings(bin_size_usd=100.0, leverage_tiers=(10,), leverage_weights=(1.0,),
                         maintenance_margin_rate=0.0)


def candle(step: int, low: float, high: float, close: float) -> PriceCandle:
    return PriceCandle(T0 + STEP * step, high=high, low=low, close=close)


def open_interest(*values) -> dict:
    """OI sample i is stamped one step after candle i opened (the candle's close)."""
    return {T0 + STEP * (index + 1): value for index, value in enumerate(values) if value is not None}


def level_btc(result, side: str, step: int, price: float) -> float:
    state = result.long_btc if side == "long" else result.short_btc
    half = (result.bin_centers[1] - result.bin_centers[0]) / 2
    column = int(np.where((result.bin_centers - half <= price) & (price < result.bin_centers + half))[0][0])
    return float(state[step, column])


def test_settings_reject_inconsistent_input():
    for bad in (dict(bin_size_usd=0), dict(leverage_tiers=(10,), leverage_weights=(0.5,)),
                dict(leverage_tiers=(1,), leverage_weights=(1.0,)),
                dict(leverage_tiers=(10,), leverage_weights=(1.0,), maintenance_margin_rate=0.2)):
        with pytest.raises(ValueError):
            HeatmapSettings(**cast(Any, bad))


def test_liquidation_prices_by_hand():
    settings = HeatmapSettings(leverage_tiers=(25,), leverage_weights=(1.0,), maintenance_margin_rate=0.004)
    assert settings.long_liquidation_price(70_000, 25) == pytest.approx(70_000 * (1 - 0.04 + 0.004))   # 67,480
    assert settings.short_liquidation_price(70_000, 25) == pytest.approx(70_000 * (1 + 0.04 - 0.004))  # 72,520


def test_an_oi_rise_creates_equal_long_and_short_contracts_at_the_closing_price():
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 110.0), SIMPLE, T0)
    assert level_btc(result, "long", 1, 9_000) == pytest.approx(10.0)       # 10 BTC, long liquidated at 9,000
    assert level_btc(result, "short", 1, 11_000) == pytest.approx(10.0)     # 10 BTC, short liquidated at 11,000
    assert result.long_btc[1].sum() == pytest.approx(10.0) and result.short_btc[1].sum() == pytest.approx(10.0)
    assert level_btc(result, "long", 0, 9_000) == 0.0                       # nothing before the first change


def test_a_touched_long_level_disappears_and_closes_the_opposite_side():
    # Step 1 creates 10 BTC per side. Step 2 reaches 9,000 on the way down: the long level is liquidated,
    # and those 10 contracts also close their short side, so the short level (11,000) is emptied too.
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000), candle(2, 8_990, 10_000, 9_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 110.0, 100.0), SIMPLE, T0)
    assert level_btc(result, "long", 2, 9_000) == 0.0
    assert level_btc(result, "short", 2, 11_000) == pytest.approx(0.0)
    # OI fell from 110 to 100 = the 10 liquidated contracts, so nothing else changes (no double count).
    assert result.long_btc[2].sum() == pytest.approx(0.0) and result.short_btc[2].sum() == pytest.approx(0.0)


def test_a_voluntary_oi_fall_shrinks_both_sides_proportionally():
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000), candle(2, 9_990, 10_010, 10_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 110.0, 99.0), SIMPLE, T0)
    factor = 99.0 / 110.0                                                    # 0.9
    assert level_btc(result, "long", 2, 9_000) == pytest.approx(10.0 * factor)
    assert level_btc(result, "short", 2, 11_000) == pytest.approx(10.0 * factor)


def test_liquidated_amount_is_not_counted_again_as_an_oi_fall():
    # 10 BTC liquidated (long side) while OI falls by 15: 10 is explained, the other 5 closes voluntarily.
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000), candle(2, 8_990, 10_000, 9_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 110.0, 95.0), SIMPLE, T0)
    # After liquidation: short side 10 -> 0, tracked OI 110 - 10 = 100; residual 95 - 100 = -5; nothing left to shrink.
    assert result.short_btc[2].sum() == pytest.approx(0.0)


def test_leverage_tiers_split_the_new_contracts_by_weight():
    settings = HeatmapSettings(bin_size_usd=100.0, leverage_tiers=(10, 20), leverage_weights=(0.75, 0.25),
                               maintenance_margin_rate=0.0)
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 108.0), settings, T0)
    assert level_btc(result, "long", 1, 9_000) == pytest.approx(6.0)        # 10x: 8 * 0.75, long at 9,000
    assert level_btc(result, "long", 1, 9_500) == pytest.approx(2.0)        # 20x: 8 * 0.25, long at 9,500
    assert level_btc(result, "short", 1, 11_000) == pytest.approx(6.0)
    assert level_btc(result, "short", 1, 10_500) == pytest.approx(2.0)


def test_missing_data_is_not_filled():
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000), candle(3, 9_990, 10_010, 10_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 110.0, None, 120.0), SIMPLE, T0)
    assert np.isnan(result.long_btc[2]).all()                                # step 2 has no candle: empty row
    assert any("no candle" in note for note in result.notes)
    assert any("no open-interest sample" in note for note in result.notes) is False   # the sample after the gap exists
    # The sample for step 2 is missing, so the rise 110 -> 120 is applied at step 3 (all 10 BTC at its price).
    assert level_btc(result, "long", 3, 9_000) == pytest.approx(10.0 + 10.0)


def test_warm_up_steps_are_built_but_not_recorded():
    candles = [candle(0, 9_990, 10_010, 10_000), candle(1, 9_990, 10_010, 10_000), candle(2, 9_990, 10_010, 10_000)]
    result = simulate_heatmap(candles, open_interest(100.0, 110.0, 110.0), SIMPLE, T0 + STEP * 2)
    assert result.times == [T0 + STEP * 2]
    assert level_btc(result, "long", 0, 9_000) == pytest.approx(10.0)       # created during the warm-up


def test_candles_must_be_in_order_and_not_empty():
    with pytest.raises(ValueError):
        simulate_heatmap([], {}, SIMPLE, T0)
    with pytest.raises(ValueError):
        simulate_heatmap([candle(1, 1, 2, 1), candle(0, 1, 2, 1)], {}, SIMPLE, T0)
