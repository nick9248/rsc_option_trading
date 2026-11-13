"""Level snapshot extraction and the percentile score of a real liquidation (hand-checked)."""

from datetime import datetime, timezone

import numpy as np
import pytest

from coding.core.analytics.liquidation_heatmap.level_snapshot import extract_levels, level_percentile
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import HeatmapResult

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def result_with(long_btc, short_btc, centers):
    return HeatmapResult([T0], np.array(centers, dtype=float), np.array([long_btc], dtype=np.float32),
                         np.array([short_btc], dtype=np.float32))


def test_extract_levels_keeps_non_empty_bins_near_the_price_in_usd():
    result = result_with([2, 0, 0, 1], [0, 0, 3, 0], [9_000, 9_900, 10_100, 20_000])
    # reference 10,000, window 10 %: 9,000 .. 11,000 -> bins 9,000 (2 BTC long) and 10,100 (3 BTC short); 9,900 is empty;
    # 20,000 is outside.
    assert extract_levels(result, 0, 10_000.0) == [[9_000.0, 18_000.0, 0.0], [10_100.0, 0.0, 30_300.0]]


def test_extract_levels_of_an_empty_row_is_empty():
    result = result_with([np.nan, np.nan], [np.nan, np.nan], [9_000, 10_000])
    assert extract_levels(result, 0, 10_000.0) == []


# Reference 10,000; pool = every 100 USD from 9,500 to 10,500 (11 prices); smoothing 100 -> a point counts levels within +-100.
LEVELS = [[9_800.0, 50.0, 0.0], [10_300.0, 0.0, 70.0]]


def test_percentile_is_high_at_the_strongest_long_level_and_zero_far_from_any():
    # Long intensity: 50 for the pool points 9,700 / 9,800 / 9,900 (3 of 11), 0 for the other 8. The event is at 9,800
    # (intensity 50): 8 pool points have less, 3 tie (itself and its two neighbours) and count half: (8 + 1.5) / 11.
    at_level = level_percentile(LEVELS, "long", 9_800.0, 10_000.0, 100.0)
    assert at_level == pytest.approx(9.5 / 11)
    far = level_percentile(LEVELS, "long", 10_400.0, 10_000.0, 100.0)
    assert far == pytest.approx(0.5 * 8 / 11)                               # intensity 0 ties with the 8 empty points


def test_the_short_side_uses_the_short_levels_only():
    assert level_percentile(LEVELS, "short", 10_300.0, 10_000.0, 100.0) == pytest.approx(9.5 / 11)
    assert level_percentile(LEVELS, "short", 9_800.0, 10_000.0, 100.0) == pytest.approx(0.5 * 8 / 11)


def test_percentile_is_none_for_unscorable_events():
    assert level_percentile(LEVELS, "long", 11_000.0, 10_000.0, 100.0) is None      # outside the 5 % pool
    assert level_percentile([], "long", 10_000.0, 10_000.0, 100.0) is None
    assert level_percentile(LEVELS, "net", 10_000.0, 10_000.0, 100.0) is None
