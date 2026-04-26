from datetime import datetime, date, timezone
from unittest.mock import patch, MagicMock
import pytest

from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.service.displacement.telegram_alert_service import TelegramAlertService


def _make_signal():
    return DisplacementSignal(
        asset="BTC", detected_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
        drop_24h_pct=0.22, drop_1h_pct=0.09,
        conviction_pct=75.0, conviction_label="HIGH",
        score_drop_magnitude=82.0, score_drop_speed=55.0,
        score_funding_rate=91.0, score_dvol_spike=71.0,
        score_max_pain=64.0, score_term_structure=80.0,
        funding_rate_value=-0.008, dvol_sigma=2.1,
        max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
        instrument_name="BTC-25SEP26-70000-C", strike=70000.0,
        expiry_date=date(2026, 9, 25), dte=153,
        delta=0.14, mark_iv=0.87, premium_usd=1240.0,
        target_50pct_price=71860.0, target_100pct_price=72480.0, target_200pct_price=73720.0,
    )


class TestTelegramAlertService:
    def test_send_returns_true_on_success(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = True
            result = svc.send(_make_signal())
        assert result is True
        assert mock_post.called

    def test_send_returns_false_on_http_failure(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = False
            result = svc.send(_make_signal())
        assert result is False

    def test_send_returns_false_on_exception(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        with patch("requests.post", side_effect=ConnectionError("timeout")):
            result = svc.send(_make_signal())
        assert result is False

    def test_send_returns_false_when_not_configured(self):
        # Patch env to prevent real .env values from leaking into this test
        with patch.dict("os.environ", {}, clear=True):
            with patch("coding.service.displacement.telegram_alert_service.load_dotenv"):
                svc = TelegramAlertService(token="", chat_id="")
        result = svc.send(_make_signal())
        assert result is False

    def test_format_message_contains_key_fields(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        msg = svc._format_message(_make_signal())
        assert "BTC" in msg
        assert "22.0" in msg or "22" in msg   # drop percentage
        assert "75" in msg                     # conviction
        assert "BTC-25SEP26-70000-C" in msg
        assert "HIGH" in msg
        assert msg.count("DISPLACEMENT ALERT") == 1  # header must not repeat

    def test_posts_to_correct_url(self):
        svc = TelegramAlertService(token="abc123", chat_id="99")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = True
            svc.send(_make_signal())
        url = mock_post.call_args[0][0]
        assert "abc123" in url
        assert "sendMessage" in url

    def test_format_message_without_contract(self):
        # Signal with no contract fields — message should still format cleanly
        signal = DisplacementSignal(
            asset="ETH", detected_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
            drop_24h_pct=0.20, drop_1h_pct=0.08,
            conviction_pct=55.0, conviction_label="MEDIUM",
            score_drop_magnitude=70.0, score_drop_speed=40.0,
            score_funding_rate=60.0, score_dvol_spike=55.0,
            score_max_pain=45.0, score_term_structure=55.0,
            funding_rate_value=-0.003, dvol_sigma=1.8,
            max_pain_distance_pct=0.05, term_structure_inversion_pct=0.02,
        )
        svc = TelegramAlertService(token="t", chat_id="1")
        msg = svc._format_message(signal)
        assert "ETH" in msg
        assert "MEDIUM" in msg
        assert "BTC-25SEP26-70000-C" not in msg  # no contract section
