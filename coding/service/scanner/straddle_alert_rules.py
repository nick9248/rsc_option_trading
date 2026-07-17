"""
Straddle scanner alert rule (increment 2, Part 3).

Decides whether the top-ranked INCLUDED expiry from a StraddleScanService
scan() result is worth sending a Telegram alert for, with rate limiting so
the same finding doesn't spam every collection cycle. Kept in its own file
(rather than a StraddleScanService method) so it's testable in isolation
against a fake repository, with no API/scan dependency.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class StraddleAlertRule:
    """
    Trigger + rate-limit rule for the straddle scanner's Telegram alert.

    Constants (tune here):
      ALERT_IV_PERCENTILE_THRESHOLD: only alert when the top-ranked
        included expiry's iv_percentile is at or below this value (default
        15.0). Matches the backtest-validated "cheap" signal — the
        cheapest IV-percentile quintile averaged +30% straddle return.
      ALERT_RATE_LIMIT_HOURS: don't resend for the same (currency,
        expiration) within this many hours of the last alert_sent=true row
        (default 24) — avoids alerting every collection cycle for a
        condition that hasn't materially changed.
      ALERT_IMPROVEMENT_MARGIN: exception to the rate limit — resend
        anyway if iv_percentile has dropped (improved / gotten cheaper) by
        at least this many percentage points versus the value at the last
        alert (default 5.0).
    """

    ALERT_IV_PERCENTILE_THRESHOLD = 15.0
    ALERT_RATE_LIMIT_HOURS = 24.0
    ALERT_IMPROVEMENT_MARGIN = 5.0

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Args:
            repository: Injected DatabaseRepository (tests). Creates a
                default one if None.
        """
        self.repo = repository or DatabaseRepository()

    def should_alert(
        self,
        scan_result: Dict[str, Any],
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Evaluate the trigger + rate-limit rule against a scan() result.

        Args:
            scan_result: Output of StraddleScanService.scan().

        Returns:
            (should_send, top_entry, reason). top_entry is the top-ranked
            included expiry's entry dict (or None if scan_result has no
            included expiries) — the caller uses it to know which expiry
            to mark alert_sent on. reason is a short human-readable string
            for logging.
        """
        expiries = scan_result.get("expiries", [])
        if not expiries:
            return False, None, "no included expiries"

        top = expiries[0]
        iv_pct = top.get("iv_percentile")
        currency = scan_result["currency"]

        if iv_pct is None or iv_pct > self.ALERT_IV_PERCENTILE_THRESHOLD:
            return False, top, (
                f"iv_percentile {iv_pct} above threshold {self.ALERT_IV_PERCENTILE_THRESHOLD}"
            )

        last = self.repo.get_last_alert_for_expiry(currency, top["expiry"])
        if last is None:
            return True, top, "no prior alert for this expiry"

        alert_sent_at = last["alert_sent_at"]
        hours_since = (datetime.now(timezone.utc) - alert_sent_at).total_seconds() / 3600.0
        if hours_since >= self.ALERT_RATE_LIMIT_HOURS:
            return True, top, f"last alert {hours_since:.1f}h ago >= rate limit {self.ALERT_RATE_LIMIT_HOURS}h"

        last_iv_pct = last.get("iv_percentile")
        improvement = (last_iv_pct - iv_pct) if last_iv_pct is not None else None
        if improvement is not None and improvement >= self.ALERT_IMPROVEMENT_MARGIN:
            return True, top, f"iv_percentile improved by {improvement:.1f}pt since last alert"

        return False, top, (
            f"already alerted {hours_since:.1f}h ago, no material improvement "
            f"(last={last_iv_pct}, now={iv_pct})"
        )
