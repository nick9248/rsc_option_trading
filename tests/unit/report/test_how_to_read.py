"""Tests of the report help (``how_to_read.py``): structure, wording rules, and the arithmetic of every example."""

import inspect
import math
import re
import statistics
from datetime import datetime, timezone
from typing import Iterator, List, Tuple

import pytest

from coding.core.analytics.futures_flow import taker_flow_figure
from coding.core.analytics.futures_flow.taker_flow import taker_buy_sell_ratio
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import HeatmapSettings
from coding.core.analytics.menthorq_replication_calculator import (
    DEFAULT_LEVEL_CANDIDATES, GEX_PER_ONE_PERCENT_MOVE, strike_of_cumulative_minimum)
from coding.core.analytics.dealer_inventory_calculator import DealerInventoryCalculator
from coding.core.analytics.option_flow.level_overview import LevelMark, find_confluences
from coding.core.analytics.report import how_to_read as help_module
from coding.core.analytics.report.how_to_read import (
    REPORT_HELP, SECTION_HELP, HelpCase, HelpTopic, SectionHelp, all_help, confluence_width, cumulative_minimum_strike,
    days_left, example_ratio, flow_gex_per_one_percent_usd, flow_view_passes, gex_expiring_percent,
    long_liquidation_price, net_dex_usd, net_gex_per_one_percent_usd, notional_usd, percent_from, share_by_part_percent,
    share_percent, short_liquidation_price, sign_change_strike, strike_of_largest_positive, strike_of_most_negative,
    violation_percent, zoom_window)
from coding.service.on_chain.on_chain_analysis_service import (
    _DEALER_INVENTORY_COVERAGE_GATE, _DEALER_INVENTORY_MAX_EXCLUSION_RATE, _DEALER_INVENTORY_VIOLATION_GATE)

SECTION_KEYS = ("menthorq", "futures-flow", "option-flow", "dealer-levels", "levels")
ALLOWED_CAPITAL_WORDS = {"USDT", "BTCUSDT", "ETHUSDT"}


def all_text() -> Iterator[Tuple[str, str]]:
    """(where, text) of every string of the help."""
    for section in all_help():
        yield f"{section.section_key} summary", section.summary
        for topic in section.topics:
            where = f"{section.section_key}/{topic.title}"
            yield f"{where} title", topic.title
            yield f"{where} what_you_see", topic.what_you_see
            for term in topic.terms:
                yield f"{where}/term {term.term}", term.term
                yield f"{where}/term {term.term} meaning", term.meaning
            for case in topic.cases:
                yield f"{where}/case {case.situation} situation", case.situation
                yield f"{where}/case {case.situation} reading", case.reading
                yield f"{where}/case {case.situation} example", case.example
            for limit in topic.limits:
                yield f"{where}/limit", limit


def topics_of(section_key: str) -> List[HelpTopic]:
    """The topics of a section."""
    section = REPORT_HELP if section_key == "report" else SECTION_HELP[section_key]
    return list(section.topics)


def case_of(section_key: str, situation_start: str) -> HelpCase:
    """The one case of a section whose situation starts with ``situation_start``."""
    found = [case for topic in topics_of(section_key) for case in topic.cases
             if case.situation.startswith(situation_start)]
    assert len(found) == 1, f"{len(found)} cases start with {situation_start!r} in {section_key}"
    return found[0]


def topic_of(section_key: str, title_start: str) -> HelpTopic:
    """The one topic of a section whose title starts with ``title_start``."""
    found = [topic for topic in topics_of(section_key) if topic.title.startswith(title_start)]
    assert len(found) == 1
    return found[0]


# ------------------------------------------------------------------ structure

def test_every_section_key_is_present():
    assert set(SECTION_HELP) == set(SECTION_KEYS)
    assert REPORT_HELP.section_key == "report"
    for key, section in SECTION_HELP.items():
        assert section.section_key == key


@pytest.mark.parametrize("section", all_help(), ids=lambda section: section.section_key)
def test_sections_topics_and_cases_are_filled(section: SectionHelp):
    assert section.summary.strip()
    assert len(section.topics) >= 1
    for topic in section.topics:
        assert topic.title.strip() and topic.what_you_see.strip()
        assert len(topic.terms) >= 1 and len(topic.cases) >= 1 and len(topic.limits) >= 1
        for term in topic.terms:
            assert term.term.strip() and term.meaning.strip()
        for case in topic.cases:
            assert case.situation.strip() and case.reading.strip() and case.example.strip()


