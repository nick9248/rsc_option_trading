"""Independence-aware aggregation and the price map, with hand-calculated expectations (spot 100,000, band 1,000)."""

import pytest

from coding.core.analytics.decision import decision_config
from coding.core.analytics.decision.independence import contributions_by_group, group_contribution
from coding.core.analytics.decision.price_map import build_price_map
from tests.unit.decision.evidence_builders import SPOT, directional, level


def test_two_items_of_one_input_group_count_once():
    # Same group, both q 0.5, s 1: mean strength (0.5x1 + 0.5x1) / 1.0 = 1; mean quality (0.25 + 0.25) / 1.0 = 0.5.
    # Weight = 1 x 1 x 0.5 = 0.5, the same as ONE such item.
    same_group = build_price_map([level("a", "g1", 95_000), level("b", "g1", 95_500)], SPOT)
    assert len(same_group.levels) == 1
    assert same_group.levels[0].weight == pytest.approx(0.5)
    assert same_group.levels[0].input_groups == ["g1"]


def test_two_items_of_two_input_groups_count_twice():
    # Two groups of 0.5 each: 0.5 + 0.5 = 1.0.
    two_groups = build_price_map([level("a", "g1", 95_000), level("b", "g2", 95_500)], SPOT)
    assert two_groups.levels[0].weight == pytest.approx(1.0)
    assert two_groups.levels[0].input_groups == ["g1", "g2"]


def test_group_contribution_is_the_quality_weighted_mean_times_the_mean_quality():
    first, second = level("a", "g1", 95_000, quality=0.8), level("b", "g1", 95_000, quality=0.2, strength=0.5)
    # mean value = (0.8 x 1 + 0.2 x 0.5) / (0.8 + 0.2) = 0.9; mean quality = (0.64 + 0.04) / 1.0 = 0.68; 0.9 x 0.68 = 0.612
    assert group_contribution([(first, 1.0), (second, 0.5)]) == pytest.approx(0.612)


def test_a_group_contribution_is_capped(monkeypatch):
    monkeypatch.setitem(decision_config.GROUP_WEIGHTS, "g1", 3.0)
    # 3 x 1 x 1 = 3, capped at GROUP_CONTRIBUTION_CAP = 1
    assert group_contribution([(level("a", "g1", 95_000, quality=1.0), 1.0)]) == pytest.approx(1.0)
    assert group_contribution([(level("a", "g1", 95_000, quality=1.0), -1.0)]) == pytest.approx(-1.0)


def test_group_contribution_refuses_mixed_groups_and_handles_zero_quality():
    with pytest.raises(ValueError):
        group_contribution([(level("a", "g1", 1.0), 1.0), (level("b", "g2", 1.0), 1.0)])
    assert group_contribution([(level("a", "g1", 1.0, quality=0.0), 1.0)]) == 0.0
    assert contributions_by_group([]) == {}


def test_support_and_resistance_are_the_nearest_meaningful_bands_and_the_next_ones_are_kept():
    items = [level("s1", "g1", 97_000), level("s2", "g1", 92_000), level("weak", "g2", 99_000, quality=0.2),
             level("r1", "g1", 104_000), level("r2", "g2", 110_000), level("far", "g1", 120_000)]
    price_map = build_price_map(items, SPOT)
    # weak: 1 x 0.2 = 0.2 < MINIMUM_LEVEL_WEIGHT 0.3 -> a band, but not meaningful, so 97,000 is the support
    assert [band.price for band in price_map.levels] == [92_000, 97_000, 99_000, 104_000, 110_000]
    assert [band.meaningful for band in price_map.levels] == [True, True, False, True, True]
    assert price_map.support.price == 97_000 and price_map.next_below.price == 92_000
    assert price_map.resistance.price == 104_000 and price_map.next_above.price == 110_000
    assert price_map.left_out == ["far"]          # 20 % from the spot, beyond the 15 % window


def test_a_band_closes_one_band_width_after_its_first_price_and_its_price_is_the_weighted_mean():
    items = [level("a", "g1", 95_000, quality=0.6), level("b", "g2", 95_900, quality=0.2), level("c", "g1", 96_100)]
    price_map = build_price_map(items, SPOT)
    # 95,900 is within 1,000 of 95,000; 96,100 is not, so it opens a new band
    assert [band.evidence_ids for band in price_map.levels] == [["a", "b"], ["c"]]
    # weights strength x quality: 0.6 and 0.2 -> (95,000 x 0.6 + 95,900 x 0.2) / 0.8 = 95,225
    assert price_map.levels[0].price == pytest.approx(95_225)
    assert (price_map.levels[0].low, price_map.levels[0].high) == (95_000, 95_900)


def test_only_price_levels_shape_the_map_and_an_empty_map_has_no_levels():
    price_map = build_price_map([directional("d", "g1", "bullish")], SPOT)
    assert price_map.levels == [] and price_map.support is None and price_map.resistance is None
    with pytest.raises(ValueError):
        build_price_map([], 0.0)
