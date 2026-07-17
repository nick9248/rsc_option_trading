"""
Unit tests for StraddleAlertRule (increment 2, Part 3 trigger/rate-limit logic).

FakeRepository stands in for DatabaseRepository -- no live DB.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from coding.service.scanner.straddle_alert_rules import StraddleAlertRule


class FakeRepository:
    def __init__(self, last_alert: Optional[Dict[str, Any]] = None):
        self._last_alert = last_alert

    def get_last_alert_for_expiry(self, currency: str, expiration: str) -> Optional[Dict[str, Any]]:
        return self._last_alert


def _scan_result(iv_percentile: Optional[float], expiry: str = "25SEP26") -> Dict[str, Any]:
    if iv_percentile is None:
        return {"currency": "BTC", "expiries": []}
    return {
        "currency": "BTC",
        "expiries": [{"expiry": expiry, "iv_percentile": iv_percentile, "dte": 68.0}],
    }


class TestNoExpiries:
    def test_no_included_expiries_never_alerts(self):
        rule = StraddleAlertRule(repository=FakeRepository())
        should_send, top, reason = rule.should_alert(_scan_result(None))
        assert should_send is False
        assert top is None


class TestThreshold:
    def test_above_threshold_does_not_alert(self):
        rule = StraddleAlertRule(repository=FakeRepository())
        should_send, top, reason = rule.should_alert(_scan_result(20.0))
        assert should_send is False
        assert top["iv_percentile"] == 20.0

    def test_at_threshold_alerts(self):
        rule = StraddleAlertRule(repository=FakeRepository())
        should_send, top, reason = rule.should_alert(_scan_result(rule.ALERT_IV_PERCENTILE_THRESHOLD))
        assert should_send is True

    def test_below_threshold_with_no_prior_alert_sends(self):
        rule = StraddleAlertRule(repository=FakeRepository(last_alert=None))
        should_send, top, reason = rule.should_alert(_scan_result(8.2))
        assert should_send is True
        assert top["expiry"] == "25SEP26"

    def test_null_percentile_never_alerts(self):
        rule = StraddleAlertRule(repository=FakeRepository())
        result = {"currency": "BTC", "expiries": [{"expiry": "25SEP26", "iv_percentile": None, "dte": 68.0}]}
        should_send, top, reason = rule.should_alert(result)
        assert should_send is False


class TestRateLimit:
    def test_recent_alert_no_improvement_skips(self):
        last_alert = {
            "iv_percentile": 8.0,
            "alert_sent_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        rule = StraddleAlertRule(repository=FakeRepository(last_alert=last_alert))
        should_send, top, reason = rule.should_alert(_scan_result(7.5))  # improved only 0.5pt
        assert should_send is False

    def test_recent_alert_but_past_rate_limit_window_sends(self):
        last_alert = {
            "iv_percentile": 8.0,
            "alert_sent_at": datetime.now(timezone.utc) - timedelta(hours=25),
        }
        rule = StraddleAlertRule(repository=FakeRepository(last_alert=last_alert))
        should_send, top, reason = rule.should_alert(_scan_result(7.5))
        assert should_send is True

    def test_material_improvement_within_window_sends(self):
        last_alert = {
            "iv_percentile": 10.0,
            "alert_sent_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        rule = StraddleAlertRule(repository=FakeRepository(last_alert=last_alert))
        # improved by exactly ALERT_IMPROVEMENT_MARGIN (5.0) -> 10.0 - 5.0 = 5.0
        should_send, top, reason = rule.should_alert(_scan_result(5.0))
        assert should_send is True

    def test_improvement_just_under_margin_skips(self):
        last_alert = {
            "iv_percentile": 10.0,
            "alert_sent_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        rule = StraddleAlertRule(repository=FakeRepository(last_alert=last_alert))
        should_send, top, reason = rule.should_alert(_scan_result(5.5))  # only 4.5pt improvement
        assert should_send is False

    def test_worse_percentile_within_window_skips(self):
        last_alert = {
            "iv_percentile": 5.0,
            "alert_sent_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        rule = StraddleAlertRule(repository=FakeRepository(last_alert=last_alert))
        should_send, top, reason = rule.should_alert(_scan_result(8.0))  # got worse (higher)
        assert should_send is False
