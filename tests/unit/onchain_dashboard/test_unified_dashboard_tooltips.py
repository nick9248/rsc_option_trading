"""The tooltips of the unified dashboard: every tile, gauge, card and chip of the Overview explains itself."""

import html
import re

import pytest

from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
from coding.core.analytics.unified_dashboard import build_unified_dashboard_page
from coding.core.analytics.unified_dashboard.glossary import TOOLTIPS, tooltip_text
from coding.core.analytics.unified_dashboard.overview import CARD_TOOLTIPS, GAUGE_TOOLTIPS, TILE_TOOLTIPS, card, gauge, tile
from tests.unit.onchain_dashboard.test_unified_dashboard import PLOTLY_STAND_IN, make_report_page

CHIP_KEYS = {"chip_chain", "chip_gamma", "chip_upside"}


def test_every_tooltip_has_the_three_parts_and_no_placeholder_text():
    for key, tooltip in TOOLTIPS.items():
        for part in (tooltip.what, tooltip.how_to_read, tooltip.example):
            assert len(part) > 20, f"{key}: a part is empty or too short"
        assert "TODO" not in tooltip.text() and "..." not in tooltip.text()


def test_tooltip_text_names_the_three_parts_and_an_unknown_key_is_an_error():
    lines = tooltip_text("dvol").split("\n")
    assert [line.split(":")[0] for line in lines] == ["What it is", "How to read it", "Example"]
    with pytest.raises(KeyError):
        tooltip_text("no_such_tooltip")


def test_every_key_used_by_the_page_exists_and_every_glossary_entry_is_used():
    used = set(TILE_TOOLTIPS.values()) | set(GAUGE_TOOLTIPS.values()) | set(CARD_TOOLTIPS.values()) | CHIP_KEYS
    assert used == set(TOOLTIPS)


def test_tile_gauge_and_card_carry_their_tooltip_and_are_keyboard_focusable():
    html_tile = tile("DVOL", "55", "p90", "")
    assert 'data-tip="' in html_tile and 'tabindex="0"' in html_tile and "has-tip" in html_tile
    assert "What it is:" in html.unescape(html_tile)
    assert "has-tip" in gauge(0.5, "p50", "DVOL 55", "30-day percentile", "g1")
    assert "has-tip" in card("Price map", "<p>x</p>", "map")
    assert "data-tip" not in tile("Something new", "1", "x", "")             # no entry, no tooltip (the page test below catches it)


def test_the_tip_text_is_escaped_so_it_cannot_break_the_attribute():
    attribute = re.search(r'data-tip="([^"]*)"', tile("Net GEX · all expiries", "-1.5M", "USD", "neg")).group(1)
    assert '"' not in attribute and "<" not in attribute
    assert html.unescape(attribute) == tooltip_text("net_gex")


@pytest.fixture(scope="module")
def unified_page(real_result):
    return build_unified_dashboard_page(make_report_page(), build_onchain_dashboard_page(real_result, PLOTLY_STAND_IN), "BTC")


def test_no_tile_and_no_known_card_of_the_built_overview_is_without_a_tooltip(unified_page):
    overview = unified_page[unified_page.index('id="panel-home"'):unified_page.index('id="panel-levels"') if 'id="panel-levels"' in unified_page else None]
    tiles = re.findall(r'<div class="tile(?: [^"]*)?"[^>]*>', overview)
    assert tiles, "the overview has no tiles: the test would prove nothing"
    assert all("data-tip=" in opening for opening in tiles), [opening for opening in tiles if "data-tip=" not in opening]
    titles = re.findall(r'<div class="card-title([^"]*)"[^>]*><span class="card-ico">.*?<h3>(.*?)</h3>', overview)
    assert titles
    for classes, title in titles:
        assert ("has-tip" in classes) == (html.unescape(title) in CARD_TOOLTIPS)
        assert html.unescape(title) in CARD_TOOLTIPS, f"card '{title}' has no tooltip"
