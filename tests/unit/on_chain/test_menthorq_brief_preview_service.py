"""
MenthorqBriefPreviewService: the brief, the alert-rule check and the input
rows, built without storing or sending. Expected values are worked out by
hand from the records in ``tests.fakes.fake_menthorq_brief_preview``
(spot 100,000, so 1,000 USD = 1.00% of spot).
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding.core.analytics.menthorq_monitor.level_record import MODE_DAILY_CLOSE, MODE_LIVE
from coding.service.on_chain.menthorq_monitor.brief_formatter import code_block, telegram_length
from coding.service.on_chain.menthorq_monitor.brief_preview_service import (
    PREVIEW_TITLE,
    MenthorqBriefPreviewService,
    generated_chart_files,
    iv_source_summary,
    monitor_output_directory,
    preview_as_of_for_daily_close,
    telegram_html_to_plain_text,
)
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from tests.fakes.fake_menthorq_brief_preview import FixedRecordMonitorService, moving_records
from tests.fakes.fake_menthorq_monitor import BASE_TIME

NOW = BASE_TIME + timedelta(hours=2)   # 11:10 UTC


def _preview(records, settings=None):
    monitor = FixedRecordMonitorService(records, settings)
    service = MenthorqBriefPreviewService(monitor, clock=lambda: NOW)
    return monitor, service.build_preview(BASE_TIME)


class TestBuildPreview:
    def test_alert_rules_match_the_hand_calculation(self):
        _, preview = _preview(moving_records())
        rows = {check.rule: check for check in preview.alert_checks}
        assert [check.rule for check in preview.alert_checks] == [
            "HVL move vs 24h ago", "Call Resistance move vs 24h ago",
            "Put Support move vs 24h ago", "Spot crossed HVL since previous run",
            "Total net GEX sign change since previous run",
            "Spot near Call Resistance / Put Support"]
        # HVL 91,000 -> 95,000: +4,000 = +4.00% of spot, above 3%.
        assert rows["HVL move vs 24h ago"].current_value == "+4.00% of spot (+4,000 USD)"
        assert rows["HVL move vs 24h ago"].threshold == "|move| > 3% of spot"
        assert rows["HVL move vs 24h ago"].would_fire
        # CR 106,000 -> 110,000: +4.00%, not above 5%.
        assert rows["Call Resistance move vs 24h ago"].current_value == "+4.00% of spot (+4,000 USD)"
        assert rows["Call Resistance move vs 24h ago"].threshold == "|move| > 5% of spot"
        assert not rows["Call Resistance move vs 24h ago"].would_fire
        # PS 84,000 -> 90,000: +6.00%, above 5%.
        assert rows["Put Support move vs 24h ago"].current_value == "+6.00% of spot (+6,000 USD)"
        assert rows["Put Support move vs 24h ago"].would_fire
        # 1 h earlier HVL 101,000 > spot: negative gamma; now positive.
        assert rows["Spot crossed HVL since previous run"].current_value == "negative -> positive"
        assert rows["Spot crossed HVL since previous run"].would_fire
        assert rows["Total net GEX sign change since previous run"].current_value == \
            "-$1.00M -> +$5.00M"
        assert rows["Total net GEX sign change since previous run"].would_fire
        # Spot near a wall is off in this scenario; its value is still shown:
        # CR 110,000 and PS 90,000 against spot 100,000 are +10.00% and -10.00%.
        near_wall = rows["Spot near Call Resistance / Put Support"]
        assert not near_wall.enabled and near_wall.threshold == "off" and not near_wall.would_fire
        assert near_wall.current_value == "Call Resistance +10.00%, Put Support -10.00%"
        assert all(check.enabled for check in preview.alert_checks if check is not near_wall)
        assert sorted(alert.key for alert in preview.alerts) == [
            "hvl_move_24h", "net_gex_turned_positive", "put_support_move_24h",
            "spot_crossed_above_hvl"]
        assert preview.urgent_alert_html is not None
        assert preview.urgent_alert_html.startswith("<b>URGENT | BTC MenthorQ-style levels</b>")

    def test_brief_is_the_formatter_text_with_the_preview_title(self):
        _, preview = _preview(moving_records())
        assert preview.brief_html.startswith(
            f"<b>BTC MenthorQ-style levels | {PREVIEW_TITLE}</b>")
        assert "Call Resistance  110,000" in preview.brief_html
        assert "Close 11-05" in preview.brief_html          # daily close of the previous date
        assert preview.brief_length == telegram_length(preview.brief_html)
        assert preview.as_of_utc == BASE_TIME
        assert preview.daily_close is not None and preview.daily_close.mode == MODE_DAILY_CLOSE

    def test_point_in_time_instants_and_nothing_stored(self):
        monitor, _ = _preview(moving_records())
        assert monitor.asked == [
            (MODE_LIVE, BASE_TIME, None),
            (MODE_LIVE, BASE_TIME - timedelta(hours=1), None),
            (MODE_LIVE, BASE_TIME - timedelta(hours=24), None),
            (MODE_LIVE, BASE_TIME - timedelta(days=7), None),
            (MODE_DAILY_CLOSE, datetime(2026, 11, 5, 23, 59, tzinfo=timezone.utc),
             datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)),
        ]
        assert all(as_of <= BASE_TIME for _, as_of, _ in monitor.asked)
        repository = monitor.fake_repository
        assert repository.records == [] and repository.deliveries == []

    def test_info_rows(self):
        _, preview = _preview(moving_records())
        rows = dict(preview.info_rows)
        assert rows["Brief data time"] == "2026-11-06 09:10 UTC"
        assert rows["Snapshot captured"] == "2026-11-06 08:50 UTC"      # 20 min before
        assert rows["Snapshot older than data time by"] == "20 min"
        assert rows["Data age at Run (now - snapshot)"] == "2 h 20 min"  # 11:10 - 08:50
        assert rows["Index price"] == "100,000 (trade at 2026-11-06 09:09 UTC)"
        assert rows["Open interest from"] == "2026-11-06 08:50 UTC"
        assert rows["IV source"] == "mark_iv 900, rebuilt from mark_price 0, missing 0 (of 900 rows)"
        assert rows["Expiries"] == "27NOV26, 25DEC26, 29JAN27"
        assert rows["Daily-close column"] == "2026-11-05 (23:59 UTC, open interest 00:00 UTC)"
        assert any("Live mode" in warning for warning in preview.warnings)

    def test_stale_line_in_brief_and_warnings_follow_the_settings_limit(self):
        # Snapshot 08:50 is 20 min before the data time 09:10.
        _, fresh = _preview(moving_records(), replace(MenthorqMonitorSettings(), stale_snapshot_minutes=20))
        assert "DATA STALE" not in fresh.brief_html
        assert not any("DATA STALE" in warning for warning in fresh.warnings)
        _, stale = _preview(moving_records(), replace(MenthorqMonitorSettings(), stale_snapshot_minutes=19))
        warning = "snapshot is 20 min older than the data time; levels may be out of date."
        assert stale.brief_html.split("\n")[1] == f"<b>DATA STALE:</b> {warning}"
        assert stale.warnings[0] == f"DATA STALE: {warning}"

    def test_stale_record_previews_the_data_stale_alert_like_production(self):
        """Limit 19 min, snapshot 20 min before the data time: stale. The movement rules
        (HVL +4%, PS +6%, crossing, sign flip would all fire when fresh) are paused, and the
        preview shows the data_stale message the collector would send."""
        _, preview = _preview(moving_records(), replace(MenthorqMonitorSettings(), stale_snapshot_minutes=19))
        assert [alert.key for alert in preview.alerts] == ["data_stale"]
        assert not any(check.would_fire for check in preview.alert_checks)
        rows = {check.rule: check for check in preview.alert_checks}
        assert rows["HVL move vs 24h ago"].current_value == \
            "+4.00% of spot (+4,000 USD) (paused: data stale)"
        assert preview.urgent_alert_html is not None
        assert preview.urgent_alert_html.split("\n")[0] == \
            "<b>URGENT | BTC MenthorQ monitor data is STALE</b>"
        assert "Snapshot is 20 min older than the data time" in preview.urgent_alert_html
        assert "Movement alerts are paused until fresh data arrives." in preview.urgent_alert_html

    def test_alerts_disabled_shows_enabled_no(self):
        settings = replace(MenthorqMonitorSettings(), alerts_enabled=False)
        _, preview = _preview(moving_records(), settings)
        assert not any(check.enabled for check in preview.alert_checks)

    def test_missing_data_is_not_computed_never_zero(self):
        _, preview = _preview({})
        assert preview.alerts == [] and preview.urgent_alert_html is None
        assert not any(check.would_fire for check in preview.alert_checks)
        assert all(check.current_value.startswith("not computed")
                   for check in preview.alert_checks)
        rows = dict(preview.info_rows)
        for label in ("Snapshot captured", "Snapshot older than data time by",
                      "Data age at Run (now - snapshot)", "Index price", "Open interest from",
                      "IV source", "Expiries", "Daily-close column"):
            assert rows[label] == "not computed", label
        assert preview.daily_close is None
        assert any("inputs not available" in warning for warning in preview.warnings)

    def test_naive_as_of_is_rejected(self):
        service = MenthorqBriefPreviewService(FixedRecordMonitorService({}))
        with pytest.raises(ValueError):
            service.build_preview(datetime(2026, 11, 6, 9, 10))


class TestHelpers:
    def test_plain_text_is_what_telegram_displays(self):
        text = "<b>BTC &amp; ETH</b>\n<i>note</i>\n<pre>a &lt; b &gt; c</pre>"
        assert telegram_html_to_plain_text(text) == "BTC & ETH\nnote\na < b > c"
        block = '<b>T</b>\n<pre><code class="language-text">a &lt; b\nc</code></pre>'
        assert telegram_html_to_plain_text(block) == "T\na < b\nc"
        assert telegram_html_to_plain_text(code_block("x & y")) == "x & y"

    def test_output_directory_is_dated(self):
        base = Path("outputs/gui")
        now = datetime(2026, 11, 7, 9, 15, 42, tzinfo=timezone.utc)
        assert monitor_output_directory(base, False, date(2026, 11, 6), now) == base / "2026-11-06"
        assert monitor_output_directory(base, True, date(2026, 11, 6), now) == \
            base / "live_20261107T0915Z"
        with pytest.raises(ValueError):
            monitor_output_directory(base, True, date(2026, 11, 6), datetime(2026, 11, 7))

    def test_generated_chart_files_in_display_order(self):
        output = {"charts": {"iv_times_open_interest": "a.png", "net_dex": "b.png"},
                  "per_expiry_iv_times_open_interest": [{"chart": "c.png"}, {"chart": "d.png"}]}
        assert generated_chart_files(output) == ["a.png", "b.png", "c.png", "d.png"]

    def test_daily_close_brief_instant(self):
        assert preview_as_of_for_daily_close(date(2026, 11, 6)) == datetime(
            2026, 11, 6, 23, 59, tzinfo=timezone.utc)

    def test_iv_source_summary(self):
        assert iv_source_summary({}) == "not computed"
        assert iv_source_summary({"mark_iv": 900, "rebuilt_from_mark_price": 3, "missing": 1}) == \
            "mark_iv 900, rebuilt from mark_price 3, missing 1 (of 904 rows)"
