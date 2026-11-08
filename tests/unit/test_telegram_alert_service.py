"""
Unit tests for TelegramAlertService. requests.post is mocked -- no real
network call, no real Telegram send.
"""

from unittest.mock import MagicMock, patch

from coding.service.scanner.telegram_alert_service import TelegramAlertService


class TestSendSuccess:
    def test_send_returns_true_on_200(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        mock_response = MagicMock(ok=True, status_code=200)
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
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
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   return_value=MagicMock(ok=True)) as mock_post:
            service.send("<b>x</b>", parse_mode="HTML")
        assert mock_post.call_args[1]["json"] == {"chat_id": "999", "text": "<b>x</b>", "parse_mode": "HTML"}

    def test_get_updates_returns_the_result_list_and_passes_the_offset(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        response = MagicMock(ok=True)
        response.json.return_value = {"ok": True, "result": [{"update_id": 5}]}
        with patch("coding.service.scanner.telegram_alert_service.requests.get",
                   return_value=response) as mock_get:
            assert service.get_updates(offset=5, timeout_seconds=10) == [{"update_id": 5}]
        args, kwargs = mock_get.call_args
        assert args[0] == "https://api.telegram.org/botabc123/getUpdates"
        assert kwargs["params"]["offset"] == 5 and kwargs["params"]["timeout"] == 10
        assert kwargs["timeout"] == 20

    def test_get_updates_failures_return_none_and_hide_the_token(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        with patch("coding.service.scanner.telegram_alert_service.requests.get",
                   side_effect=ConnectionError("url: /botsecret-token/getUpdates")):
            assert service.get_updates(offset=None, timeout_seconds=1) is None
        assert "secret-token" not in caplog.text
        conflict = MagicMock(ok=False, status_code=409, text="Conflict")
        with patch("coding.service.scanner.telegram_alert_service.requests.get", return_value=conflict):
            assert service.get_updates(offset=None, timeout_seconds=1) is None


class TestSetMyCommands:
    def test_command_menu_is_posted_as_bot_command_list(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   return_value=MagicMock(ok=True)) as mock_post:
            assert service.set_my_commands([("brief", "Full brief"), ("mute", "Silence alerts")]) is True
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.telegram.org/botabc123/setMyCommands"
        assert kwargs["json"] == {"commands": [{"command": "brief", "description": "Full brief"},
                                               {"command": "mute", "description": "Silence alerts"}]}
        assert kwargs["timeout"] == 10

    def test_failures_return_false_never_raise_and_hide_the_token(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   side_effect=ConnectionError("url: /botsecret-token/setMyCommands")):
            assert service.set_my_commands([("help", "List")]) is False
        assert "secret-token" not in caplog.text
        rejected = MagicMock(ok=False, status_code=400, text="Bad Request: BOT_COMMAND_INVALID")
        with patch("coding.service.scanner.telegram_alert_service.requests.post", return_value=rejected):
            assert service.set_my_commands([("Help!", "x")]) is False

    def test_missing_token_skips_the_call(self):
        service = TelegramAlertService(token="", chat_id="999")
        service._token = ""
        with patch("coding.service.scanner.telegram_alert_service.requests.post") as mock_post:
            assert service.set_my_commands([("help", "List")]) is False
        mock_post.assert_not_called()


class TestSendFailurePaths:
    def test_non_200_response_returns_false(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        mock_response = MagicMock(ok=False, status_code=400, text="Bad Request")
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   return_value=mock_response):
            result = service.send("hello")
        assert result is False

    def test_network_exception_returns_false_not_raises(self):
        service = TelegramAlertService(token="abc123", chat_id="999")
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   side_effect=ConnectionError("network down")):
            result = service.send("hello")
        assert result is False

    def test_network_exception_text_never_logs_the_token(self, caplog):
        service = TelegramAlertService(token="secret-token", chat_id="999")
        error_text = ("HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded "
                      "with url: /botsecret-token/sendMessage")
        with caplog.at_level("WARNING"),              patch("coding.service.scanner.telegram_alert_service.requests.post",
                   side_effect=ConnectionError(error_text)):
            assert service.send("hello") is False
        assert "secret-token" not in caplog.text
        assert "/bot<bot token>/sendMessage" in caplog.text

    def test_missing_credentials_returns_false_without_network_call(self):
        with patch("coding.service.scanner.telegram_alert_service.load_dotenv"), \
             patch("coding.service.scanner.telegram_alert_service.os.getenv", return_value=""):
            service = TelegramAlertService(token="", chat_id="")

        with patch("coding.service.scanner.telegram_alert_service.requests.post") as mock_post:
            result = service.send("hello")

        assert result is False
        mock_post.assert_not_called()

    def test_missing_token_only_returns_false(self):
        service = TelegramAlertService(token="", chat_id="999")
        with patch("coding.service.scanner.telegram_alert_service.load_dotenv"), \
             patch("coding.service.scanner.telegram_alert_service.os.getenv", return_value=""):
            result = service.send("hello")
        assert result is False


class TestEnvFallback:
    def test_reads_env_vars_when_not_passed_explicitly(self):
        def fake_getenv(name, default=""):
            return {
                "OSF_TELEGRAM_BOT_TOKEN": "env-token",
                "OSF_TELEGRAM_CHAT_ID": "env-chat",
            }.get(name, default)

        with patch("coding.service.scanner.telegram_alert_service.load_dotenv"), \
             patch("coding.service.scanner.telegram_alert_service.os.getenv", side_effect=fake_getenv):
            service = TelegramAlertService()

        mock_response = MagicMock(ok=True, status_code=200)
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   return_value=mock_response) as mock_post:
            service.send("hi")

        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["chat_id"] == "env-chat"
        assert "env-token" in mock_post.call_args[0][0]

    def test_explicit_args_take_priority_over_env(self):
        with patch("coding.service.scanner.telegram_alert_service.os.getenv") as mock_getenv:
            service = TelegramAlertService(token="explicit-token", chat_id="explicit-chat")
            mock_getenv.assert_not_called()

        mock_response = MagicMock(ok=True, status_code=200)
        with patch("coding.service.scanner.telegram_alert_service.requests.post",
                   return_value=mock_response) as mock_post:
            service.send("hi")

        assert "explicit-token" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["json"]["chat_id"] == "explicit-chat"
