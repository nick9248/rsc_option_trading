"""Dashboard text of a block's structure: the combo name, else the legs ("custom" has no combo name, it is not unknown)."""

from coding.core.analytics.onchain_dashboard.market_section import block_structure_text
from coding.core.analytics.results.market_wide_results import Block


def block(combo_id=None, instruments=(), leg_count=2, observed_leg_count=None):
    return Block(block_trade_id="BLOCK-1", leg_count=leg_count,
                 observed_leg_count=len(instruments) if observed_leg_count is None else observed_leg_count, combo_id=combo_id,
                 combined_premium_usd=1000.0, total_amount=1.0, instruments=tuple(instruments), timestamp=None)


def test_a_registered_combo_shows_its_name():
    assert block_structure_text(block("BTC-STRD-26MAR27-58000", ["BTC-26MAR27-58000-C", "BTC-26MAR27-58000-P"])) == "BTC-STRD-26MAR27-58000"


def test_a_custom_block_shows_its_legs():
    text = block_structure_text(block(None, ["BTC-27NOV26-58000-C", "BTC-27NOV26-60000-C"]))
    assert text == "custom: 27NOV26-58000-C, 27NOV26-60000-C"


def test_a_leg_filled_in_several_prints_is_shown_once():
    legs = ["BTC-27NOV26-58000-C"] * 3 + ["BTC-27NOV26-60000-C"]
    assert block_structure_text(block(None, legs, leg_count=2, observed_leg_count=2)) == "custom: 27NOV26-58000-C, 27NOV26-60000-C"


def test_a_single_leg_block_is_called_that():
    assert block_structure_text(block(None, ["BTC-27NOV26-58000-P"], leg_count=1)) == "single leg: 27NOV26-58000-P"


def test_a_block_cut_by_the_window_says_legs_seen_not_custom():
    assert block_structure_text(block(None, ["BTC-27NOV26-58000-C"], leg_count=3, observed_leg_count=1)) == "legs seen: 27NOV26-58000-C"


def test_a_non_option_leg_keeps_its_full_name_and_empty_names_are_dropped():
    assert block_structure_text(block(None, ["BTC-PERPETUAL", "", "BTC-27NOV26-58000-C"], leg_count=2, observed_leg_count=2)) == "custom: BTC-PERPETUAL, 27NOV26-58000-C"


def test_many_legs_are_cut_with_a_count():
    legs = [f"BTC-27NOV26-{strike}-C" for strike in (50000, 52000, 54000, 56000, 58000, 60000)]
    assert block_structure_text(block(None, legs, leg_count=6)).endswith("and 2 more")


def test_no_legs_and_no_combo_is_not_computed():
    assert block_structure_text(block(None, [], leg_count=1, observed_leg_count=1)) == "not computed"
