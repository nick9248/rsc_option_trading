"""
Replies of /levels, /changes, /status, /mute, /help and the unknown-command
hint, built from an in-memory repository and sent to a fake Telegram
(nothing is sent). Every expected text is worked out by hand from the round
numbers of ``make_record``: spot 100,000, Call Resistance 110,000, HVL
95,000, Put Support 90,000, total net GEX +5M, so 1% of spot = 1,000 USD.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from coding.core.analytics.menthorq_monitor.level_record import MODE_DAILY_CLOSE
from coding.service.on_chain.menthorq_monitor.bot_command_service import MenthorqBotCommandService
from coding.service.on_chain.menthorq_monitor.brief_delivery_service import MenthorqBriefDeliveryService
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    CODE_BLOCK_CLOSE,
    CODE_BLOCK_OPEN,
    TELEGRAM_MESSAGE_LIMIT,
    telegram_length,
)
from coding.service.on_chain.menthorq_monitor.command_formatter import (
    BOT_COMMANDS,
    MUTE_USAGE,
    format_age,
    format_changes_reply,
)
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from coding.service.on_chain.menthorq_monitor.telegram_command_listener import TelegramCommandListener
from tests.fakes.fake_menthorq_monitor import (
    BASE_TIME,
    FakeInputLoader,
    FakeMonitorRepository,
    FakeTelegram,
    make_record,
    make_record_without_levels,
)

NOW = BASE_TIME + timedelta(minutes=55)          # 2026-11-06 10:05 UTC; latest record 09:10
OWNER = "111"
ALLOWED_TAGS = {"b", "i", "pre", "code"}
OPEN, CLOSE = CODE_BLOCK_OPEN, CODE_BLOCK_CLOSE


class _TagChecker(HTMLParser):
    """Unknown or unbalanced tags of a Telegram HTML message."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.problems.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.problems.append(f"/{tag}")


def _valid(message: str) -> str:
    checker = _TagChecker()
    checker.feed(message)
    checker.close()
    assert checker.problems == [] and checker.stack == []
    assert telegram_length(message) < TELEGRAM_MESSAGE_LIMIT
    assert message.isascii()
    return message


def _service(records, settings=None):
    repository = FakeMonitorRepository(records)
    telegram = FakeTelegram()
    # The bot commands run only in the production configuration (both Telegram switches on).
    settings = settings or MenthorqMonitorSettings(alerts_enabled=True, commands_enabled=True)
    monitor = MenthorqMonitorService(repository=repository, settings=settings,
                                     input_loader=FakeInputLoader())
    clock = {"now": NOW}
    delivery = MenthorqBriefDeliveryService(monitor, telegram, clock=lambda: clock["now"])
    commands = MenthorqBotCommandService(monitor, delivery, telegram, clock=lambda: clock["now"])
    return commands, repository, telegram, clock


def _run(commands, command, arguments=""):
    commands.handlers()[command](arguments)
    message = commands.telegram.sent[-1]
    assert message["parse_mode"] == "HTML"
    return _valid(message["message"])


def test_levels_reply_by_hand():
    commands, _, _, _ = _service([make_record()])
    assert _run(commands, "/levels") == (
        "<b>BTC key levels</b> | live 2026-11-06 09:10 UTC\n"
        f"{OPEN}Level              Strike  vs spot      USD\n"
        "Call Resistance   110,000  +10.00%  +10,000\n"
        "HVL                95,000   -5.00%   -5,000\n"
        "Put Support        90,000  -10.00%  -10,000\n"
        f"Spot              100,000{CLOSE}\n"
        "POSITIVE gamma: spot 5,000 (5.00%) above HVL.\n"
        "Total net GEX +$5.00M per 1% move."
    )


def test_levels_reply_explains_regime_versus_net_gex_and_missing_values():
    # spot 100,000 below HVL 101,000 (negative regime) with positive net GEX +5M
    commands, _, _, _ = _service([make_record(hvl=101000.0, put_support=None)])
    message = _run(commands, "/levels")
    assert "Put Support           n/a      n/a      n/a" in message
    assert "NEGATIVE gamma: spot 1,000 (1.00%) below HVL." in message
    assert ("<i>Spot below HVL = negative-gamma regime; total net GEX (sum over all strikes) "
            "is positive. Different measures.</i>") in message


def test_levels_and_changes_without_a_record_say_so():
    commands, _, _, _ = _service([])
    assert _run(commands, "/levels") == "<b>BTC key levels</b>\nNo live record is stored yet."
    assert _run(commands, "/changes") == "<b>BTC changes</b>\nNo live record is stored yet."


