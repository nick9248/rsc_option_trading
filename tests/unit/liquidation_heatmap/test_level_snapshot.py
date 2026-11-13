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


# Reference 10,000, bin 100, smoothing 100. A long's pool is its own side: 9,500 .. 10,000 (6 points); a short's pool is
# 10,000 .. 10,500 (6 points). A point counts the levels within +-100 of it.
LEVELS = [[9_800.0, 50.0, 0.0], [10_300.0, 0.0, 70.0]]


def test_percentile_is_high_at_the_strongest_long_level_and_low_far_from_any():
    # Long pool intensities: 9,500: 0, 9,600: 0, 9,700: 50, 9,800: 50, 9,900: 50, 10,000: 0.
    # Event at 9,800 (50): 3 points below, 3 ties (half each): (3 + 1.5) / 6 = 0.75.
    assert level_percentile(LEVELS, "long", 9_800.0, 10_000.0, 100.0) == pytest.approx(4.5 / 6)
    # Event at 9,600 (0): none below, 3 ties: 1.5 / 6 = 0.25.
    assert level_percentile(LEVELS, "long", 9_600.0, 10_000.0, 100.0) == pytest.approx(1.5 / 6)


def test_the_short_side_uses_the_short_levels_and_the_pool_above_the_price():
    # Short pool intensities: 10,000: 0, 10,100: 0, 10,200: 70, 10,300: 70, 10,400: 70, 10,500: 0.
    assert level_percentile(LEVELS, "short", 10_300.0, 10_000.0, 100.0) == pytest.approx(4.5 / 6)
    assert level_percentile(LEVELS, "short", 10_100.0, 10_000.0, 100.0) == pytest.approx(1.5 / 6)


def test_an_event_on_the_wrong_side_of_the_price_is_not_scored():
    assert level_percentile(LEVELS, "long", 10_400.0, 10_000.0, 100.0) is None      # a long above the reference
    assert level_percentile(LEVELS, "short", 9_800.0, 10_000.0, 100.0) is None      # a short below the reference


def test_percentile_is_none_for_unscorable_events():
    assert level_percentile(LEVELS, "long", 9_000.0, 10_000.0, 100.0) is None       # beyond the 5 % pool
    assert level_percentile([], "long", 10_000.0, 10_000.0, 100.0) is None
    assert level_percentile(LEVELS, "net", 10_000.0, 10_000.0, 100.0) is None
