"""
Unit tests for TelegramAlertService. requests.post, requests.get and
requests.Session are mocked -- no real network call, no real Telegram send.
"""

from unittest.mock import MagicMock, patch

import requests

from coding.service.notifications.telegram_alert_service import (
    TelegramAlertService,
    get_updates_timeouts,
    get_updates_worst_case_seconds,
)


class TestSendSuccess:
    def test_send_returns_true_on_200(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        mock_response = MagicMock(ok=True, status_code=200)
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   return_value=mock_response) as mock_post:
            result = service.send("hello world")

        assert result is True
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.telegram.org/botabc123/sendMessage"
        assert kwargs["json"] == {"chat_id": "999", "text": "hello world"}
        assert kwargs["timeout"] == 10


class TestParseModeAndUpdates:
    def test_parse_mode_is_sent_only_when_given(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   return_value=MagicMock(ok=True)) as mock_post:
            service.send("<b>x</b>", parse_mode="HTML")
        assert mock_post.call_args[1]["json"] == {"chat_id": "999", "text": "<b>x</b>", "parse_mode": "HTML"}


def _updates_response(updates):
    response = MagicMock(ok=True)
    response.json.return_value = {"ok": True, "result": updates}
    return response


class _SessionFactory:
    """Stands in for requests.Session: every session's get answers from the shared ``outcomes``."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sessions = []

    def __call__(self):
        session = MagicMock()
        session.get.side_effect = self._next_outcome
        self.sessions.append(session)
        return session

    def _next_outcome(self, *arguments, **keyword_arguments):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


SESSION_PATH = "coding.service.notifications.telegram_alert_service.requests.Session"


class TestGetUpdates:
    def test_returns_the_result_list_and_passes_the_offset_and_a_connect_read_timeout(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        factory = _SessionFactory([_updates_response([{"update_id": 5}])])
        with patch(SESSION_PATH, factory):
            assert service.get_updates(offset=5, timeout_seconds=5) == [{"update_id": 5}]
        args, kwargs = factory.sessions[0].get.call_args
        assert args[0] == "https://api.telegram.org/botabc123/getUpdates"
        assert kwargs["params"]["offset"] == 5 and kwargs["params"]["timeout"] == 5
        assert kwargs["timeout"] == (4, 8)            # connect 4 s, read = long poll 5 + 3 s

    def test_the_timeout_arithmetic_of_one_poll(self):
        assert get_updates_timeouts(5) == (4, 8)
        assert get_updates_timeouts(0) == (4, 3)      # the start-up skip (no wait)
        assert get_updates_worst_case_seconds(5) == 12

    def test_one_session_is_reused_and_a_new_one_opened_after_a_network_error(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        factory = _SessionFactory([_updates_response([]), _updates_response([]),
                                   requests.ReadTimeout("read timed out"), _updates_response([])])
        with patch(SESSION_PATH, factory):
            assert service.get_updates(offset=None, timeout_seconds=5) == []
            assert service.get_updates(offset=None, timeout_seconds=5) == []
            assert len(factory.sessions) == 1 and factory.sessions[0].get.call_count == 2
            assert service.get_updates(offset=None, timeout_seconds=5) is None
            factory.sessions[0].close.assert_called_once()
            assert service.get_updates(offset=None, timeout_seconds=5) == []
        assert len(factory.sessions) == 2 and factory.sessions[1].get.call_count == 1

    def test_an_http_error_status_keeps_the_session(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        factory = _SessionFactory([MagicMock(ok=False, status_code=409, text="Conflict"),
                                   _updates_response([])])
        with patch(SESSION_PATH, factory):
            assert service.get_updates(offset=None, timeout_seconds=5) is None
            assert service.get_updates(offset=None, timeout_seconds=5) == []
        assert len(factory.sessions) == 1

    def test_a_timeout_is_info_a_first_http_status_or_malformed_body_is_a_warning(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        malformed = MagicMock(ok=True)
        malformed.json.side_effect = ValueError("not JSON")
        malformed.text = "<html>"
        not_ok = MagicMock(ok=True)
        not_ok.json.return_value = {"ok": False}
        factory = _SessionFactory([
            requests.ConnectTimeout("url: /botsecret-token/getUpdates"),
            requests.ConnectionError("url: /botsecret-token/getUpdates refused"),
            _updates_response([]),                       # resets: the next two are first occurrences
            MagicMock(ok=False, status_code=409, text="Conflict"),
            malformed, not_ok,
        ])
        with caplog.at_level("INFO"), patch(SESSION_PATH, factory):
            for _ in range(6):
                service.get_updates(offset=None, timeout_seconds=5)
        levels = [(record.levelname, record.getMessage()) for record in caplog.records]
        assert levels[0] == ("INFO", "TelegramAlertService: getUpdates timed out, will retry")
        assert levels[1][0] == "INFO" and "getUpdates failed, will retry" in levels[1][1]
        assert levels[2] == ("WARNING", "TelegramAlertService: getUpdates returned 409: Conflict")
        assert levels[3] == ("WARNING", "TelegramAlertService: unexpected getUpdates body: <html>")
        # A second malformed body is a repeat of the same kind: INFO.
        assert levels[4] == ("INFO", "TelegramAlertService: unexpected getUpdates body: {'ok': False} "
                                     "(occurrence 2 since the last successful poll)")
        assert "secret-token" not in caplog.text
        assert "/bot<bot token>/getUpdates refused" in caplog.text

    def test_the_3rd_consecutive_timeout_warns_then_every_20th_and_a_success_resets(self, caplog):
        service = TelegramAlertService(token="abc123", chat_id="999")
        timeouts = [requests.ReadTimeout("read timed out")] * 43
        factory = _SessionFactory(timeouts[:2] + [_updates_response([])] + timeouts)
        with caplog.at_level("INFO"), patch(SESSION_PATH, factory):
            for _ in range(2):
                service.get_updates(offset=None, timeout_seconds=5)
            assert service.get_updates(offset=None, timeout_seconds=5) == []     # resets the count
            for _ in range(43):
                service.get_updates(offset=None, timeout_seconds=5)
        warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
        # 2 failures, a success, then 43 failures: warnings at the 3rd, 23rd and 43rd of the new run.
        assert warnings == [
            f"TelegramAlertService: getUpdates timed out, will retry ({count} consecutive getUpdates failures)"
            for count in (3, 23, 43)]
        assert sum(record.levelname == "INFO" for record in caplog.records) == 2 + 40

    def test_a_repeated_http_status_warns_first_then_every_20th_per_status(self, caplog):
        service = TelegramAlertService(token="abc123", chat_id="999")
        conflicts = [MagicMock(ok=False, status_code=409, text="Conflict")] * 41
        unauthorized = MagicMock(ok=False, status_code=401, text="Unauthorized")
        factory = _SessionFactory(conflicts[:5] + [unauthorized] + conflicts[5:]
                                  + [_updates_response([]), conflicts[0]])
        with caplog.at_level("INFO"), patch(SESSION_PATH, factory):
            for _ in range(44):     # 41 conflicts with one 401 among them, a success, one conflict
                service.get_updates(offset=None, timeout_seconds=5)
        warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
        assert warnings == [
            "TelegramAlertService: getUpdates returned 409: Conflict",
            "TelegramAlertService: getUpdates returned 401: Unauthorized",   # another status: its own first
            "TelegramAlertService: getUpdates returned 409: Conflict (occurrence 21 since the last "
            "successful poll)",
            "TelegramAlertService: getUpdates returned 409: Conflict (occurrence 41 since the last "
            "successful poll)",
            "TelegramAlertService: getUpdates returned 409: Conflict",       # after a success: first again
        ]

    def test_missing_token_asks_nothing(self):
        service = TelegramAlertService(token="explicit-token", chat_id="999")
        service._token = ""
        with patch(SESSION_PATH) as session_class:
            assert service.get_updates(offset=None, timeout_seconds=5) is None
        session_class.assert_not_called()


class TestSetMyCommands:
    def test_command_menu_is_posted_as_bot_command_list(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   return_value=MagicMock(ok=True)) as mock_post:
            assert service.set_my_commands([("brief", "Full brief"), ("mute", "Silence alerts")]) is True
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.telegram.org/botabc123/setMyCommands"
        assert kwargs["json"] == {"commands": [{"command": "brief", "description": "Full brief"},
                                               {"command": "mute", "description": "Silence alerts"}]}
        assert kwargs["timeout"] == 10

    def test_failures_return_false_never_raise_and_hide_the_token(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   side_effect=ConnectionError("url: /botsecret-token/setMyCommands")):
            assert service.set_my_commands([("help", "List")]) is False
        assert "secret-token" not in caplog.text
        rejected = MagicMock(ok=False, status_code=400, text="Bad Request: BOT_COMMAND_INVALID")
        with patch("coding.service.notifications.telegram_alert_service.requests.post", return_value=rejected):
            assert service.set_my_commands([("Help!", "x")]) is False

    def test_missing_token_skips_the_call(self):
        service = TelegramAlertService(token="", chat_id="999")
        service._token = ""
        with patch("coding.service.notifications.telegram_alert_service.requests.post") as mock_post:
            assert service.set_my_commands([("help", "List")]) is False
        mock_post.assert_not_called()


class TestSendFailurePaths:
    def test_non_200_response_returns_false(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        mock_response = MagicMock(ok=False, status_code=400, text="Bad Request")
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   return_value=mock_response):
            result = service.send("hello")
        assert result is False

    def test_network_exception_returns_false_not_raises(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   side_effect=ConnectionError("network down")):
            result = service.send("hello")
        assert result is False

    def test_network_exception_text_never_logs_the_token(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        error_text = ("HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded "
                      "with url: /botsecret-token/sendMessage")
        with caplog.at_level("WARNING"),              patch("coding.service.notifications.telegram_alert_service.requests.post",
                   side_effect=ConnectionError(error_text)):
            assert service.send("hello") is False
        assert "secret-token" not in caplog.text
        assert "/bot<bot token>/sendMessage" in caplog.text

    def test_missing_credentials_returns_false_without_network_call(self):
        with patch("coding.service.notifications.telegram_alert_service.load_dotenv"), \
             patch("coding.service.notifications.telegram_alert_service.os.getenv", return_value=""):
            service = TelegramAlertService(token="", chat_id="")

        with patch("coding.service.notifications.telegram_alert_service.requests.post") as mock_post:
            result = service.send("hello")

        assert result is False
        mock_post.assert_not_called()

    def test_missing_token_only_returns_false(self):
        service = TelegramAlertService(token="", chat_id="999")
        with patch("coding.service.notifications.telegram_alert_service.load_dotenv"), \
             patch("coding.service.notifications.telegram_alert_service.os.getenv", return_value=""):
            result = service.send("hello")
        assert result is False


class TestEnvFallback:
    def test_reads_env_vars_when_not_passed_explicitly(self):
        def fake_getenv(name, default=""):
            return {
                "OSF_TELEGRAM_BOT_TOKEN": "env-token",
                "OSF_TELEGRAM_CHAT_ID": "env-chat",
            }.get(name, default)

        with patch("coding.service.notifications.telegram_alert_service.load_dotenv"), \
             patch("coding.service.notifications.telegram_alert_service.os.getenv", side_effect=fake_getenv):
            service = TelegramAlertService()

        mock_response = MagicMock(ok=True, status_code=200)
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   return_value=mock_response) as mock_post:
            service.send("hi")

        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["chat_id"] == "env-chat"
        assert "env-token" in mock_post.call_args[0][0]

    def test_explicit_args_take_priority_over_env(self):
        with patch("coding.service.notifications.telegram_alert_service.os.getenv") as mock_getenv:
            service = TelegramAlertService(token="explicit-token", chat_id="explicit-chat")
            mock_getenv.assert_not_called()

        mock_response = MagicMock(ok=True, status_code=200)
        with patch("coding.service.notifications.telegram_alert_service.requests.post",
                   return_value=mock_response) as mock_post:
            service.send("hi")

        assert "explicit-token" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["json"]["chat_id"] == "explicit-chat"


class TestGetBotUsername:
    def test_username_is_read_from_get_me_and_cached(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        response = MagicMock(ok=True)
        response.json.return_value = {"ok": True, "result": {"id": 1, "is_bot": True, "username": "MenthorBot"}}
        with patch("coding.service.notifications.telegram_alert_service.requests.get",
                   return_value=response) as mock_get:
            assert service.get_bot_username() == "MenthorBot"
            assert service.get_bot_username() == "MenthorBot"
        assert mock_get.call_count == 1
        assert mock_get.call_args[0][0] == "https://api.telegram.org/botabc123/getMe"

    def test_failures_return_none_are_not_cached_and_hide_the_token(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        with patch("coding.service.notifications.telegram_alert_service.requests.get",
                   side_effect=ConnectionError("url: /botsecret-token/getMe")):
            assert service.get_bot_username() is None
        assert "secret-token" not in caplog.text
        malformed = MagicMock(ok=True)
        malformed.json.return_value = {"ok": True, "result": {}}
        with patch("coding.service.notifications.telegram_alert_service.requests.get", return_value=malformed):
            assert service.get_bot_username() is None
        unauthorized = MagicMock(ok=False, status_code=401, text="Unauthorized")
        with patch("coding.service.notifications.telegram_alert_service.requests.get", return_value=unauthorized):
            assert service.get_bot_username() is None
        good = MagicMock(ok=True)
        good.json.return_value = {"ok": True, "result": {"username": "MenthorBot"}}
        with patch("coding.service.notifications.telegram_alert_service.requests.get", return_value=good):
            assert service.get_bot_username() == "MenthorBot"   # asked again after a failure

    def test_missing_token_asks_nothing(self):
        service = TelegramAlertService(token="explicit-token", chat_id="999")
        service._token = ""   # the missing-token case without reading .env
        with patch("coding.service.notifications.telegram_alert_service.requests.get") as mock_get:
            assert service.get_bot_username() is None
        mock_get.assert_not_called()