def test_levels_and_changes_start_with_the_stale_line_beyond_the_configured_limit():
    # make_record: snapshot 20 min before the data time, spot trade 30 s before.
    stale = "<b>DATA STALE:</b> snapshot is 20 min older than the data time; levels may be out of date."
    # The reply is asked at the record's data time, so only the in-record age counts here.
    at_limit, _, _, clock = _service([make_record()], replace(MenthorqMonitorSettings(), stale_snapshot_minutes=20))
    clock["now"] = BASE_TIME
    assert "DATA STALE" not in _run(at_limit, "/levels")
    assert "DATA STALE" not in _run(at_limit, "/changes")
    beyond, _, _, clock = _service([make_record()], replace(MenthorqMonitorSettings(), stale_snapshot_minutes=19))
    clock["now"] = BASE_TIME
    for command, title in (("/levels", "key levels"), ("/changes", "changes")):
        lines = _run(beyond, command).split("\n")
        assert lines[0] == f"<b>BTC {title}</b> | live 2026-11-06 09:10 UTC"
        assert lines[1] == stale


def test_levels_and_changes_judge_a_stored_record_against_the_clock():
    # make_record: snapshot 08:50 UTC. At NOW (10:05) it is 75 min old: within the 120 min default.
    commands, _, _, clock = _service([make_record()])
    assert "DATA STALE" not in _run(commands, "/levels")
    # Collector down for 3 days: same stored record, clock 2026-11-09 10:05 -> 3 d 1 h 15 min.
    clock["now"] = NOW + timedelta(days=3)
    expected = ("<b>DATA STALE:</b> snapshot is 3 d 1 h 15 min older than now "
                "(2026-11-09 10:05 UTC); levels may be out of date.")
    for command in ("/levels", "/changes"):
        assert _run(commands, command).split("\n")[1] == expected


def test_on_demand_brief_judges_a_stored_record_against_the_clock():
    commands, _, telegram, clock = _service([make_record()])
    clock["now"] = NOW + timedelta(days=3)
    commands.handlers()["/brief"]("")
    assert ("<b>DATA STALE:</b> snapshot is 3 d 1 h 15 min older than now (2026-11-09 10:05 UTC)"
            in telegram.sent[-1]["message"])


def _changes_history():
    latest = make_record(expirations=["27NOV26", "25DEC26", "29JAN27"])
    previous = make_record(as_of=BASE_TIME - timedelta(hours=1), hvl=94000.0, total_net_gex=4e6)
    # 6NOV26 settles 2026-11-06 08:00 UTC, inside (2026-11-05 09:10, 2026-11-06 09:10]
    day_ago = make_record(as_of=BASE_TIME - timedelta(hours=24), hvl=93000.0, total_net_gex=6e6,
                          expirations=["6NOV26", "27NOV26", "25DEC26", "29JAN27"])
    return [day_ago, previous, latest]


def test_changes_reply_by_hand_with_settled_expiry_label():
    commands, _, _, _ = _service(_changes_history())
    # previous: HVL 94,000 -> 95,000 = +1,000 = +1.00% of spot; GEX 4M -> 5M = +1M
    # 24h: HVL 93,000 -> 95,000 = +2,000 = +2.00%; GEX 6M -> 5M = -1M; 6NOV26 settled
    assert _run(commands, "/changes") == (
        "<b>BTC changes</b> | live 2026-11-06 09:10 UTC\n"
        "<i>USD; level % = % of current spot.</i>\n"
        f"{OPEN}vs previous run (2026-11-06 08:10 UTC)\n"
        "  Spot    0 (+0.00%)\n"
        "  CR      0\n"
        "  HVL     +1,000 (+1.00% of spot)\n"
        "  PS      0\n"
        f"  Net GEX +$1.00M{CLOSE}\n"
        f"{OPEN}vs 24h ago (2026-11-05 09:10 UTC)\n"
        "  Spot    0 (+0.00%)\n"
        "  CR      0\n"
        "  HVL     +2,000 (+2.00% of spot)\n"
        "  PS      0\n"
        "  Net GEX -$1.00M\n"
        f"  Settled: 6NOV26{CLOSE}\n"
        "vs 7d ago: no stored record\n"
        "<b>Events and expiry roll-offs</b>\n"
        "- 6NOV26 settled since the 24h record; the moves of HVL coincide with this roll-off "
        "(its open interest left the sum)."
    )