def test_term_names_are_unique_within_a_topic():
    for section in all_help():
        for topic in section.topics:
            names = [term.term for term in topic.terms]
            assert len(names) == len(set(names)), (section.section_key, topic.title)


def test_expected_topics_exist():
    assert len(topics_of("menthorq")) == 4
    assert topic_of("menthorq", "Net GEX panels")
    assert topic_of("menthorq", "Net DEX")
    assert topic_of("menthorq", "IV x OI per expiry")
    assert topic_of("dealer-levels", "The plausibility check")
    assert topic_of("levels", "The liquidation estimate")
    assert topic_of("levels", "Using the levels")


# ------------------------------------------------------------------ wording rules

def test_no_dollar_sign_and_no_angle_brackets():
    for where, text in all_text():
        assert "$" not in text, where
        assert "<" not in text and ">" not in text, where


def test_flip_and_hvl_are_never_read_as_a_gamma_regime():
    """The flip and the HVL are points on the strike axis: no sentence may pair them with a regime or dampening story."""
    for where, text in all_text():
        for sentence in re.split(r"(?<=[.;:])\s+", text):
            lowered = sentence.lower()
            if "flip" in lowered or "hvl" in lowered:
                for word in ("dampen", "amplif", "regime"):
                    assert word not in lowered, (where, sentence)


def test_no_hard_coded_ratio_range_and_no_overclaims():
    text = " ".join(text for _, text in all_text()).lower()
    assert "0.8 and 1.15" not in text
    for phrase in ("act as a magnet", "act like magnets", "tends to dampen", "tends to amplify", "dampening environment",
                   "which strikes failed", "only prices within 15 %", "dealer assumption matters more"):
        assert phrase not in text, phrase


def test_menthorq_charts_are_not_described_as_all_expiries():
    text = " ".join(text for where, text in all_text() if where.startswith("menthorq")).lower()
    assert "over all expiries" not in text and "all-expiry net gex levels" not in text
    assert "three all-expiry level lines" not in text
    assert "monthly by default" in text


def test_menthorq_open_interest_table_timing_examples():
    close = datetime(2026, 11, 12, 23, 59, tzinfo=timezone.utc)
    open_interest_start = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
    chain = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    assert round((chain - close).total_seconds() / 3600) == 14
    assert round((chain - open_interest_start).total_seconds() / 3600) == 38
    example = case_of("menthorq", "This tab and the Levels tab").example
    assert "14 hours" in example and "38 hours" in example
    assert "38 hours" in case_of("menthorq", "The open-interest table and the charts differ").example
    assert "38 hours" in case_of("report", "Two charts or two tabs").example


def test_plausibility_and_level_help_wording_fixes():
    check = topic_of("dealer-levels", "The plausibility check")
    terms = {term.term: term.meaning for term in check.terms}
    assert "any trade of the currency, not only this expiry" in terms["Coverage"]
    assert "open interest 0 is checked" in terms["Options without an open interest value"]
    levels = " ".join(topic_of("levels", "The lanes").limits)
    assert "opens at plus or minus 15 %" in levels and "ledger lists them all" in levels
    single = next(term.meaning for term in topic_of("option-flow", "Block-structure").terms if term.term == "Label 'single leg'")
    assert "only 1 of 4 legs in the window" in single
    assert case_of("levels", "Many levels, fewer than 3 sources")


def test_no_all_capital_words_except_known_acronyms():
    for where, text in all_text():
        for word in re.findall(r"\b[A-Z]{4,}\b", text):
            assert word in ALLOWED_CAPITAL_WORDS, f"{word!r} in {where}"


def test_no_forecast_wording():
    forbidden = ("will rise", "will fall", "will go", "price target is", "is guaranteed", "always reverses", "price target is")
    for where, text in all_text():
        for phrase in forbidden:
            assert phrase not in text.lower(), (where, phrase)


def test_dealer_and_net_gex_topics_state_the_assumption():
    for section_key, title in (("menthorq", "Net GEX panels"), ("dealer-levels", "Flow-based and OI-based GEX")):
        topic = topic_of(section_key, title)
        text = " ".join([topic.what_you_see] + [term.meaning for term in topic.terms]
                        + [case.reading for case in topic.cases] + list(topic.limits)).lower()
        assert "assumption" in text, title
    dealer_terms = " ".join(term.meaning for term in topic_of("dealer-levels", "Flow-based").terms).lower()
    assert dealer_terms.count("assumption") >= 2


