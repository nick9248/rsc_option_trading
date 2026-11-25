"""
Builds the facts of one expiry as of any instant, from stored data only (read only, point in time).

Nothing here looks at data that did not exist at ``as_of``: each stored hourly row is used only once it was written (or, for tables without a
write time, once its hour had ended), and a row older than three hours is treated as missing. The same code serves a live run (as_of = now,
after the sync) and a replay of any past instant.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List

from coding.core.analytics.realised_volatility import last_completed_candle_start, realised_volatility
from coding.core.analytics.strategy_pipeline.facts import MAX_SNAPSHOT_AGE_HOURS, FactsV1
from coding.core.analytics.menthorq_replication_calculator import parse_expiry_settlement
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class FactsAsOfService:
    """Reads the stored hourly analysis and market series as of an instant."""

    def __init__(self, repository: DatabaseRepository):
        """
        Args:
            repository: Local database (read only).
        """
        self.repository = repository

    def build(self, currency: str, expiration: str, as_of: datetime, spot: float, forward: Optional[float] = None) -> FactsV1:
        """
        The facts of one expiry.

        Args:
            currency: "BTC" or "ETH".
            expiration: Expiry label such as "27NOV26".
            as_of: The instant, timezone-aware UTC. Nothing newer is used.
            spot: Index price at ``as_of`` (the caller took it from the same snapshot as its quotes).
            forward: Forward of the expiry at ``as_of``, when known.

        Raises:
            ValueError: for a naive ``as_of`` or an expiry label that does not parse or is not after ``as_of``.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        settlement_at = parse_expiry_settlement(expiration)
        days = None if settlement_at is None else (settlement_at - as_of).total_seconds() / 86400.0          # fractional days to 08:00 UTC
        if days is None or days <= 0:
            raise ValueError(f"expiry {expiration!r} is not open at {as_of.isoformat()}")

        analysis = self._fresh(self.repository.get_onchain_analysis_snapshot_at_or_before(currency, expiration, as_of), as_of)
        volatility = self._fresh(self.repository.get_onchain_volatility_snapshot_at_or_before(currency, expiration, as_of), as_of)
        chain_volatility = self._fresh(self.repository.get_volatility_skew_at_or_before(currency, expiration, as_of), as_of) or {}
        put_25d, call_25d = chain_volatility.get("put_25d_iv"), chain_volatility.get("call_25d_iv")
        realised_10d, realised_30d = self._realised(currency, as_of, 10), self._realised(currency, as_of, 30)
        atm_iv = chain_volatility.get("atm_iv_interp")
        flow = self.repository.get_flow_delta_window(currency, expiration, as_of)
        if flow["newest_hour"] is None or as_of - flow["newest_hour"] > timedelta(hours=MAX_SNAPSHOT_AGE_HOURS + 1):
            flow = {**flow, "hours_present": 0, "hiro_usd": None, "premium_usd": None, "gross_delta_usd": None, "trade_count": None}  # stale
        dvol, dvol_percentile = self._dvol(currency, as_of)
        analysis = self._clean_analysis(analysis or {})
        volatility = self._without_empty_zeros(volatility or {}, ("pc_atm_ratio", "pc_near_otm_ratio", "pc_far_otm_ratio", "net_vanna",
                                                                  "net_charm", "expected_daily_move", "expected_weekly_move"))
        return FactsV1(
            currency=currency, expiration=expiration, as_of=as_of, spot=spot, days_to_expiry=days, forward=forward,
            dvol=dvol, dvol_percentile_365d=dvol_percentile,
            realised_vol_10d=realised_10d, realised_vol_30d=realised_30d,
            analysis_hour=analysis.get("snapshot_hour"), max_pain_strike=analysis.get("max_pain_strike"),
            put_call_ratio_oi=analysis.get("put_call_ratio_oi"), put_call_ratio_volume=analysis.get("put_call_ratio_volume"),
            total_call_oi=analysis.get("total_call_oi"), total_put_oi=analysis.get("total_put_oi"),
            total_net_gex_usd=analysis.get("total_net_gex"), total_net_dex=analysis.get("total_net_dex"),
            call_resistance_strike=analysis.get("call_resistance_strike"), put_support_strike=analysis.get("put_support_strike"),
            hvl_level=analysis.get("hvl_level"), itm_call_oi_pct=analysis.get("itm_call_oi_pct"), itm_put_oi_pct=analysis.get("itm_put_oi_pct"),
            chain_volatility_hour=chain_volatility.get("snapshot_hour"), atm_iv_pct=atm_iv,
            skew_25d=None if put_25d is None or call_25d is None else put_25d - call_25d, put_25d_iv_pct=put_25d, call_25d_iv_pct=call_25d,
            volatility_hour=volatility.get("snapshot_hour"),
            # computed here from the two numbers shown, so it cannot contradict them (the stored one averages the few traded instruments)
            vrp_absolute_points=None if atm_iv is None or realised_30d is None else atm_iv - realised_30d * 100.0,
            iv_percentile_365d=volatility.get("iv_percentile_365d"),
            expected_daily_move_usd=volatility.get("expected_daily_move"), expected_weekly_move_usd=volatility.get("expected_weekly_move"),
            pc_atm_ratio=volatility.get("pc_atm_ratio"), pc_near_otm_ratio=volatility.get("pc_near_otm_ratio"),
            pc_far_otm_ratio=volatility.get("pc_far_otm_ratio"), net_vanna=volatility.get("net_vanna"), net_charm=volatility.get("net_charm"),
            flow_hours=flow["hours_present"], flow_hiro_usd=flow["hiro_usd"], flow_premium_usd=flow["premium_usd"],
            flow_gross_delta_usd=flow["gross_delta_usd"], flow_trade_count=flow["trade_count"])

    @classmethod
    def _clean_analysis(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        The analysis row without the numbers the collector fills with 0 when there was nothing to compute: everything derived from open
        interest when the expiry has none, the volume ratio when nothing traded, and a net GEX of exactly 0.
        """
        cleaned = cls._without_empty_zeros(row, ("total_net_gex",))
        if (row.get("total_call_oi") or 0.0) + (row.get("total_put_oi") or 0.0) == 0.0:
            for column in ("put_call_ratio_oi", "total_net_gex", "total_net_dex", "max_pain_strike", "call_resistance_strike",
                           "put_support_strike", "hvl_level", "itm_call_oi_pct", "itm_put_oi_pct"):
                cleaned[column] = None
        if (row.get("total_volume") or 0.0) == 0.0:
            cleaned["put_call_ratio_volume"] = None
        return cleaned

    @staticmethod
    def _without_empty_zeros(row: Dict[str, Any], columns) -> Dict[str, Any]:
        """
        The row with these columns set to None where they are exactly 0.0. The collector stores an empty bucket (no open interest in the
        at-the-money band, an expiry without positions) as 0: that is "nothing to compute", not a measured zero (checked on the stored data:
        44% of the at-the-money put/call ratios are exactly 0).
        """
        return {key: (None if key in columns and value == 0.0 else value) for key, value in row.items()}

    @staticmethod
    def _fresh(row: Optional[Dict[str, Any]], as_of: datetime) -> Optional[Dict[str, Any]]:
        """The row, or None when its hour is older than the freshness limit at ``as_of``."""
        if row is None:
            return None
        if (as_of - row["snapshot_hour"]) > timedelta(hours=MAX_SNAPSHOT_AGE_HOURS):
            return None
        return row

    def _dvol(self, currency: str, as_of: datetime):
        """(DVOL, its percentile over the last 365 days of closed hourly candles) at ``as_of``; None for both when there is no history."""
        newest = self.repository.get_latest_dvol_candle_start_before(currency, as_of)
        if newest is None or as_of - newest > timedelta(hours=MAX_SNAPSHOT_AGE_HOURS):
            return None, None                                        # no DVOL, or a stale one: never shown as current
        closes = self.repository.get_dvol_history_before(currency, as_of, days=365)
        if not closes:
            return None, None
        latest = closes[-1]
        return latest, 100.0 * sum(1 for value in closes if value <= latest) / len(closes)

    def daily_log_returns(self, currency: str, as_of: datetime) -> List[float]:
        """
        Daily log returns of the perpetual (since mid 2024) from the candles complete at ``as_of``: the NEWEST unbroken run of consecutive days, so that a
        missing candle never joins the days on its two sides (the series is used by an autoregression).
        """
        end = last_completed_candle_start(as_of)
        candles = self.repository.get_ohlcv_by_date_range(currency, datetime(2024, 7, 1), end)
        run: List[float] = []
        for first, second in zip(candles, candles[1:]):
            if first["close"] > 0 and second["close"] > 0 and (second["date"] - first["date"]) == timedelta(days=1):
                run.append(math.log(second["close"] / first["close"]))
            else:
                run = []                                                  # a gap or a bad close: start again after it
        return run

    def _realised(self, currency: str, as_of: datetime, days: int) -> Optional[float]:
        """Realised volatility of the last ``days`` daily returns of the candles that were complete at ``as_of`` (the running candle is never used)."""
        end = last_completed_candle_start(as_of)
        candles = self.repository.get_ohlcv_by_date_range(currency, end - timedelta(days=days + 15), end)
        return realised_volatility(candles, days)
