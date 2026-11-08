"""
Telegram HTML brief and urgent alert: section order, escaping, balanced
tags (only b / i / pre / code), the 4096-character limit, the expiry-roll
label, the "DATA STALE" line and the de-duplicated caveats.
"""

from datetime import timedelta
from html.parser import HTMLParser

import pytest

from coding.core.analytics.menthorq_monitor.alert_rules import AlertThresholds, evaluate_urgent_alerts
from coding.core.analytics.menthorq_monitor.level_trend_calculator import compare_records
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    CODE_BLOCK_CLOSE,
    CODE_BLOCK_OPEN,
    TELEGRAM_MESSAGE_LIMIT,
    code_block,
    escape,
    format_brief,
    format_duration,
    format_gex,
    format_signed_percent,
    format_signed_usd,
    format_strike,
    format_urgent_alert,
    regime_versus_net_gex_note,
    stale_data_line,
    stale_data_warning,
    telegram_length,
)
from coding.service.on_chain.menthorq_monitor.command_formatter import (
    format_changes_reply,
    format_levels_reply,
)
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

ALLOWED_TAGS = {"b", "i", "pre", "code"}


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
    # A missing comparison is said once, in Changes, not again in the caveats.
    assert text.count("vs 7d ago: no stored record") == 1
    assert "no stored record" not in text.lower().split("data caveats")[1]
    assert "No stored record for the" not in text
    # Legend right under the panels block; regime note shortened to one line.
    assert ("</code></pre>\n<i>CR = Call Resistance, PS = Put Support, "
            "HVL = high-volatility level (gamma flip).</i>") in message
    # make_record: snapshot 20 min, spot 30 s before the data time -> not stale.
    assert "DATA STALE" not in text
    assert text.split("Data caveats\n")[1].startswith(
        "- Live mode: newest open interest; MenthorQ's is about 1 day older.\n"
        "- Expiry set not verified against MenthorQ.")
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
    # Crossing switched on and HVL at 3% so one message shows every alert kind.
    thresholds = AlertThresholds(hvl_move_percent_of_spot=3.0, spot_crosses_hvl=True)
    alerts = evaluate_urgent_alerts(latest, compare_records(latest, previous, "previous"),
                                    compare_records(latest, day_ago, "24h"), thresholds)
    message = format_urgent_alert(latest, alerts)
    text = _check_html(message)
    assert text.startswith("URGENT | BTC MenthorQ-style levels")
    assert ("HVL moved more than 3% of spot in 24h: +5,000 (+5.00% of spot). Coincides with "
            "the settlement of 6NOV26 (expiry roll-off).") in text
    assert "Spot crossed BELOW HVL (negative gamma regime)." in text
    assert "Total net GEX turned NEGATIVE: net GEX change -$3.00M." in text
    assert telegram_length(message) <= TELEGRAM_MESSAGE_LIMIT


def test_brief_explains_when_regime_and_net_gex_disagree():
    # spot 100,000 < HVL 101,000 (negative-gamma regime) but total net GEX +5M
    disagree = make_record(hvl=101000.0, total_net_gex=5e6)
    expected = ("Spot below HVL = negative-gamma regime; total net GEX (sum over all strikes) "
                "is positive. Different measures.")
    assert regime_versus_net_gex_note(disagree) == expected
    assert f"<i>{expected}</i>" in format_brief(disagree, [])
    # spot above HVL 95,000 (positive regime) with negative GEX: the mirrored line
    assert regime_versus_net_gex_note(make_record(total_net_gex=-2e6)) == (
        "Spot above HVL = positive-gamma regime; total net GEX (sum over all strikes) "
        "is negative. Different measures.")
    # agreement or a missing value: no line
    assert regime_versus_net_gex_note(make_record(total_net_gex=5e6)) is None
    assert regime_versus_net_gex_note(make_record(hvl=101000.0, total_net_gex=-1e6)) is None
    assert regime_versus_net_gex_note(make_record(total_net_gex=None)) is None
    assert regime_versus_net_gex_note(make_record(hvl=None)) is None
    assert "Different measures" not in format_brief(make_record(), [])


def _aged(snapshot_age: timedelta, spot_age: timedelta):
    record = make_record()
    record.snapshot_captured_at_utc = record.as_of_utc - snapshot_age
    record.spot_time_utc = record.as_of_utc - spot_age
    return record


def test_stale_line_boundary_exactly_the_limit_is_not_stale():
    limit = timedelta(minutes=120)
    at_limit = _aged(snapshot_age=limit, spot_age=timedelta(seconds=30))
    assert stale_data_warning(at_limit, limit) is None
    assert "DATA STALE" not in format_brief(at_limit, [], stale_limit=limit)
    # one minute more: 121 min = "2 h 1 min"
    beyond = _aged(snapshot_age=limit + timedelta(minutes=1), spot_age=timedelta(seconds=30))
    expected = ("<b>DATA STALE:</b> snapshot is 2 h 1 min older than the data time; "
                "levels may be out of date.")
    assert stale_data_line(beyond, limit) == expected
    lines = format_brief(beyond, [], title="Weekly brief", stale_limit=limit).split("\n")
    assert lines[0] == "<b>BTC MenthorQ-style levels | Weekly brief</b>"
    assert lines[1] == expected                      # first line under the title


def test_stale_snapshot_and_spot_share_one_line_like_the_first_real_brief():
    # First real brief (img.png): data 22:29, snapshot 09:48 (12 h 41 min), spot trade
    # 09:57:17 (12 h 31 min 43 s -> 12 h 31 min, minutes rounded down).
    limit = timedelta(minutes=120)
    record = _aged(snapshot_age=timedelta(hours=12, minutes=41),
                   spot_age=timedelta(hours=12, minutes=31, seconds=43))
    message = format_brief(record, [], stale_limit=limit)
    assert ("<b>DATA STALE:</b> snapshot is 12 h 41 min older than the data time "
            "(spot trade 12 h 31 min); levels may be out of date.") in message
    assert message.count("DATA STALE") == 1
    assert "Spot comes from a trade" not in message and "Snapshot is" not in message