def test_report_help_states_the_basic_limits():
    text = " ".join(text for where, text in all_text() if where.startswith("report")).lower()
    for phrase in ("not computed", "never mean", "ground truth", "not independent", "forward test", "as of"):
        assert phrase in text or phrase.replace(" ", "") in text, phrase
    assert "zero" in text


def test_menthorq_help_names_the_hvl_rules_of_the_code():
    topic = topic_of("menthorq", "IV x OI chart")
    text = " ".join(topic.limits + tuple(term.meaning for term in topic.terms))
    assert DEFAULT_LEVEL_CANDIDATES == {"call_resistance": "net_gex_argmax_positive",
                                        "put_support": "net_gex_argmin_negative", "hvl": "cumulative_net_gex_argmin"}
    for candidate in ("net_gex_argmax_positive", "net_gex_argmin_negative", "cumulative_net_gex_argmin"):
        assert candidate in text
    for alternative in ("spot sweep", "zero crossing", "sign", "call-only"):
        assert alternative in text
    assert "23:59 UTC" in " ".join(case.reading for case in topic.cases)


# ------------------------------------------------------------------ constants against the code

def test_constants_match_the_code():
    assert help_module.GEX_PER_ONE_PERCENT_MOVE == GEX_PER_ONE_PERCENT_MOVE
    assert help_module.COVERAGE_GATE == _DEALER_INVENTORY_COVERAGE_GATE
    assert help_module.VIOLATION_GATE == _DEALER_INVENTORY_VIOLATION_GATE
    assert help_module.MAX_EXCLUSION_RATE == _DEALER_INVENTORY_MAX_EXCLUSION_RATE
    assert help_module.MAINTENANCE_MARGIN_RATE == HeatmapSettings().maintenance_margin_rate


def test_level_rule_helpers_match_the_code():
    net = help_module.GEX_EXAMPLE_NET_BY_STRIKE_MILLION
    assert cumulative_minimum_strike(net) == strike_of_cumulative_minimum(dict(net)) == 70_000.0
    assert strike_of_largest_positive(net) == 80_000.0
    assert strike_of_most_negative(net) == 60_000.0
    flip_rows = {strike: {"inferred_gex": value} for strike, value in help_module.GEX_FLIP_EXAMPLE_BY_STRIKE_MILLION.items()}
    assert DealerInventoryCalculator._detect_key_levels(flip_rows)["hvl"] == sign_change_strike(
        help_module.GEX_FLIP_EXAMPLE_BY_STRIKE_MILLION) == 70_000.0
    never_negative = {60_000.0: 1.0, 65_000.0: 2.0, 70_000.0: 3.0}
    assert sign_change_strike(never_negative) is None
    assert DealerInventoryCalculator._detect_key_levels(
        {strike: {"inferred_gex": value} for strike, value in never_negative.items()})["hvl"] is None


def test_liquidation_helpers_match_the_model():
    settings = HeatmapSettings()
    assert long_liquidation_price(70_000, 25) == pytest.approx(settings.long_liquidation_price(70_000, 25))
    assert short_liquidation_price(70_000, 25) == pytest.approx(settings.short_liquidation_price(70_000, 25))
    assert round(long_liquidation_price(70_000, 25)) == 67_480
    assert round(short_liquidation_price(70_000, 25)) == 72_520


# ------------------------------------------------------------------ the arithmetic of the examples

def test_futures_ratio_example():
    assert round(example_ratio(5.2e9, 5.7e9), 2) == 0.91
    assert round(5.2 / 5.7, 3) == 0.912
    assert taker_buy_sell_ratio(5.2e9, 5.7e9) == example_ratio(5.2e9, 5.7e9)
    with pytest.raises(ValueError):
        example_ratio(1.0, 0.0)
    below = case_of("futures-flow", "Ratio below 1")
    assert "0.91" in below.example and "0.912" in below.example and "5.2B USD" in below.example
    assert "-0.5B USD" in below.example
    above = case_of("futures-flow", "Ratio above 1")
    assert "1.20" in above.example and "6.0B USD" in above.example and "5.0B USD" in above.example
    assert example_ratio(6.0e9, 5.0e9) == pytest.approx(1.2)
    even = case_of("futures-flow", "Ratio about 1")
    assert "1.0" in even.example
    band = case_of("futures-flow", "The line leaves the band")
    assert "0.082" in band.example and "0.08" in band.example