def test_changes_reply_is_shortened_below_the_limit():
    latest = make_record()
    commands, _, _, _ = _service(_changes_history())
    stored_latest = commands.monitor_service.latest_record("live", NOW)
    assert stored_latest is not None
    comparisons = commands.monitor_service.build_comparisons(stored_latest)
    for comparison in comparisons:
        if comparison.reference is not None:
            comparison.new_expiries = [f"{day}MAR27-" + "x" * 280 for day in range(1, 20)]
            comparison.hvl_crossing = "crossed_below_hvl"
            comparison.net_gex_sign_flipped = True
    full = format_changes_reply("BTC", latest, comparisons)
    assert telegram_length(full) > 1300                     # 7 events, two of 300 characters
    message = format_changes_reply("BTC", latest, comparisons, limit=1300)
    assert telegram_length(message) <= 1300
    assert "- ... and 4 more" in message
    _valid(message)
    tiny = format_changes_reply("BTC", latest, comparisons, limit=900)
    assert telegram_length(tiny) <= 900
    assert "- ... and 7 more" in tiny


def _status_records():
    live = make_record()                                     # as_of = computed 09:10, snapshot 08:50
    daily_close = make_record(as_of=datetime(2026, 11, 5, 23, 59, tzinfo=timezone.utc),
                              mode=MODE_DAILY_CLOSE)
    return [live, daily_close]


def test_status_reply_by_hand_healthy_not_muted():
    commands, _, _, _ = _service(_status_records())
    # now 10:05: live 55 min ago; daily close 23:59 -> 10 h 06 min = 10.1 h; snapshot 08:50 = 75 min old
    assert _run(commands, "/status") == (
        "<b>BTC MenthorQ monitor status</b> | 2026-11-06 10:05 UTC\n"
        "Health: OK\n"
        "Live run: 2026-11-06 09:10 UTC (55 min ago), computed\n"
        "Daily close: 2026-11-05 23:59 UTC (10.1 h ago), computed\n"
        "Data age: snapshot 2026-11-06 08:50 UTC (75 min old), open interest 2026-11-06 08:50 UTC\n"
        "Hourly job ran in the last 2h: yes\n"
        "Urgent alerts: on, not muted\n"
        "Last alert delivered: none"
    )


def test_status_reply_muted_with_last_alert_and_held_back_alert():
    commands, repository, _, _ = _service(_status_records())
    repository.save_menthorq_alert_delivery("BTC", "hvl_move_24h", BASE_TIME + timedelta(minutes=1), "m")
    repository.save_menthorq_alert_delivery("BTC", "weekly_brief", BASE_TIME + timedelta(minutes=20), "b")
    repository.save_menthorq_alert_mute("BTC", NOW + timedelta(hours=24), NOW)
    repository.save_menthorq_suppressed_alerts("BTC", ["put_support_move_24h"], BASE_TIME + timedelta(minutes=50))
    message = _run(commands, "/status")
    assert "Urgent alerts: MUTED until 2026-11-07 10:05 UTC (24.0 h left)" in message
    # the weekly brief at 09:30 is not an alert; the newest alert is the 09:11 one
    assert "Last alert delivered: hvl_move_24h at 2026-11-06 09:11 UTC" in message
    assert "Last alert held back by the mute: put_support_move_24h at 2026-11-06 10:00 UTC" in message


def test_status_reply_reports_problems():
    stale_failed = make_record_without_levels(as_of=NOW - timedelta(hours=3), spot=None,
                                              snapshot_captured_at_utc=None,
                                              not_computed_reasons=["inputs not available: no snapshot"])
    commands, _, _, _ = _service([stale_failed])
    message = _run(commands, "/status")
    assert ("Health: PROBLEM: hourly job has not run for 3.0 h; latest live run not computed: "
            "inputs not available: no snapshot") in message
    assert "Live run: 2026-11-06 07:05 UTC (3.0 h ago), NOT computed" in message
    assert "Daily close: none stored" in message
    assert "Data age: n/a (no computed live run)" in message
    assert "Hourly job ran in the last 2h: NO" in message


def test_status_says_problem_when_the_latest_live_run_has_spot_but_no_levels():
    # Inputs loaded (spot, snapshot 20 min old, run 55 min ago) but no level computed.
    without_levels = make_record_without_levels(not_computed_reasons=["HVL not computed: no net GEX by strike"])
    commands, _, _, _ = _service([without_levels])
    message = _run(commands, "/status")
    assert "Health: PROBLEM: latest live run not computed: HVL not computed: no net GEX by strike" in message
    assert "Live run: 2026-11-06 09:10 UTC (55 min ago), NOT computed" in message


