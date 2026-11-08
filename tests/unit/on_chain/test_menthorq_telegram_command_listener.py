"""
Bot command polling: owner-only, stale commands skipped, arguments passed,
unknown-command hint, command-menu registration once and failure-tolerant,
offset confirmation, failures never raise. Telegram is a fake (nothing is
sent).
"""

import logging
from datetime import datetime, timedelta, timezone

from coding.service.on_chain.menthorq_monitor.telegram_command_listener import TelegramCommandListener
from tests.fakes.fake_menthorq_monitor import FakeTelegram

NOW = datetime(2026, 11, 6, 9, 10, tzinfo=timezone.utc)
OWNER = "111"
MENU = (("brief", "Full brief"), ("mute", "Silence alerts"))


def _update(update_id, text, chat_id=OWNER, sent=NOW - timedelta(seconds=5), username="owner"):
    return {"update_id": update_id,
            "message": {"text": text, "chat": {"id": int(chat_id)}, "from": {"username": username},
                        "date": int(sent.timestamp())}}


def _listener(updates, owner=OWNER, with_unknown=True, menu=MENU, started=True):
    """The listener and its fake Telegram. ``started``: the first poll (which skips
    what was pending at start) has already run on an empty queue, and ``updates``
    arrive after it; otherwise ``updates`` are queued before the first poll."""
    telegram = FakeTelegram(updates=[] if started else updates)
    calls = []
    listener = TelegramCommandListener(
        telegram, owner,
        {"/brief": lambda arguments: calls.append(("brief", arguments)),
         "/mute": lambda arguments: calls.append(("mute", arguments))},
        clock=lambda: NOW,
        unknown_command_handler=(lambda command: calls.append(("unknown", command)))
        if with_unknown else None,
        command_menu=menu,
    )
    if started:
        listener.poll_once()
        telegram.updates = list(updates)
        telegram.get_updates_calls.clear()
    return listener, telegram, calls


def test_owner_commands_run_with_their_arguments_and_bot_suffix_is_accepted():
    listener, _, calls = _listener([_update(5, "/brief"), _update(6, "/brief@MenthorBot now"),
                                    _update(7, "/MUTE  24h ")])
    assert listener.poll_once() == 3
    assert calls == [("brief", ""), ("brief", "now"), ("mute", "24h")]


def test_other_chat_is_rejected_and_logged_for_known_and_unknown_commands(caplog):
    listener, telegram, calls = _listener([_update(7, "/mute off", chat_id="999", username="stranger"),
                                           _update(8, "/start", chat_id="999", username="stranger")])
    with caplog.at_level(logging.WARNING):
        assert listener.poll_once() == 0
    assert calls == []
    assert "REJECTED /mute from chat 999 (user stranger)" in caplog.text
    assert "REJECTED /start from chat 999 (user stranger)" in caplog.text
    assert telegram.sent == []          # no reply to strangers


class TestBotSuffix:
    """The fake bot is @MenthorBot (getMe)."""

    def test_a_command_addressed_to_another_bot_is_ignored(self, caplog):
        caplog.set_level(logging.DEBUG)
        listener, _, calls = _listener([_update(5, "/mute@AnotherBot 24h"), _update(6, "/levelz@OtherBot")])
        assert listener.poll_once() == 0
        assert calls == []                       # neither run nor answered with the hint
        ignored = [record for record in caplog.records if "addressed to @AnotherBot" in record.message]
        assert len(ignored) == 1 and ignored[0].levelname == "DEBUG"

    def test_the_suffix_of_this_bot_is_accepted_without_case(self):
        listener, _, calls = _listener([_update(5, "/mute@menthorbot 24h")])
        assert listener.poll_once() == 1
        assert calls == [("mute", "24h")]

    def test_username_is_asked_once_and_cached(self):
        listener, telegram, _ = _listener([_update(5, "/brief")])
        listener.poll_once()
        listener.poll_once()
        assert telegram.get_bot_username_calls == 1

    def test_unknown_username_ignores_suffixed_commands_but_runs_plain_ones(self, caplog):
        telegram = FakeTelegram(updates=[])
        telegram.bot_username = None             # getMe fails
        calls = []
        listener = TelegramCommandListener(
            telegram, OWNER, {"/brief": lambda arguments: calls.append(("brief", arguments))},
            clock=lambda: NOW)
        listener.poll_once()                     # first poll: skips the (empty) backlog
        telegram.updates = [_update(5, "/brief@MenthorBot"), _update(6, "/brief")]
        assert listener.poll_once() == 1
        assert calls == [("brief", "")]
        assert "bot username unknown" in caplog.text
        # getMe works again: asked at the next poll, then the suffix is accepted
        telegram.bot_username = "MenthorBot"
        telegram.updates = [_update(7, "/brief@MenthorBot later")]
        assert listener.poll_once() == 1
        assert calls[-1] == ("brief", "later")