def test_band_example_uses_the_population_standard_deviation():
    values = help_module.FUTURES_RATIO_VALUES
    assert values == (0.9, 1.0, 1.1)
    # population: sqrt(((0.1)^2 + 0 + (0.1)^2) / 3); the sample version would divide by 2 and give 0.1
    assert help_module.population_standard_deviation(values) == pytest.approx(math.sqrt(0.02 / 3))
    assert round(math.sqrt(0.02 / 3), 3) == 0.082
    assert statistics.pstdev(values) == pytest.approx(help_module.population_standard_deviation(values))
    assert statistics.stdev(values) != pytest.approx(help_module.population_standard_deviation(values))
    # the chart's own band uses pstdev (taker_flow_figure.py)
    assert "pstdev" in inspect.getsource(taker_flow_figure)


def test_index_versus_strike_notional_example():
    assert help_module.index_notional_difference_percent(77_000, 70_000) == pytest.approx(7_000 / 77_000 * 100)
    assert round(7_000 / 77_000 * 100, 1) == 9.1
    assert round(38_000 / 108_000 * 100, 1) == 35.2
    # contracts x index against contracts x strike, recomputed directly
    assert round((1 - (10 * 70_000) / (10 * 77_000)) * 100) == 9
    assert round((1 - (10 * 70_000) / (10 * 108_000)) * 100) == 35
    assert help_module.index_notional_difference_percent(60_000, 70_000) < 0
    term = next(term.meaning for term in topic_of("option-flow", "Buy and sell bars").terms if term.term == "Notional")
    assert "9 % less" in term and "35 % less" in term and "17 % more" in term
    assert "4 %" not in term


def test_option_flow_examples():
    assert notional_usd(10, 80_000) == 800_000
    assert "800,000 USD" in case_of("option-flow", "Calls bought").example
    assert "800,000 USD" in case_of("option-flow", "Calls sold").example
    assert notional_usd(10, 65_000) == 650_000
    assert "650,000 USD" in case_of("option-flow", "Puts bought").example
    assert "650,000 USD" in case_of("option-flow", "Puts sold").example
    mixed = case_of("option-flow", "Notional, contracts and trades").example
    assert "800,000" in mixed and f"{notional_usd(10, 140_000):,.0f}" in mixed and "1,400,000" in mixed
    assert "4M USD long" in case_of("option-flow", "A bar has a blocked").example
    far = case_of("option-flow", "Large flow far").example
    assert f"{percent_from(140_000, 70_000):,.0f} %" in far and "100 %" in far
    shares = share_by_part_percent((6e6, 3e6, 1e6))
    assert shares == pytest.approx([60.0, 30.0, 10.0])
    carry = case_of("option-flow", "One expiry carries").example
    assert "10M USD" in carry and "60 %, 30 % and 10 %" in carry


def test_open_interest_table_example():
    row = case_of("menthorq", "Reading one table row").example
    total = 600 + 400
    assert total == 1000
    assert "1,000 coins" in row
    assert total * 70_000 == 70_000_000 and "70M USD" in row
    assert share_percent(total, 4000) == 25.0 and "25 %" in row
    days = days_left(help_module.EXPIRY_NOW, help_module.EXPIRY_SETTLEMENT)
    assert days == 7.0 and "7 days left" in case_of("menthorq", "Few days left").example
    assert days_left(help_module.EXPIRY_NOW, help_module.EXPIRY_FAR_SETTLEMENT) == 90.0
    assert "90 days left" in case_of("menthorq", "Many days left").example
    assert "25 %" in case_of("menthorq", "One expiry has a big share").example


def test_iv_times_open_interest_example():
    bar = case_of("menthorq", "Reading one bar").example
    first, second = 60 / 100 * 500, 50 / 100 * 200
    assert (first, second, first + second) == (300.0, 100.0, 400.0)
    assert "= 300" in bar and "adds 100" in bar and "bar is 400" in bar


