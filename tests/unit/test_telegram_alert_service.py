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
