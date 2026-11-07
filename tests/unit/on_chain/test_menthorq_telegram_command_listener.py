"""
/brief command polling: owner-only, stale commands skipped, offset
confirmation, failures never raise. Telegram is a fake (nothing is sent).
"""

import logging
from datetime import datetime, timedelta, timezone

from coding.service.on_chain.menthorq_monitor.telegram_command_listener import TelegramCommandListener
from tests.fakes.fake_menthorq_monitor import FakeTelegram

NOW = datetime(2026, 11, 6, 9, 10, tzinfo=timezone.utc)
OWNER = "111"


def _update(update_id, text, chat_id=OWNER, sent=NOW - timedelta(seconds=5), username="owner"):
    return {"update_id": update_id,
            "message": {"text": text, "chat": {"id": int(chat_id)}, "from": {"username": username},
                        "date": int(sent.timestamp())}}


def _listener(updates, owner=OWNER):
    telegram = FakeTelegram(updates=updates)
    calls = []
    listener = TelegramCommandListener(telegram, owner, {"/brief": lambda: calls.append("brief")},
                                       clock=lambda: NOW)
    return listener, telegram, calls


def test_owner_brief_runs_and_bot_suffix_is_accepted():
    listener, _, calls = _listener([_update(5, "/brief"), _update(6, "/brief@MenthorBot now")])
    assert listener.poll_once() == 2
    assert calls == ["brief", "brief"]


def test_other_chat_is_rejected_and_logged(caplog):
    listener, telegram, calls = _listener([_update(7, "/brief", chat_id="999", username="stranger")])
    with caplog.at_level(logging.WARNING):
        assert listener.poll_once() == 0
    assert calls == []
    assert "REJECTED /brief from chat 999 (user stranger)" in caplog.text
    assert telegram.sent == []          # no reply to strangers


def test_stale_command_after_downtime_is_skipped():
    listener, _, calls = _listener([_update(8, "/brief", sent=NOW - timedelta(minutes=11))])
    assert listener.poll_once() == 0
    assert calls == []


def test_unknown_commands_and_plain_text_are_ignored():
    listener, _, calls = _listener([_update(9, "/start"), _update(10, "hello")])
    assert listener.poll_once() == 0
    assert calls == []


def test_offset_confirms_processed_updates_on_the_next_poll():
    listener, telegram, calls = _listener([_update(41, "/brief"), _update(42, "hello")])
    listener.poll_once()
    listener.poll_once()
    assert telegram.get_updates_calls[0]["offset"] is None
    assert telegram.get_updates_calls[1]["offset"] == 43
    assert calls == ["brief"]           # not re-run on the second poll


def test_failed_get_updates_and_failing_handler_never_raise():
    telegram = FakeTelegram(fail_updates=True)
    assert TelegramCommandListener(telegram, OWNER, {}, clock=lambda: NOW).poll_once() == 0

    def broken():
        raise RuntimeError("boom")

    listener = TelegramCommandListener(FakeTelegram(updates=[_update(1, "/brief")]), OWNER,
                                       {"/brief": broken}, clock=lambda: NOW)
    assert listener.poll_once() == 1


def test_no_owner_configured_disables_commands():
    listener, telegram, calls = _listener([_update(1, "/brief")], owner="")
    assert listener.poll_once() == 0
    assert telegram.get_updates_calls == []