def test_net_gex_examples():
    # The worked example of documentation/gex_dex_methodology.md: strike 72,000, net GEX 1,715,000.
    assert net_gex_per_one_percent_usd(0.05, 0.015, 70_000) == pytest.approx(1_715_000)
    formula = case_of("menthorq", "Net GEX of one strike").example
    assert "1,715,000 USD" in formula and "0.035" in formula
    red = case_of("menthorq", "A red bar").example
    assert net_gex_per_one_percent_usd(0.02, 0.05, 70_000) == pytest.approx(-1_470_000)
    assert "-1,470,000 USD" in red
    assert gex_expiring_percent(30, (30, -10, 60)) == pytest.approx(30.0)
    assert "30.0 %" in case_of("menthorq", "GEX Expiring").example
    levels = case_of("menthorq", "How the three lines").example
    assert "60,000: -3M" in levels and "+4M" in levels and "-3, -5, -6, -5, -1" in levels
    assert "Call resistance 80,000, put support 60,000, HVL 70,000" in levels
    assert "70,000" in case_of("menthorq", "The profile line").example
    cumulative = list(help_module.cumulative_net_gex(help_module.GEX_EXAMPLE_NET_BY_STRIKE_MILLION).values())
    assert cumulative == [-3.0, -5.0, -6.0, -5.0, -1.0]


def test_net_dex_examples():
    assert net_dex_usd(100, 0.5, 100, -0.3, 70_000) == pytest.approx(1_400_000)
    assert "1,400,000 USD" in case_of("menthorq", "A positive DEX bar").example
    assert net_dex_usd(100, 0.2, 100, -0.5, 70_000) == pytest.approx(-2_100_000)
    assert "-2,100,000 USD" in case_of("menthorq", "A negative DEX bar").example
    assert [sum([2, -1, 3][:count]) for count in (1, 2, 3)] == [2, 1, 4]


def test_price_versus_level_examples():
    spot_levels = {"call": 75_000, "hvl": 68_000, "put": 62_000}
    above = case_of("menthorq", "Price is above call resistance").example
    assert f"{percent_from(78_000, spot_levels['call']):.1f} %" in above and "4.0 %" in above
    between = case_of("menthorq", "Price is between HVL and call").example
    assert f"{spot_levels['call'] - 72_000:,}" in between and f"{72_000 - spot_levels['hvl']:,}" in between
    lower = case_of("menthorq", "Price is between put support").example
    assert f"{spot_levels['hvl'] - 65_000:,}" in lower and f"{65_000 - spot_levels['put']:,}" in lower
    below = case_of("menthorq", "Price is below put support").example
    assert f"{abs(percent_from(60_000, spot_levels['put'])):.1f} %" in below and "3.2 %" in below
    assert "0 %" in case_of("menthorq", "Price is at a level").example
    assert "5.0 %" not in above
    assert "7.1 %" in REPORT_HELP.topics[0].cases[0].example
    assert round(percent_from(75_000, 70_000), 1) == 7.1


def test_dealer_levels_examples():
    assert help_module.dealer_position(100) == -100
    assert "-100" in case_of("dealer-levels", "Dealer position").example
    assert flow_gex_per_one_percent_usd(-100, 0.00005, 0, 0, 70_000) == pytest.approx(-245_000)
    assert "-245,000 USD" in case_of("dealer-levels", "Flow-based GEX").example
    assert "1,715,000 USD" in case_of("dealer-levels", "OI-based GEX").example
    assert "2,000 below the wall" not in case_of("dealer-levels", "Price is above the gamma flip").example
    above_flip = case_of("dealer-levels", "Price is above the gamma flip").example
    assert "4,000 above the flip" in above_flip and "3,000 below the wall" in above_flip
    below_flip = case_of("dealer-levels", "Price is below the gamma flip").example
    assert "2,000 below the flip" in below_flip and "1,000 above the put wall" in below_flip
    assert "4.0 %" in case_of("dealer-levels", "Price is above the call wall").example
    assert "4.6 %" in case_of("dealer-levels", "Price is below the put wall").example
    assert round(abs(percent_from(62_000, 65_000)), 1) == 4.6
    assert "5.5 %" in case_of("dealer-levels", "The two views disagree").example
    assert round(percent_from(77_000, 73_000), 1) == 5.5
    multiple = case_of("dealer-levels", "The running sum changes sign more than once").example
    assert "+1, -2, +2, +1" in multiple and "Flip 70,000" in multiple
    assert sign_change_strike({1.0: 1.0, 2.0: 2.0, 3.0: 3.0}) is None
    assert "no flip" in case_of("dealer-levels", "The gamma flip says not computed").example