def test_status_reply_with_stale_snapshot_unreadable_state_and_alerts_off():
    old_snapshot = make_record(snapshot_captured_at_utc=BASE_TIME - timedelta(minutes=50))
    settings = MenthorqMonitorSettings(alerts_enabled=False, commands_enabled=True)
    commands, repository, _, _ = _service([old_snapshot], settings=settings)
    message = _run(commands, "/status")
    assert "Health: PROBLEM: snapshot 50 min older than the latest run" in message
    assert "Urgent alerts: OFF (MENTHORQ_ALERTS_ENABLED=0)" in message
    commands, repository, _, _ = _service([make_record()])
    repository.fail_state = True
    assert ("Urgent alerts: on; mute state not readable (RuntimeError), treated as not muted"
            in _run(commands, "/status"))


def test_status_with_no_run_at_all():
    commands, _, _, _ = _service([])
    message = _run(commands, "/status")
    assert "Health: PROBLEM: no live run stored" in message
    assert "Live run: none stored" in message


def test_mute_for_a_duration_persists_and_expires():
    commands, repository, _, clock = _service([make_record()])
    assert _run(commands, "/mute", "24h") == (
        "<b>BTC URGENT alerts muted</b> until 2026-11-07 10:05 UTC (24h).\n"
        "Command replies and the weekly brief are still sent. /mute off ends it early.\n"
        "A sign flip or HVL crossing during the mute is sent after it if the new state "
        "still holds; /brief and /levels show the current state.")
    assert repository.states["BTC"].alerts_muted_until == NOW + timedelta(hours=24)
    assert repository.states["BTC"].mute_updated_at == NOW

    # A service built again on the same storage (a restart) still sees the mute
    restarted, restarted_repository, _, _ = _service([])
    restarted_repository.states = repository.states
    assert restarted.delivery_service.alerts_muted(NOW + timedelta(hours=23, minutes=59)) is True
    assert restarted.delivery_service.alerts_muted(NOW + timedelta(hours=24)) is False   # ended on its own

    _run(commands, "/mute", "3D")
    assert repository.states["BTC"].alerts_muted_until == datetime(2026, 11, 9, 10, 5, tzinfo=timezone.utc)
    _run(commands, "/mute", "90m")
    assert repository.states["BTC"].alerts_muted_until == datetime(2026, 11, 6, 11, 35, tzinfo=timezone.utc)
    clock["now"] = NOW + timedelta(minutes=10)
    assert _run(commands, "/mute") == (
        "<b>BTC mute</b>\nURGENT alerts are muted until 2026-11-06 11:35 UTC.\n" + MUTE_USAGE)


def test_mute_off_and_mute_state_when_not_muted():
    commands, repository, _, _ = _service([])
    assert _run(commands, "/mute") == "<b>BTC mute</b>\nURGENT alerts are not muted.\n" + MUTE_USAGE
    _run(commands, "/mute", "24h")
    assert _run(commands, "/mute", "OFF") == (
        "<b>BTC URGENT alerts unmuted.</b> They are sent again from the next run.")
    assert repository.states["BTC"].alerts_muted_until is None
    assert commands.delivery_service.alerts_muted(NOW) is False


def test_mute_with_a_bad_argument_or_failed_save_changes_nothing():
    commands, repository, _, _ = _service([])
    assert _run(commands, "/mute", "soon") == (
        "<b>Mute not changed</b>: 'soon' is not a duration (use e.g. 24h, 3d, 90m or off)\n" + MUTE_USAGE)
    assert _run(commands, "/mute", "31d").startswith("<b>Mute not changed</b>: the longest mute is 30d")
    assert _run(commands, "/mute", "0h").startswith(
        "<b>Mute not changed</b>: the duration must be longer than zero")
    assert _run(commands, "/mute", "<b>").startswith("<b>Mute not changed</b>: '&lt;b&gt;' is not")
    assert repository.states == {}
    repository.fail_state = True
    assert _run(commands, "/mute", "24h") == (
        "<b>Mute not changed</b>: could not be saved (database error, see the collector log)\n"
        + MUTE_USAGE)


