"""The levels page: the data block is exactly what the page draws, lanes are named, and nothing in the data can break the page."""

import json
import re

from coding.core.analytics.option_flow.level_overview import LevelMark, LevelOverview, find_confluences
from coding.core.analytics.option_flow.level_overview_page import (
    DATA_PLACEHOLDER, TEMPLATE_PATH, build_level_overview_page, describe_source, page_data)


def overview(extra_marks=()):
    marks = [LevelMark("MenthorQ replica (monthly expiries)", "put support", 68_000.0, "support"),
             LevelMark("Dealer GEX 27NOV26, open interest", "put wall", 68_000.0, "support"),
             LevelMark("Dealer GEX 27NOV26, flow-based, check failed", "put wall", 68_300.0, "support"),
             LevelMark("Option flow 24h (top 2 strikes)", "calls sold 311M USD", 73_000.0, "sell")] + list(extra_marks)
    return LevelOverview("BTC", 68_338.0, "27NOV26", marks, ["chain snapshot 2026-11-13 21:17 UTC, index 68,338"])


def embedded_data(page):
    match = re.search(r"const D = (\{.*?\});\nconst KIND_ORDER", page, re.S)
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_source_names_split_into_title_subtitle_and_check_flag():
    assert describe_source("MenthorQ replica (monthly expiries)") == ("MenthorQ replica", "monthly expiries", False)
    assert describe_source("Dealer GEX 27NOV26, flow-based, check failed") == ("Dealer GEX 27NOV26", "flow-based", True)
    assert describe_source("Dealer GEX 27NOV26, open interest") == ("Dealer GEX 27NOV26", "open interest", False)
    assert describe_source("Plain") == ("Plain", "", False)


def test_data_block_lists_marks_high_to_low_and_confluences_point_at_them():
    value = overview()
    bands = find_confluences(value.marks, 0.01 * value.spot, 3)
    data = page_data(value, bands, 1.0, 3)
    assert [mark["price"] for mark in data["marks"]] == [73_000.0, 68_300.0, 68_000.0, 68_000.0]
    assert len(data["confluences"]) == 1
    band = data["confluences"][0]
    assert (band["low"], band["high"]) == (68_000.0, 68_300.0) and len(band["sources"]) == 3
    assert sorted(data["marks"][index]["price"] for index in band["marks"]) == [68_000.0, 68_000.0, 68_300.0]
    assert [source["failed"] for source in data["sources"]] == [False, False, True, False]
    assert data["spot"] == 68_338.0 and data["minimumSources"] == 3 and data["bandPercent"] == 1.0


def test_page_embeds_the_same_data_and_is_self_contained():
    page = build_level_overview_page(overview(), band_percent=1.0, minimum_sources=3)
    data = embedded_data(page)
    assert len(data["marks"]) == 4 and data["currency"] == "BTC" and data["expiry"] == "27NOV26"
    assert DATA_PLACEHOLDER not in page
    assert not re.search(r'(src|href)="https?://', page)                      # no network use


def test_band_width_and_minimum_sources_change_the_confluences():
    assert len(embedded_data(build_level_overview_page(overview(), 1.0, 3))["confluences"]) == 1
    assert embedded_data(build_level_overview_page(overview(), 0.1, 3))["confluences"] == []          # band too narrow
    assert embedded_data(build_level_overview_page(overview(), 1.0, 5))["confluences"] == []          # not enough sources


def test_text_in_the_data_cannot_close_the_script_or_the_page():
    hostile = LevelMark("Source </script><b>x</b>", "name <!-- y", 70_000.0, "buy")
    page = build_level_overview_page(overview([hostile]))
    assert page.count("</script>") == 1                                       # only the page's own closing tag
    assert any(mark["source"] == "Source </script><b>x</b>" for mark in embedded_data(page)["marks"])   # the data is intact


def test_empty_overview_still_builds_a_page():
    page = build_level_overview_page(LevelOverview("ETH", 3_000.0, "", [], []))
    assert embedded_data(page)["marks"] == [] and embedded_data(page)["confluences"] == []


def test_template_has_exactly_one_data_placeholder():
    assert TEMPLATE_PATH.read_text(encoding="utf-8").count(DATA_PLACEHOLDER) == 1
