"""
Notification services (outgoing messages), independent of any one feature.
"""

from .telegram_alert_service import TelegramAlertService

__all__ = ["TelegramAlertService"]