def test_help_reply_lists_commands_and_rule_states():
    commands, _, _, _ = _service([])
    assert _run(commands, "/help") == (
        "<b>BTC MenthorQ monitor commands</b>\n"
        "/brief - Full brief: levels, regime, changes, caveats\n"
        "/levels - Key levels and distance to spot\n"
        "/changes - Changes vs previous run, 24h and 7d\n"
        "/status - Last runs, data age, health, alert state\n"
        "/mute - Silence URGENT alerts: /mute 24h, /mute 3d, /mute off\n"
        "/help - List the commands and alert rules\n"
        "<b>Urgent alert rules</b>\n"
        "- HVL move &gt; 5% of spot vs 24h: ON\n"
        "- Call Resistance move &gt; 10% of spot vs 24h: ON\n"
        "- Put Support move &gt; 5% of spot vs 24h: ON\n"
        "- Total net GEX sign change: ON\n"
        "- Spot crosses HVL: OFF\n"
        "- Spot near Call Resistance / Put Support: OFF\n"
        "- Cooldown per alert: 12h (24h moves: 24h)"
    )


def test_bot_command_menu_matches_telegram_rules():
    # Telegram: 1-32 characters, lower-case letters, digits and underscores; description 1-256.
    for command, description in BOT_COMMANDS:
        assert 1 <= len(command) <= 32 and command == command.lower() and command.isidentifier()
        assert 1 <= len(description) <= 256
    commands, _, _, _ = _service([])
    assert set(commands.handlers()) == {f"/{command}" for command, _ in BOT_COMMANDS}


def test_unknown_command_reply_and_failed_reply(monkeypatch):
    commands, repository, telegram, _ = _service([])
    commands.reply_to_unknown_command("/start")
    assert _valid(telegram.sent[-1]["message"]) == (
        "Unknown command /start. Commands: /brief /levels /changes /status /mute /help. "
        "Send /help for details.")

    def broken(*arguments):
        raise RuntimeError("database down")

    monkeypatch.setattr(repository, "get_latest_menthorq_level_run", broken)
    assert _run(commands, "/status") == "/status failed (see the collector log)."


def test_brief_handler_sends_the_on_demand_brief():
    commands, repository, telegram, _ = _service([make_record()])
    commands.handlers()["/brief"]("")
    assert "On-demand brief" in telegram.sent[-1]["message"]
    assert [delivery["alert_key"] for delivery in repository.deliveries] == ["on_demand_brief"]


def test_brief_that_cannot_be_built_gets_the_failed_reply(monkeypatch):
    commands, repository, telegram, _ = _service([make_record()])

    def broken(*arguments):
        raise RuntimeError("database down")

    monkeypatch.setattr(repository, "get_menthorq_level_snapshot_at_or_before", broken)
    assert commands.handlers()["/brief"]("") is True
    assert telegram.sent[-1] == {"message": "/brief failed (see the collector log).", "parse_mode": "HTML"}
    assert repository.deliveries == []    # a failure line is not recorded as a delivered brief


def _owner_update(update_id, text, chat_id=OWNER):
    return {"update_id": update_id,
            "message": {"text": text, "chat": {"id": int(chat_id)},
                        "from": {"id": int(chat_id), "username": "u"},   # a private chat: user id = chat id
                        "date": int((NOW - timedelta(seconds=3)).timestamp())}}


def test_end_to_end_owner_only_through_the_listener():
    commands, repository, _, _ = _service([make_record()])
    telegram = commands.telegram
    assert isinstance(telegram, FakeTelegram)
    listener = TelegramCommandListener(telegram, OWNER, commands.handlers(), clock=lambda: NOW,
                                       unknown_command_handler=commands.reply_to_unknown_command,
                                       command_menu=BOT_COMMANDS)
    assert listener.poll_once() == 0          # start: nothing pending
    telegram.updates = [_owner_update(1, "/mute 24h", chat_id="999"), _owner_update(2, "/levels", chat_id="999"),
                        _owner_update(3, "/levels"), _owner_update(4, "/nope")]
    assert listener.poll_once() == 2
    assert repository.states == {}                                  # the stranger's /mute did nothing
    assert [sent["message"].split("\n")[0] for sent in telegram.sent] == [
        "<b>BTC key levels</b> | live 2026-11-06 09:10 UTC",
        "Unknown command /nope. Commands: /brief /levels /changes /status /mute /help. Send /help for details.",
    ]
    assert telegram.registered_commands == [list(BOT_COMMANDS)]


def test_format_age_by_hand():
    assert format_age(timedelta(minutes=55)) == "55 min"
    assert format_age(timedelta(minutes=119)) == "119 min"
    assert format_age(timedelta(hours=2)) == "2.0 h"
    assert format_age(timedelta(hours=47, minutes=54)) == "47.9 h"
    assert format_age(timedelta(days=3)) == "3.0 d"