def test_stale_command_after_downtime_is_skipped_known_or_unknown():
    listener, _, calls = _listener([_update(8, "/brief", sent=NOW - timedelta(minutes=11)),
                                    _update(9, "/foo", sent=NOW - timedelta(minutes=11))])
    assert listener.poll_once() == 0
    assert calls == []


def test_unknown_owner_command_gets_the_hint_and_plain_text_is_ignored():
    listener, _, calls = _listener([_update(9, "/start"), _update(10, "hello"),
                                    _update(11, "/Levelz@MenthorBot x")])
    assert listener.poll_once() == 2
    assert calls == [("unknown", "/start"), ("unknown", "/levelz")]


def test_unknown_command_without_hint_handler_is_ignored():
    listener, _, calls = _listener([_update(9, "/start")], with_unknown=False)
    assert listener.poll_once() == 0
    assert calls == []


def test_command_menu_is_registered_once_at_the_first_poll():
    listener, telegram, _ = _listener([])
    listener.poll_once()
    listener.poll_once()
    assert telegram.registered_commands == [list(MENU)]


def test_failed_menu_registration_is_logged_and_polling_goes_on(caplog):
    listener, telegram, calls = _listener([], started=False)
    telegram.accept_commands = False
    with caplog.at_level(logging.WARNING):
        assert listener.poll_once() == 0
    telegram.updates = [_update(1, "/brief")]
    assert listener.poll_once() == 1
    assert "command menu NOT registered" in caplog.text
    assert calls == [("brief", "")]
    listener.poll_once()
    assert len(telegram.registered_commands) == 1          # not retried every poll


def test_raising_menu_registration_never_stops_polling(monkeypatch):
    listener, telegram, calls = _listener([], started=False)

    def broken(commands):
        raise RuntimeError("network")

    monkeypatch.setattr(telegram, "set_my_commands", broken)
    listener.poll_once()
    telegram.updates = [_update(1, "/brief")]
    assert listener.poll_once() == 1
    assert calls == [("brief", "")]


def test_empty_menu_registers_nothing():
    listener, telegram, _ = _listener([], menu=())
    listener.poll_once()
    assert telegram.registered_commands == []


def test_offset_confirms_processed_updates_on_the_next_poll():
    listener, telegram, calls = _listener([_update(41, "/brief"), _update(42, "hello")])
    listener.poll_once()
    listener.poll_once()
    assert telegram.get_updates_calls[0]["offset"] is None     # nothing was pending at start
    assert telegram.get_updates_calls[1]["offset"] == 43
    assert calls == [("brief", "")]           # not re-run on the second poll


def test_failed_get_updates_and_failing_handler_never_raise():
    telegram = FakeTelegram(fail_updates=True)
    assert TelegramCommandListener(telegram, OWNER, {}, clock=lambda: NOW).poll_once() == 0

    def broken(arguments):
        raise RuntimeError("boom")

    telegram = FakeTelegram()
    listener = TelegramCommandListener(telegram, OWNER, {"/brief": broken}, clock=lambda: NOW)
    listener.poll_once()
    telegram.updates = [_update(1, "/brief")]
    assert listener.poll_once() == 1


def test_no_owner_configured_disables_commands_with_one_warning(caplog):
    with caplog.at_level(logging.WARNING):
        listener, telegram, calls = _listener([_update(1, "/brief")], owner="", started=False)
        for _ in range(5):
            assert listener.poll_once() == 0
    assert telegram.get_updates_calls == []
    assert telegram.registered_commands == []
    assert calls == []
    assert caplog.text.count("no owner chat id configured") == 1


def test_first_poll_skips_what_was_pending_so_a_restart_does_not_rerun_a_command(caplog):
    """Before the restart /mute 24h (id 70) was handled; Telegram still holds it unconfirmed
    with a newer /brief (id 71) sent while down. Both are recent (under the 10 min age rule)."""
    pending = [_update(70, "/mute 24h", sent=NOW - timedelta(minutes=3)),
               _update(71, "/brief", sent=NOW - timedelta(minutes=1))]
    with caplog.at_level(logging.INFO):
        listener, telegram, calls = _listener(pending, started=False)
        assert listener.poll_once() == 0
    assert calls == []
    assert telegram.get_updates_calls[0] == {"offset": -1, "timeout_seconds": 0}
    assert telegram.get_updates_calls[1]["offset"] == 72                 # continues after 71
    assert "skipped the pending updates up to update_id 71" in caplog.text

    telegram.updates.append(_update(72, "/brief"))                      # sent after the start
    assert listener.poll_once() == 1
    assert calls == [("brief", "")]


def test_skip_that_fails_is_retried_and_nothing_runs_meanwhile():
    listener, telegram, calls = _listener([_update(5, "/brief")], started=False)
    telegram.fail_updates = True
    assert listener.poll_once() == 0
    telegram.fail_updates = False
    assert listener.poll_once() == 0                                    # the retry skips /brief
    assert calls == []
    assert [call["offset"] for call in telegram.get_updates_calls] == [-1, -1, 6]