def test_plausibility_check_examples():
    assert violation_percent(6, 100) == 6.0 and not flow_view_passes(1.0, 0.06)
    assert violation_percent(4, 100) == 4.0 and flow_view_passes(1.0, 0.04)
    assert not flow_view_passes(0.90, 0.0)
    assert flow_view_passes(0.95, 0.05)
    failing = case_of("dealer-levels", "Too many violations").example
    assert "6 of 100" in failing and "6 %" in failing
    passing = case_of("dealer-levels", "The check passes").example
    assert "4 of 100" in passing and "4 %" in passing
    assert "90 %" in case_of("dealer-levels", "Coverage too low").example
    thresholds = " ".join(topic_of("dealer-levels", "The plausibility check").limits)
    assert "95 %" in thresholds and "5 %" in thresholds and "20 %" in thresholds
    terms = {term.term: term.meaning for term in topic_of("dealer-levels", "The plausibility check").terms}
    assert "95 %" in terms["Coverage"] and "5 %" in terms["Violations"] and "20 %" in terms["Options without an open interest value"]
    assert "tiny" in case_of("dealer-levels", "Failures are tiny strikes").reading.lower()
    assert "weigh that lane low" in case_of("dealer-levels", "Failures are tiny strikes").reading


def test_confluence_examples_against_the_code():
    spot = 70_000.0
    width = confluence_width(spot)
    assert width == pytest.approx(700.0)

    def bands(marks):
        return find_confluences([LevelMark(source, "level", price, "resistance") for source, price in marks], width, 3)

    assert len(bands(help_module.LEVELS_AGREE_MARKS)) == 1
    assert bands(help_module.LEVELS_TOO_FAR_MARKS) == []
    assert bands(help_module.LEVELS_SAME_SOURCE_MARKS) == []
    agree = case_of("levels", "Three different sources").example
    assert "700" in agree and "75,000" in agree and "75,200" in agree and "75,400" in agree and "400 from the first" in agree
    far = case_of("levels", "Levels too far apart").example
    assert "1,000 from the first" in far and "700" in far
    assert "75,600" in case_of("levels", "Many levels, fewer than 3 sources").example


def test_levels_zoom_and_ruler_examples():
    low, high = zoom_window(70_000, 0.05)
    assert (round(low), round(high)) == (66_500, 73_500)
    assert "66,500 to 73,500" in case_of("levels", "The zoom buttons").example
    assert "+7.1%" in case_of("levels", "The ruler").example
    assert "6 of 100" in case_of("levels", "Hollow diamonds").example


def test_strategy_and_hedge_examples():
    spread = case_of("levels", "Structuring a spread").example
    assert "5,000 points wide" in spread
    hedge = case_of("levels", "Hedging a long position").example
    assert f"{abs(percent_from(66_000, 70_000)):.1f} %" in hedge and "5.7 %" in hedge
    stop = case_of("levels", "Placing a stop").example
    assert f"{62_000 * 0.99:,.0f}" in stop and "61,380" in stop


def test_liquidation_examples():
    long_case = case_of("levels", "A long liquidation level").example
    assert "67,480" in long_case and "0.004" in long_case
    short_case = case_of("levels", "A short liquidation level").example
    assert "72,520" in short_case
    topic = topic_of("levels", "The liquidation estimate")
    text = " ".join(case.reading for case in topic.cases) + " ".join(topic.limits)
    assert "not a floor" in text and "not a ceiling" in text and "not shown by this report" in text and "no direction" in text
    assert "forward test" in text


def test_levels_help_names_every_lane_and_the_shared_inputs():
    topic = topic_of("levels", "The lanes")
    terms = " ".join(term.term for term in topic.terms)
    for lane in ("MenthorQ replica", "Dealer GEX, open interest", "Dealer GEX, flow-based", "Option flow",
                 "Liquidation estimate"):
        assert lane in terms
    shared = next(term for term in topic.terms if term.term == "Shared inputs").meaning
    assert "same open interest" in shared and "same stored trades" in shared
    confluence = topic_of("levels", "Where sources agree")
    meanings = " ".join(term.meaning for term in confluence.terms)
    assert "1 %" in meanings and "3" in meanings
    assert "3 of 5" in " ".join(term.term for term in confluence.terms)
    assert "coincidence of methods" in " ".join(case.reading for case in confluence.cases)
