"""
Straddle scanner forward-testing harness (increment 2, Part 2).

Mirrors coding/service/on_chain/forward_testing_harness.py's record/resolve
pattern for the long-straddle scanner: each scan cycle records the best
candidate for every INCLUDED expiry into straddle_scan_history, and once an
expiry's calendar settlement date has passed, resolves its actual P&L
against the settlement price.

Settlement price source: ohlcv_history.close on the expiry's calendar date
(that column is stamped 08:00 UTC — exactly Deribit's daily settlement
instant) — the same source scripts/backtest_straddle_metrics.py uses for
its settlement lookup. If that row hasn't been collected yet, resolution is
simply deferred to the next cycle; this never raises.

NOTE (judgment call): settlement_return_pct is computed as
(pnl_usd / cost_usd) * 100 — a percent-scale value, matching every other
"_pct" column in this codebase (e.g. itm_call_oi_pct, iv_percentile). The
task spec wrote the formula as "pnl/cost" without the x100, but given the
column name and codebase convention, the x100 was treated as implied.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class StraddleForwardTestHarness:
    """
    Records straddle scan history and resolves settlement outcomes.

    Typical call sequence (called from the prospective collector after
    each straddle scan):

        harness = StraddleForwardTestHarness(repo)
        harness.record_scan(scan_result, scan_time=hour)
        harness.resolve_due(currency)
    """

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Args:
            repository: Injected DatabaseRepository (tests). Creates a
                default one if None.
        """
        self.repo = repository or DatabaseRepository()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_scan(
        self,
        scan_result: Dict[str, Any],
        scan_time: Optional[datetime] = None,
    ) -> int:
        """
        Record one straddle_scan_history row per INCLUDED expiry (never for
        entries in scan_result["excluded"]), using each expiry's best
        candidate fields.

        Args:
            scan_result: Output of StraddleScanService.scan().
            scan_time: Natural-key timestamp for this collection cycle —
                combined with (currency, expiration) for dedup, so calling
                this twice for the same cycle inserts nothing extra.
                Defaults to scan_result["as_of"] truncated to the hour,
                matching the truncated-hour convention already used by
                ForwardTestingHarness / the prospective collector.

        Returns:
            Count of NEW rows inserted (dedup skips are not counted).
        """
        currency = scan_result["currency"]
        index_price = scan_result["index_price"]
        scan_time = scan_time or self._truncate_to_hour(scan_result["as_of"])

        inserted = 0
        for entry in scan_result.get("expiries", []):
            best = entry.get("best")
            if not best:
                continue

            row = {
                "scan_time": scan_time,
                "currency": currency,
                "expiration": entry["expiry"],
                "dte": entry["dte"],
                "future_price": entry["F"],
                "index_price": index_price,
                "strike": best["strike"],
                "call_ask_usd": best.get("call_ask_usd"),
                "put_ask_usd": best.get("put_ask_usd"),
                "cost_usd": best["cost_usd"],
                "breakeven_down": best["breakeven_down"],
                "breakeven_up": best["breakeven_up"],
                "atm_iv": entry.get("atm_iv"),
                "iv_percentile": entry.get("iv_percentile"),
                "iv_percentile_n_obs": entry.get("iv_percentile_n_obs"),
                "iv_percentile_window_days": entry.get("iv_percentile_window_days"),
                "rv": entry.get("rv"),
                "rv_iv_ratio": entry.get("rv_iv_ratio"),
                "vrp": entry.get("vrp"),
                "min_pnl_score": best.get("min_pnl_score"),
                "deribit_url": best.get("deribit_url"),
            }
            try:
                if self.repo.save_straddle_scan(row):
                    inserted += 1
            except Exception as exc:
                logger.warning(
                    "StraddleForwardTestHarness.record_scan failed for %s %s: %s",
                    currency, entry.get("expiry"), exc,
                )

        return inserted

    def resolve_due(self, currency: str) -> int:
        """
        Resolve all unresolved straddle_scan_history rows for `currency`
        whose expiry's calendar settlement date has already passed.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").

        Returns:
            Count of rows resolved this call.
        """
        pending = self.repo.get_unresolved_straddle_scans(currency)
        if not pending:
            return 0

        now = datetime.now(timezone.utc)
        resolved_count = 0

        for scan in pending:
            settle_dt = self._parse_expiry_settlement(scan["expiration"])
            if settle_dt is None:
                logger.warning(
                    "StraddleForwardTestHarness: unparseable expiration '%s' "
                    "(scan id=%s), skipping", scan["expiration"], scan["id"],
                )
                continue
            if settle_dt > now:
                continue  # not due yet

            settlement_price = self._lookup_settlement_price(currency, settle_dt)
            if settlement_price is None:
                logger.info(
                    "StraddleForwardTestHarness: settlement price not yet "
                    "available for %s %s, will retry next cycle",
                    currency, scan["expiration"],
                )
                continue

            cost_usd = scan["cost_usd"]
            strike = scan["strike"]
            pnl_usd = abs(settlement_price - strike) - cost_usd
            return_pct = (pnl_usd / cost_usd * 100.0) if cost_usd else None

            try:
                self.repo.resolve_straddle_scan(
                    scan_id=scan["id"],
                    settlement_index_price=settlement_price,
                    settlement_pnl_usd=pnl_usd,
                    settlement_return_pct=return_pct,
                    resolved_at=now,
                )
                resolved_count += 1
                logger.info(
                    "StraddleForwardTestHarness: resolved %s %s -> "
                    "settlement=%.2f pnl=%.2f return=%s%%",
                    currency, scan["expiration"], settlement_price, pnl_usd,
                    f"{return_pct:.1f}" if return_pct is not None else "N/A",
                )
            except Exception as exc:
                logger.warning(
                    "StraddleForwardTestHarness: failed to resolve scan id=%s: %s",
                    scan["id"], exc,
                )

        return resolved_count

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_expiry_settlement(expiry: str) -> Optional[datetime]:
        """Deribit expiration string ('25SEP26') -> settlement datetime (08:00 UTC)."""
        try:
            expiry_date = datetime.strptime(expiry, "%d%b%y")
            return expiry_date.replace(hour=8, tzinfo=timezone.utc)
        except ValueError:
            return None

    def _lookup_settlement_price(self, currency: str, settle_dt: datetime) -> Optional[float]:
        """
        ohlcv_history.close on the expiry's calendar date — same approach as
        scripts/backtest_straddle_metrics.py's resolve_settlements(). Returns
        None (never raises) if that row hasn't been collected yet.
        """
        day_start = settle_dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        rows = self.repo.get_ohlcv_by_date_range(currency, day_start, day_end)
        if not rows:
            return None
        return rows[-1]["close"]

    @staticmethod
    def _truncate_to_hour(dt: datetime) -> datetime:
        return dt.replace(minute=0, second=0, microsecond=0)