def test_spot_age_alone_beyond_the_limit_or_as_a_caveat_below_it():
    limit = timedelta(minutes=120)
    spot_stale = _aged(snapshot_age=timedelta(minutes=20), spot_age=timedelta(minutes=121))
    assert stale_data_warning(spot_stale, limit) == (
        "spot comes from a trade 2 h 1 min before the data time; "
        "distances to spot may be out of date.")
    assert "Spot comes from a trade" not in format_brief(spot_stale, [], stale_limit=limit)
    # 16 min: over the 15-min spot caveat, under the stale limit -> one caveat line only
    spot_late = _aged(snapshot_age=timedelta(minutes=20), spot_age=timedelta(minutes=16))
    message = format_brief(spot_late, [], stale_limit=limit)
    assert "DATA STALE" not in message
    assert "- Spot comes from a trade 16 min before the data time." in message


def test_format_duration_by_hand():
    assert format_duration(timedelta(minutes=55)) == "55 min"
    assert format_duration(timedelta(hours=12, minutes=40, seconds=59)) == "12 h 40 min"
    assert format_duration(timedelta(hours=26, minutes=5)) == "1 d 2 h 5 min"
    assert format_duration(timedelta(minutes=-3)) == "-3 min"


def test_code_blocks_use_the_single_markup_constant():
    message = format_brief(*_standard_case())
    # every block goes through code_block: levels, previous, 24h, panels
    assert message.count("<pre") == message.count(CODE_BLOCK_OPEN) == 4
    assert message.count(CODE_BLOCK_CLOSE) == 4
    assert code_block("a < b") == f"{CODE_BLOCK_OPEN}a &lt; b{CODE_BLOCK_CLOSE}"


class TestWallClockStaleness:
    """A stored record is also judged against the reader's wall clock (``now``)."""

    LIMIT = timedelta(minutes=120)

    def test_record_stored_days_ago_is_stale_and_shows_the_age_at_now(self):
        # Fresh inside the record: snapshot 20 min before the data time 09:10 (snapshot 08:50).
        record = _aged(snapshot_age=timedelta(minutes=20), spot_age=timedelta(seconds=30))
        assert stale_data_warning(record, self.LIMIT) is None
        now = BASE_TIME + timedelta(days=3)          # 2026-11-09 09:10 UTC
        # now - snapshot = 3 d + 20 min -> "3 d 0 h 20 min"
        expected = ("<b>DATA STALE:</b> snapshot is 3 d 0 h 20 min older than now "
                    "(2026-11-09 09:10 UTC); levels may be out of date.")
        assert stale_data_line(record, self.LIMIT, now) == expected
        lines = format_brief(record, [], title="Weekly brief", stale_limit=self.LIMIT, now=now).split("\n")
        assert lines[1] == expected
        assert format_brief(record, [], stale_limit=self.LIMIT, now=now).count("DATA STALE") == 1

    def test_exactly_the_limit_at_now_is_not_stale_one_minute_more_is(self):
        record = _aged(snapshot_age=timedelta(minutes=20), spot_age=timedelta(seconds=30))
        at_limit = record.snapshot_captured_at_utc + self.LIMIT       # 10:50 UTC
        assert stale_data_warning(record, self.LIMIT, at_limit) is None
        beyond = at_limit + timedelta(minutes=1)                      # 10:51 -> 121 min = 2 h 1 min
        assert stale_data_warning(record, self.LIMIT, beyond) == (
            "snapshot is 2 h 1 min older than now (2026-11-06 10:51 UTC); levels may be out of date.")

    def test_one_line_with_the_larger_age_and_the_stale_spot(self):
        # In-record: snapshot 12 h 41 min, spot 12 h 31 min before the data time; now 1 h later
        # -> snapshot age at now 13 h 41 min (the larger), spot bracket unchanged.
        record = _aged(snapshot_age=timedelta(hours=12, minutes=41),
                       spot_age=timedelta(hours=12, minutes=31, seconds=43))
        now = BASE_TIME + timedelta(hours=1)
        message = format_brief(record, [], stale_limit=self.LIMIT, now=now)
        assert ("<b>DATA STALE:</b> snapshot is 13 h 41 min older than now (2026-11-06 10:10 UTC) "
                "(spot trade 12 h 31 min); levels may be out of date.") in message
        assert message.count("DATA STALE") == 1

    def test_now_equal_to_the_data_time_keeps_the_data_time_wording(self):
        record = _aged(snapshot_age=self.LIMIT + timedelta(minutes=1), spot_age=timedelta(seconds=30))
        assert stale_data_warning(record, self.LIMIT, BASE_TIME) == (
            "snapshot is 2 h 1 min older than the data time; levels may be out of date.")

    def test_levels_and_changes_replies_pass_now(self):
        record = _aged(snapshot_age=timedelta(minutes=20), spot_age=timedelta(seconds=30))
        now = BASE_TIME + timedelta(days=3)
        for message in (format_levels_reply("BTC", record, stale_limit=self.LIMIT, now=now),
                        format_changes_reply("BTC", record, [], stale_limit=self.LIMIT, now=now)):
            assert message.split("\n")[1].startswith("<b>DATA STALE:</b> snapshot is 3 d 0 h 20 min older than now")
        assert "DATA STALE" not in format_levels_reply("BTC", record, stale_limit=self.LIMIT)
