"""Tests of the bar-expiry and block-structure fragments and of the page script that makes the jump buttons work."""

from datetime import datetime, timezone

from coding.core.analytics.option_flow.block_structure import (
    BarExpiryShare, BiggestLeg, BlockStructure, ExpiryShare)
from coding.core.analytics.report.report_page import ReportSection, build_report_page
from coding.core.analytics.report.section_fragments import bar_expiry_table, block_structure_table

TIME = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)


def make_bar(expiries, unexplained=0.0, total=1_000_000.0):
    return BarExpiryShare(120000.0, "C", "buy", total, total * 0.25, 0.25,
                          [ExpiryShare(name, total * share, share) for name, share in expiries], unexplained)


def make_block(**changes):
    values = dict(block_trade_id="BLOCK-306229", legs_in_window=5, declared_leg_count=7, expiries=["25DEC26", "26MAR27"],
                  legs_bought=3, legs_sold=2, calls=4, puts=1, notional_usd=400_000_000.0,
                  biggest_legs=[BiggestLeg("25DEC26", 120000.0, "C", "buy", 2721.7, 326_604_000.0)],
                  label="multi-leg structure over 2 expiries: the direction of one leg is not a view on its own "
                        "(only 5 of 7 legs in the window)", first_time=TIME)
    values.update(changes)
    return BlockStructure(**values)


def test_bar_row_shows_strike_type_side_notional_blocked_and_the_expiry_with_days():
    html_text = bar_expiry_table([make_bar([("25DEC26", 0.99), ("26MAR27", 0.01)])], {"25DEC26": 41.2, "26MAR27": 132.0}, {"25DEC26"})
    assert "<th scope=\"row\" class=\"num\">120,000</th>" in html_text
    assert "<td>Call</td><td>bought</td>" in html_text
    assert "1.0M USD" in html_text and ">25%</td>" in html_text
    assert "99% (41 days)" in html_text
    assert "26MAR27 1% (132 days)" in html_text


def test_jump_buttons_exist_only_for_precomputed_expiries():
    html_text = bar_expiry_table([make_bar([("25DEC26", 0.6), ("26MAR27", 0.4)])], {}, {"25DEC26"})
    assert ('<button type="button" class="jump" data-switcher-target="option-flow" data-control="expiry" '
            'data-value="25DEC26">25DEC26</button>') in html_text
    assert 'data-value="26MAR27"' not in html_text and "26MAR27 40%" in html_text


def test_only_three_expiries_are_named_and_the_rest_is_others():
    bar = make_bar([("A1", 0.4), ("B1", 0.3), ("C1", 0.2), ("D1", 0.06), ("E1", 0.04)])
    html_text = bar_expiry_table([bar], {}, set())
    assert "D1" not in html_text and "E1" not in html_text and "others 10%" in html_text


def test_unexplained_notional_is_stated_and_a_complete_bar_says_nothing():
    assert "not explained: 250,000 USD" in bar_expiry_table([make_bar([("25DEC26", 0.75)], unexplained=250_000.0)], {}, set())
    assert "not explained" not in bar_expiry_table([make_bar([("25DEC26", 1.0)], unexplained=0.0)], {}, set())


def test_missing_days_are_not_computed_never_zero():
    html_text = bar_expiry_table([make_bar([("25DEC26", 1.0)])], {"25DEC26": None}, set())
    assert "days left not computed" in html_text and "(0 days)" not in html_text


def test_bar_fragment_escapes_everything():
    attack = "<script>alert(1)</script>"
    html_text = bar_expiry_table([make_bar([(attack, 1.0)])], {}, {attack})
    assert "<script>" not in html_text and "&lt;script&gt;" in html_text
    assert 'data-value="&lt;script&gt;' in html_text


def test_block_table_shows_legs_counts_notional_in_usd_and_label():
    html_text = block_structure_table([make_block()])
    assert "BLOCK-306229" in html_text and "2026-11-14 09:30 UTC" in html_text
    assert "5 / 7" in html_text and "3 / 2" in html_text and "4 / 1" in html_text
    assert "25DEC26, 26MAR27" in html_text
    assert "400.0M USD" in html_text and "$" not in html_text
    assert "25DEC26 120,000 call bought 2,721.7 contracts, 326.6M USD" in html_text
    assert "only 5 of 7 legs in the window" in html_text


def test_block_table_with_unknown_declared_count_says_not_computed_and_escapes():
    block = make_block(declared_leg_count=None, block_trade_id="<b>x</b>", label="<i>y</i>")
    html_text = block_structure_table([block])
    assert "5 / not computed" in html_text
    assert "<b>x</b>" not in html_text and "&lt;b&gt;x&lt;/b&gt;" in html_text and "<i>y</i>" not in html_text


def test_the_page_script_handles_the_jump_buttons():
    page = build_report_page_for_script()
    for needle in ("[data-switcher-target]", "aria-pressed", "scrollIntoView", "prefers-reduced-motion", "drawSwitcher(element)"):
        assert needle in page


def build_report_page_for_script():
    from coding.core.analytics.report.report_page import ReportHeader
    return build_report_page(ReportHeader("BTC", TIME, []), [ReportSection(key="one", title="One")], None)
