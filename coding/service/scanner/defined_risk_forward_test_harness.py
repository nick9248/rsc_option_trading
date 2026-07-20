"""
Defined-risk (iron condor / long butterfly) forward-test harness. Mirrors
StraddleForwardTestHarness's record/resolve pattern; shares settlement-
lookup logic via coding.service.scanner.settlement_lookup rather than
duplicating it (see that harness's own docstring for the general approach).

RETURN CONVENTION (do not regress): iron condor's capital at risk is
max_loss (a credit structure -- the credit is income, not capital
deployed); butterfly's capital at risk is cost_or_credit (a debit
structure). See defined_risk_candidate_builder.py's module docstring.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from coding.core.database.repository import DatabaseRepository
from coding.service.scanner.defined_risk_candidate_builder import (
    butterfly_payoff,
    iron_condor_payoff,
)
from coding.service.scanner.settlement_lookup import (
    lookup_settlement_price,
    parse_expiry_settlement,
)

logger = logging.getLogger(__name__)


class DefinedRiskForwardTestHarness:
    def __init__(self, repository: Optional[DatabaseRepository] = None):
        self.repo = repository or DatabaseRepository()

    def record_scan(
        self,
        scan_result: Dict[str, Any],
        structure_type: str,
        scan_time: Optional[datetime] = None,
    ) -> int:
        """
        Record one defined_risk_scan_history row per INCLUDED expiry, using
        each expiry's best candidate. Never records excluded expiries.

        Returns:
            Count of NEW rows inserted (dedup skips are not counted).
        """
        currency = scan_result["currency"]
        scan_time = scan_time or self._truncate_to_hour(scan_result["as_of"])

        inserted = 0
        for entry in scan_result.get("expiries", []):
            best = entry.get("best")
            if not best:
                continue
            regime = entry.get("regime", {}) or {}
            row = {
                "scan_time": scan_time, "currency": currency, "expiration": entry["expiry"],
                "structure_type": structure_type,
                "dte": entry["dte"], "future_price": entry["F"], "index_price": scan_result["index_price"],
                "short_call": best.get("short_call"), "long_call": best.get("long_call"),
                "short_put": best.get("short_put"), "long_put": best.get("long_put"),
                "k1": best.get("k1"), "k2": best.get("k2"), "k3": best.get("k3"),
                "cost_or_credit": best["cost_or_credit"], "max_loss": best.get("max_loss"),
                "max_profit": best.get("max_profit"),
                "breakeven_lo": best["breakeven_lo"], "breakeven_hi": best["breakeven_hi"],
                "prob_profit": best.get("prob_profit"), "ev": best.get("ev"),
                "net_gex": regime.get("net_gex"), "rv_10d": regime.get("rv_10d"),
                "rv_30d": regime.get("rv_30d"), "rv_ratio": regime.get("rv_ratio"),
                "gate_pass": regime.get("gate_pass"),
                "deribit_url": best.get("deribit_url"),
            }
            try:
                if self.repo.save_defined_risk_scan(row):
                    inserted += 1
            except Exception as exc:
                logger.warning(
                    "DefinedRiskForwardTestHarness.record_scan failed for %s %s %s: %s",
                    currency, entry.get("expiry"), structure_type, exc,
                )
        return inserted

    def resolve_due(self, currency: str, structure_type: str) -> int:
        """Resolve all unresolved rows for (currency, structure_type) whose expiry has passed."""
        pending = self.repo.get_unresolved_defined_risk_scans(currency, structure_type)
        if not pending:
            return 0

        now = datetime.now(timezone.utc)
        resolved_count = 0

        for scan in pending:
            settle_dt = parse_expiry_settlement(scan["expiration"])
            if settle_dt is None or settle_dt > now:
                continue

            settlement_price = lookup_settlement_price(self.repo, currency, settle_dt)
            if settlement_price is None:
                continue
            settlement_price = float(settlement_price)

            if structure_type == "iron_condor":
                candidate = {
                    "short_call": scan["short_call"], "long_call": scan["long_call"],
                    "short_put": scan["short_put"], "long_put": scan["long_put"],
                    "cost_or_credit": scan["cost_or_credit"],
                }
                pnl = iron_condor_payoff(candidate, settlement_price)
                capital_at_risk = scan["max_loss"]
            else:
                candidate = {"k1": scan["k1"], "k2": scan["k2"], "k3": scan["k3"], "cost_or_credit": scan["cost_or_credit"]}
                pnl = butterfly_payoff(candidate, settlement_price)
                capital_at_risk = scan["cost_or_credit"]

            return_pct = (pnl / capital_at_risk * 100.0) if capital_at_risk else None

            try:
                self.repo.resolve_defined_risk_scan(
                    scan_id=scan["id"], settlement_index_price=settlement_price,
                    settlement_pnl_usd=pnl, settlement_return_pct=return_pct, resolved_at=now,
                )
                resolved_count += 1
            except Exception as exc:
                logger.warning("DefinedRiskForwardTestHarness: failed to resolve scan id=%s: %s", scan["id"], exc)

        return resolved_count

    @staticmethod
    def _truncate_to_hour(dt: datetime) -> datetime:
        return dt.replace(minute=0, second=0, microsecond=0)
