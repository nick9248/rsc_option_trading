"""Dashboard text of a block's structure: the combo name, else the legs ("custom" has no combo name, it is not unknown)."""

from types import SimpleNamespace

from coding.core.analytics.onchain_dashboard.market_section import block_structure_text


def block(combo_id=None, instruments=(), leg_count=2):
    return SimpleNamespace(combo_id=combo_id, instruments=tuple(instruments), leg_count=leg_count)


def test_a_registered_combo_shows_its_name():
    assert block_structure_text(block("BTC-STRD-26MAR27-58000", ["BTC-26MAR27-58000-C", "BTC-26MAR27-58000-P"])) == "BTC-STRD-26MAR27-58000"


def test_a_custom_block_shows_its_legs():
    text = block_structure_text(block(None, ["BTC-27NOV26-58000-C", "BTC-27NOV26-60000-C"]))
    assert text == "custom: 27NOV26-58000-C, 27NOV26-60000-C"


def test_a_single_leg_block_is_called_that():
    assert block_structure_text(block(None, ["BTC-27NOV26-58000-P"], leg_count=1)) == "single leg: 27NOV26-58000-P"


def test_many_legs_are_cut_with_a_count():
    legs = [f"BTC-27NOV26-{strike}-C" for strike in (50000, 52000, 54000, 56000, 58000, 60000)]
    assert block_structure_text(block(None, legs, leg_count=6)).endswith("and 2 more")


def test_no_legs_and_no_combo_is_not_computed():
    assert block_structure_text(block(None, [])) == "not computed"
