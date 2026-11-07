"""
Telegram HTML brief and urgent alert: section order, escaping, balanced
tags (only b / i / pre), the 4096-character limit and the expiry-roll label.
"""

from datetime import timedelta
from html.parser import HTMLParser

import pytest

from coding.core.analytics.menthorq_monitor.alert_rules import evaluate_urgent_alerts
from coding.core.analytics.menthorq_monitor.level_trend_calculator import compare_records
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    TELEGRAM_MESSAGE_LIMIT,
    escape,
    format_brief,
    format_gex,
    format_signed_percent,
    format_signed_usd,
    format_strike,
    format_urgent_alert,
    telegram_length,
)
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

ALLOWED_TAGS = {"b", "i", "pre"}


class TagChecker(HTMLParser):
    """Collects problems: unknown tags, unbalanced tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"tag {tag}")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.problems.append(f"unbalanced {tag}")

    def handle_data(self, data):
        self.text.append(data)


def _check_html(message: str) -> str:
    checker = TagChecker()
    checker.feed(message)
    checker.close()
    assert checker.problems == [] and checker.stack == []
    return "".join(checker.text)


def _comparisons(latest, references):
    return [compare_records(latest, reference, label) for label, reference in references]


def _standard_case():
    latest = make_record(spot=100000.0, call_resistance=110000.0, put_support=90000.0,
                         hvl=101000.0, total_net_gex=-1e6, expirations=["27NOV26", "25DEC26"])
    previous = make_record(as_of=BASE_TIME - timedelta(hours=1), hvl=99000.0, total_net_gex=2e6,
                           expirations=["27NOV26", "25DEC26"])
    day_ago = make_record(as_of=BASE_TIME - timedelta(hours=24), hvl=96000.0,
                          expirations=["6NOV26", "27NOV26", "25DEC26"])
    comparisons = _comparisons(latest, [("previous", previous), ("24h", day_ago), ("7d", None)])
    return latest, comparisons


def test_number_formats():
    assert format_strike(80000.0) == "80,000" and format_strike(None) == "n/a"
    assert format_signed_usd(5000.0) == "+5,000" and format_signed_usd(-0.2) == "0"
    assert format_signed_percent(1.234) == "+1.23%" and format_signed_percent(None) == "n/a"
    assert format_gex(8670788.5) == "+$8.67M" and format_gex(-2.5e9) == "-$2.50B"
    assert format_gex(-12345.0) == "-$12K"


def test_brief_sections_in_order_and_valid_html():
    latest, comparisons = _standard_case()
    message = format_brief(latest, comparisons, title="Weekly brief")
    text = _check_html(message)
    positions = [text.index(title) for title in
                 ("BTC MenthorQ-style levels | Weekly brief", "Key levels", "Gamma regime",
                  "Changes", "Panels", "Notable events", "Data caveats")]
    assert positions == sorted(positions)
    # spot 100,000 < HVL 101,000: negative gamma, 1,000 (1.00%) below
    assert "NEGATIVE gamma: spot 1,000 (1.00%) below HVL." in text
    assert "Spot crossed BELOW HVL vs previous run" in text
    assert "Total net GEX changed sign vs previous run (+$2.00M -> -$1.00M)" in text
    # 24h: HVL 96,000 -> 101,000 with 6NOV26 settled in between -> roll-off label
    assert "6NOV26 settled since the 24h record; the moves of HVL coincide with this roll-off" in text
    assert "HVL     +5,000 (+5.00% of spot)" in text
    assert "vs 7d ago: no stored record" in text
    assert "No stored record for the 7d comparison." in text
    assert telegram_length(message) <= TELEGRAM_MESSAGE_LIMIT


def test_dynamic_text_is_escaped():
    latest = make_record(not_computed_reasons=["a <b> & c > d"], expirations=["X<Y&Z"])
    message = format_brief(latest, [], title="T <script>")
    assert "a &lt;b&gt; &amp; c &gt; d" in message
    assert "X&lt;Y&amp;Z" in message
    assert "T &lt;script&gt;" in message
    text = _check_html(message)
    assert "a <b> & c > d" in text            # what the reader sees
    assert escape("<&>") == "&lt;&amp;&gt;"


def test_oversized_brief_is_shortened_below_the_limit_and_says_so():
    huge_reasons = [f"reason {index} " + "x" * 400 for index in range(400)]
    many_expiries = [f"{day}DEC26" for day in range(1, 29)] * 5
    latest = make_record(not_computed_reasons=huge_reasons, expirations=many_expiries)
    references = [(label, make_record(as_of=BASE_TIME - offset, expirations=many_expiries + ["6NOV26"]))
                  for label, offset in (("previous", timedelta(hours=1)),
                                        ("24h", timedelta(hours=24)), ("7d", timedelta(days=7)))]
    message = format_brief(latest, _comparisons(latest, references), title="Weekly brief")
    assert telegram_length(message) <= TELEGRAM_MESSAGE_LIMIT
    _check_html(message)
    assert "Shortened to fit Telegram" in message
    assert "... and" in message                # the list says how many items were left out
    assert "Key levels" in message and "Gamma regime" in message


@pytest.mark.parametrize("limit", [3000, 3500, 4096])
def test_limits_below_telegram_are_respected_with_a_real_brief(limit):
    # Fully shortened, a brief is at most about 2,900 characters (3 events and 3
    # caveats of <= 300 characters, one comparison block), so 3000 always works.
    latest, comparisons = _standard_case()
    latest.not_computed_reasons = ["long reason " + "y" * 400] * 30
    latest.expirations = [f"{day}DEC26" for day in range(1, 29)]
    for comparison in comparisons:
        comparison.settled_expiries = [f"{day}NOV26" for day in range(1, 29)] if comparison.reference else []
    message = format_brief(latest, comparisons, limit=limit)
    assert telegram_length(message) <= limit
    _check_html(message)
    assert "Key levels" in message             # a real brief, not the last-resort notice


def test_telegram_length_counts_utf16_code_units():
    assert telegram_length("ab") == 2
    assert telegram_length("a\U0001F600") == 3      # an emoji is two UTF-16 units


def test_urgent_alert_lists_alerts_with_roll_off_label():
    latest = make_record(spot=100000.0, hvl=101000.0, total_net_gex=-1e6, expirations=["27NOV26"])
    previous = make_record(as_of=BASE_TIME - timedelta(hours=1), hvl=99000.0, total_net_gex=2e6,
                           expirations=["27NOV26"])
    day_ago = make_record(as_of=BASE_TIME - timedelta(hours=24), hvl=96000.0,
                          expirations=["6NOV26", "27NOV26"])
    alerts = evaluate_urgent_alerts(compare_records(latest, previous, "previous"),
                                    compare_records(latest, day_ago, "24h"))
    message = format_urgent_alert(latest, alerts)
    text = _check_html(message)
    assert text.startswith("URGENT | BTC MenthorQ-style levels")
    assert ("HVL moved more than 3% of spot in 24h: +5,000 (+5.00% of spot). Coincides with "
            "the settlement of 6NOV26 (expiry roll-off).") in text
    assert "Spot crossed BELOW HVL (negative gamma regime)." in text
    assert "Total net GEX turned NEGATIVE: net GEX change -$3.00M." in text
    assert telegram_length(message) <= TELEGRAM_MESSAGE_LIMIT
