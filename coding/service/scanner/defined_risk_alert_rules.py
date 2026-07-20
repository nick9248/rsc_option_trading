"""
Alert rule + message formatting for the defined-risk (iron condor /
butterfly) scanners. One class parametrized by structure_type rather than
two near-duplicate classes, matching StraddleAlertRule's trigger/rate-limit
shape but keyed on RV-implied EV instead of iv_percentile.

The message header carries the gate_pass label so the Telegram feed itself
is the live A/B signal agreed during brainstorming: once enough alerts
resolve, query defined_risk_scan_history grouped by gate_pass to see which
bucket actually performed better.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)

STRUCTURE_LABELS = {"iron_condor": "IRON CONDOR SCANNER", "butterfly": "LONG BUTTERFLY SCANNER"}


class DefinedRiskAlertRule:
    """
    Trigger + rate-limit rule shared by both defined-risk scanners.

    Constants:
      ALERT_RATE_LIMIT_HOURS: don't resend for the same (currency,
        expiration, structure_type) within this many hours of the last
        alert (default 24).
      ALERT_IMPROVEMENT_MARGIN: exception to the rate limit -- resend if
        EV has improved by at least this many dollars since the last alert
        (default 50.0).
    """

    ALERT_RATE_LIMIT_HOURS = 24.0
    ALERT_IMPROVEMENT_MARGIN = 50.0

    def __init__(self, structure_type: str, repository: Optional[DatabaseRepository] = None):
        self.structure_type = structure_type
        self.repo = repository or DatabaseRepository()

    def should_alert(self, scan_result: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Trigger: top-ranked included expiry's best candidate has positive
        RV-implied EV. Rate-limited per (currency, expiration, structure_type).

        Returns:
            (should_send, top_entry, reason).
        """
        expiries = scan_result.get("expiries", [])
        if not expiries:
            return False, None, "no included expiries"

        top = expiries[0]
        ev = top["best"].get("ev")
        currency = scan_result["currency"]

        if ev is None or ev <= 0:
            return False, top, f"ev {ev} not positive"

        last = self.repo.get_last_alert_for_defined_risk(currency, top["expiry"], self.structure_type)
        if last is None:
            return True, top, "no prior alert for this expiry"

        alert_sent_at = last["alert_sent_at"]
        hours_since = (datetime.now(timezone.utc) - alert_sent_at).total_seconds() / 3600.0
        if hours_since >= self.ALERT_RATE_LIMIT_HOURS:
            return True, top, f"last alert {hours_since:.1f}h ago >= rate limit {self.ALERT_RATE_LIMIT_HOURS}h"

        last_ev = last.get("ev")
        improvement = (ev - last_ev) if last_ev is not None else None
        if improvement is not None and improvement >= self.ALERT_IMPROVEMENT_MARGIN:
            return True, top, f"ev improved by ${improvement:.2f} since last alert"

        return False, top, f"already alerted {hours_since:.1f}h ago, no material improvement"


def format_defined_risk_alert(scan_result: Dict[str, Any], structure_type: str) -> str:
    """Plain-text Telegram alert body. Never raises on missing data -- returns a short message instead."""
    label = STRUCTURE_LABELS.get(structure_type, structure_type.upper())
    currency = scan_result["currency"]
    as_of: datetime = scan_result["as_of"]
    index_price = scan_result["index_price"]
    expiries = scan_result.get("expiries", [])

    if not expiries:
        return f"{label} — {currency}\nNo qualifying candidates found."

    top = expiries[0]
    best = top["best"]
    regime = top.get("regime", {}) or {}
    gate_pass = regime.get("gate_pass")
    header = f"{label} — CALM-REGIME MATCH — {currency}" if gate_pass else f"{label} — {currency}"

    lines = [
        header,
        f"as of {as_of.strftime('%Y-%m-%d %H:%M UTC')} | index ${index_price:,.2f}",
        "",
        f"BEST: {top['expiry']} ({top['dte']:.0f}d)",
    ]
    if structure_type == "iron_condor":
        lines.append(f"  Short {best['short_call']:.0f}C / {best['short_put']:.0f}P, "
                      f"Long {best['long_call']:.0f}C / {best['long_put']:.0f}P")
        lines.append(f"  Credit: ${best['cost_or_credit']:,.2f}  (max loss ${best['max_loss']:,.2f})")
    else:
        lines.append(f"  Wings: {best['k1']:.0f} / {best['k2']:.0f} / {best['k3']:.0f}")
        lines.append(f"  Cost: ${best['cost_or_credit']:,.2f}  (max profit ${best['max_profit']:,.2f})")

    lines.append(f"  Breakevens: {best['breakeven_lo']:,.0f} / {best['breakeven_hi']:,.0f}")
    prob_str = f"{best['prob_profit']:.1f}%" if best.get("prob_profit") is not None else "N/A"
    ev_str = f"${best['ev']:,.2f}" if best.get("ev") is not None else "N/A"
    lines.append(f"  Probability of profit (RV-implied): {prob_str}  |  EV: {ev_str}")
    lines.append("")
    lines.append(f"  Regime: net_gex={regime.get('net_gex')}  RV10/RV30={regime.get('rv_ratio')}  "
                  f"gate_pass={gate_pass}")
    lines.append("")
    lines.append(f"Chart: {best.get('deribit_url', '')}")

    return "\n".join(lines)
