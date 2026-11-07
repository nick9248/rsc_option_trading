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


def _listener(updates, owner=OWNER, with_unknown=True, menu=MENU):
    telegram = FakeTelegram(updates=updates)
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
    listener, telegram, calls = _listener([_update(1, "/brief")])
    telegram.accept_commands = False
    with caplog.at_level(logging.WARNING):
        assert listener.poll_once() == 1
    assert "command menu NOT registered" in caplog.text
    assert calls == [("brief", "")]
    listener.poll_once()
    assert len(telegram.registered_commands) == 1          # not retried every poll


def test_raising_menu_registration_never_stops_polling():
    listener, telegram, calls = _listener([_update(1, "/brief")])

    def broken(commands):
        raise RuntimeError("network")

    telegram.set_my_commands = broken
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
    assert telegram.get_updates_calls[0]["offset"] is None
    assert telegram.get_updates_calls[1]["offset"] == 43
    assert calls == [("brief", "")]           # not re-run on the second poll


def test_failed_get_updates_and_failing_handler_never_raise():
    telegram = FakeTelegram(fail_updates=True)
    assert TelegramCommandListener(telegram, OWNER, {}, clock=lambda: NOW).poll_once() == 0

    def broken(arguments):
        raise RuntimeError("boom")

    listener = TelegramCommandListener(FakeTelegram(updates=[_update(1, "/brief")]), OWNER,
                                       {"/brief": broken}, clock=lambda: NOW)
    assert listener.poll_once() == 1


def test_no_owner_configured_disables_commands():
    listener, telegram, calls = _listener([_update(1, "/brief")], owner="")
    assert listener.poll_once() == 0
    assert telegram.get_updates_calls == []
    assert telegram.registered_commands == []
