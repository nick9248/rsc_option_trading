"""
Volatility metric reconstruction service.

Backfills onchain_volatility_snapshots by re-running the existing volatility
calculators (VolatilitySurfaceCalculator, VRPCalculator) and market-wide
formulas (expected move, IV percentile/rank) against historical
hourly_snapshots / historical_trades / dvol_history / ohlcv_history data —
the inputs were collected all along, only the derived metrics were never
persisted (see TASKS.md Track B).

Two-pass design (the table itself documents why — see migration 012):
  Pass 1 — compute everything except iv_percentile_expiry (self-referential:
           needs the ATM-IV series to exist first) and persist rows.
  Pass 2 — walk the persisted ATM-IV series per (currency, expiration) and
           backfill iv_percentile_expiry against a trailing window.
"""

import logging
import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator
from coding.core.analytics.vrp_calculator import VRPCalculator
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class VolatilityReconstructionService:
    """Reconstructs historical volatility-surface/VRP/percentile metrics."""

    # Mirrors get_atm_iv_history's default limit=90 (a ~90-day daily series in the
    # live per-expiry IV percentile path — on_chain_analysis_service.py:752-778).
    # Expressed as a time window here (not a row count) since our series is hourly.
    IV_PERCENTILE_EXPIRY_LOOKBACK_DAYS = 90
    IV_PERCENTILE_EXPIRY_MIN_HISTORY = 5  # mirrors the `len(iv_history) >= 5` floor, ibid. line 765
    VRP_LOOKBACK_DAYS = 30  # matches VRPCalculator/MarketWideCalculator default lookback

    def __init__(self, repository: DatabaseRepository):
        self.repo = repository

    def reconstruct_range(
        self,
        currency: str,
        start: datetime,
        end: datetime,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Dict[str, int]:
        """
        Run the full two-pass reconstruction for a currency over a date range.

        Returns:
            Dict with counts: pairs_found, rows_saved, rows_skipped, percentile_updated.
        """
        progress = progress_callback or (lambda msg: None)

        pairs = self.repo.get_distinct_snapshot_hours_with_expirations(currency, start, end)
        progress(f"Pass 1: reconstructing {len(pairs)} (hour, expiration) slices for {currency}...")

        rows_saved = 0
        rows_skipped = 0
        for snapshot_hour, expiration in pairs:
            if self._reconstruct_one(currency, snapshot_hour, expiration):
                rows_saved += 1
            else:
                rows_skipped += 1

        progress(f"Pass 1 done: {rows_saved} saved, {rows_skipped} skipped (no instrument data)")

        progress(f"Pass 2: backfilling per-expiry IV percentile for {currency}...")
        percentile_updated = self._backfill_iv_percentile_expiry(currency, start, end, progress)
        progress(f"Pass 2 done: {percentile_updated} rows updated with iv_percentile_expiry")

        return {
            "pairs_found": len(pairs),
            "rows_saved": rows_saved,
            "rows_skipped": rows_skipped,
            "percentile_updated": percentile_updated,
        }

    # ── Pass 1: per-(hour, expiration) reconstruction ────────────────────────

    def _reconstruct_one(self, currency: str, snapshot_hour: datetime, expiration: str) -> bool:
        """Compute and save one (snapshot_hour, currency, expiration) volatility row."""
        instruments = self.repo.get_hourly_snapshots_for_hour(currency, snapshot_hour, expiration)
        if not instruments:
            return False

        underlying_price = self._extract_underlying_price(instruments)
        if underlying_price <= 0:
            logger.warning(
                f"Skipping {currency} {expiration} at {snapshot_hour}: no usable underlying_price"
            )
            return False

        hour_start = snapshot_hour
        hour_end = snapshot_hour + timedelta(hours=1)
        trades = self.repo.get_trades_for_hour_and_expiration(currency, hour_start, hour_end, expiration)
        vwap_iv, mark_iv_avg = self._calculate_vwap_iv(trades, instruments)

        surface_calc = VolatilitySurfaceCalculator(
            instruments=instruments,
            spot_price=underlying_price,
            expiration=expiration,
        )
        surface_calc.set_vwap_iv_data(vwap_iv, mark_iv_avg)
        surface = surface_calc.calculate()

        skew = surface["skew_25d"]
        second_order = surface["second_order_greeks"]
        pc_moneyness = surface["pc_by_moneyness"]

        vrp_metrics = self._reconstruct_vrp(currency, snapshot_hour, instruments, underlying_price)
        market_metrics = self._reconstruct_market_metrics(currency, snapshot_hour, underlying_price)

        metrics = {
            "atm_iv": surface["atm_iv"],
            "skew_25d": skew.get("skew"),
            "put_25d_iv": skew.get("put_25d_iv"),
            "call_25d_iv": skew.get("call_25d_iv"),
            "net_vanna": second_order.get("net_vanna"),
            "net_charm": second_order.get("net_charm"),
            "vwap_iv": vwap_iv,
            "mark_iv_avg": mark_iv_avg,
            "pc_atm_ratio": self._sanitize_decimal(pc_moneyness.get("atm", {}).get("ratio")),
            "pc_near_otm_ratio": self._sanitize_decimal(pc_moneyness.get("near_otm", {}).get("ratio")),
            "pc_far_otm_ratio": self._sanitize_decimal(pc_moneyness.get("far_otm", {}).get("ratio")),
            "iv_percentile_expiry": None,  # filled in by pass 2
            **vrp_metrics,
            **market_metrics,
        }

        self.repo.save_volatility_snapshot(snapshot_hour, currency, expiration, metrics, underlying_price)
        return True

    @staticmethod
    def _extract_underlying_price(instruments: List[Dict[str, Any]]) -> float:
        """
        Mode of index_price across the slice's instruments.

        Mirrors the documented pattern for extracting underlying_price from
        aggregated instrument data (CLAUDE.md "Data investigation example" /
        OnChainAnalyzer._extract_underlying_price, on_chain_analyzer.py:65):
        the most-common value wins over stale per-instrument cached prices.
        """
        prices = [i["index_price"] for i in instruments if i.get("index_price")]
        if not prices:
            return 0.0
        return float(Counter(prices).most_common(1)[0][0])

    @staticmethod
    def _calculate_vwap_iv(
        trades: List[Dict[str, Any]],
        instruments: List[Dict[str, Any]]
    ) -> tuple:
        """
        VWAP IV vs mark IV — mirrors OnChainAnalysisService._calculate_vwap_iv
        (on_chain_analysis_service.py:446-481). Reimplemented here as plain
        arithmetic (not a calculator class) because the live version is a
        private instance method on a service tied to a live API session.
        """
        weighted_iv_sum = 0.0
        total_volume = 0.0
        for trade in trades:
            iv = trade.get("iv")
            amount = trade.get("amount", 0)
            if iv is not None and iv > 0 and amount > 0:
                weighted_iv_sum += iv * amount
                total_volume += amount
        vwap_iv = (weighted_iv_sum / total_volume) if total_volume > 0 else None

        mark_ivs = [i["mark_iv"] for i in instruments if i.get("mark_iv") is not None and i["mark_iv"] > 0]
        mark_iv_avg = (sum(mark_ivs) / len(mark_ivs)) if mark_ivs else None

        return vwap_iv, mark_iv_avg

    def _reconstruct_vrp(
        self,
        currency: str,
        snapshot_hour: datetime,
        instruments: List[Dict[str, Any]],
        underlying_price: float
    ) -> Dict[str, Optional[float]]:
        """
        VRP = IV - RV, reusing VRPCalculator as-is.

        Passes reference_time=snapshot_hour so the RV lookback window anchors on
        the historical hour rather than "now" (see vrp_calculator.py
        calculate_realized_volatility — its date filter is `now`-relative by
        default, which would silently filter out all historical price_history).

        Returns all-None when ohlcv_history has no coverage for the window
        (table starts 2026-03-14 — TASKS.md Track B verified facts) or when
        IV/RV inputs are unusable, rather than persisting a misleading 0.
        """
        window_start = snapshot_hour - timedelta(days=self.VRP_LOOKBACK_DAYS + 5)
        ohlcv_rows = self.repo.get_ohlcv_by_date_range(currency, window_start, snapshot_hour)
        price_history = [
            {"timestamp": row["date"].timestamp(), "close": row["close"]}
            for row in ohlcv_rows
        ]

        vrp_calc = VRPCalculator(currency=currency, lookback_days=self.VRP_LOOKBACK_DAYS)
        # VRPCalculator uses numpy internally (np.std/np.mean) -> np.float64 results.
        # psycopg2 can't adapt those directly, so cast to plain Python float at the boundary.
        realized_vol = float(vrp_calc.calculate_realized_volatility(price_history, reference_time=snapshot_hour))
        if realized_vol <= 0:
            return {"vrp_absolute": None, "vrp_percentage": None, "realized_vol": None}

        options_data = [
            {
                "mark_iv": float(i["mark_iv"]) / 100.0,  # percentage -> decimal, matches vrp_service.py:230
                "strike": float(i["strike"]),
                "underlying_price": underlying_price,
            }
            for i in instruments
            if i.get("mark_iv") is not None and i.get("strike") is not None
        ]
        implied_vol = float(vrp_calc.calculate_average_iv(options_data))
        if implied_vol <= 0:
            return {"vrp_absolute": None, "vrp_percentage": None, "realized_vol": realized_vol}

        vrp_result = vrp_calc.calculate_vrp(implied_vol, realized_vol)
        return {
            "vrp_absolute": float(vrp_result["vrp_absolute"]),
            "vrp_percentage": float(vrp_result["vrp_percentage"]),
            "realized_vol": realized_vol,
        }

    def _reconstruct_market_metrics(
        self,
        currency: str,
        snapshot_hour: datetime,
        underlying_price: float
    ) -> Dict[str, Optional[float]]:
        """
        365d IV percentile + expected moves — pure formula, mirrors
        OnChainAnalysisService._fetch_market_metrics (on_chain_analysis_service.py:896-937)
        and the expected-move block in OnChainAnalyzer.generate_report
        (on_chain_analyzer.py:627-632).

        iv_rank_365d is intentionally always None here — see
        DatabaseRepository.get_dvol_history_before for why: the live formula
        needs daily high/low (true range), and neither dvol_history nor
        volatility_index_history ever persisted those — only a single daily
        close-equivalent value. That input does not exist historically, so
        this one metric is genuinely not reconstructable (not a bug to fix —
        a data gap from before this table existed).
        """
        dvol_series = self.repo.get_dvol_history_before(currency, snapshot_hour, days=365)
        if not dvol_series:
            return {
                "iv_percentile_365d": None,
                "iv_rank_365d": None,
                "expected_daily_move": None,
                "expected_weekly_move": None,
                "expected_monthly_move": None,
            }

        current_dvol = dvol_series[-1]
        values_below = sum(1 for v in dvol_series if v < current_dvol)
        iv_percentile_365d = (values_below / len(dvol_series)) * 100

        return {
            "iv_percentile_365d": iv_percentile_365d,
            "iv_rank_365d": None,
            "expected_daily_move": current_dvol / 100 / math.sqrt(365) * underlying_price,
            "expected_weekly_move": current_dvol / 100 / math.sqrt(52) * underlying_price,
            "expected_monthly_move": current_dvol / 100 / math.sqrt(12) * underlying_price,
        }

    @staticmethod
    def _sanitize_decimal(value: Optional[float]) -> Optional[float]:
        """
        Convert inf/-inf/nan to None.

        Postgres DECIMAL columns can't store them — _calculate_pc_by_moneyness
        (volatility_surface_calculator.py:166-219) returns float('inf') for any
        moneyness bucket with zero call OI and nonzero put OI.
        """
        if value is None:
            return None
        if math.isinf(value) or math.isnan(value):
            return None
        return value

    # ── Pass 2: per-expiry IV percentile (self-referential) ──────────────────

    def _backfill_iv_percentile_expiry(
        self,
        currency: str,
        start: datetime,
        end: datetime,
        progress: Callable[[str], None]
    ) -> int:
        """
        Walk the saved ATM-IV series per expiration and UPDATE iv_percentile_expiry
        with a trailing-window percentile for each row.

        Adapted (not literally ported) from the live per-expiry IV percentile
        (on_chain_analysis_service.py:752-778): that path compares the current
        ATM IV to a ~90-day daily series fetched from daily_oi_snapshots via
        get_atm_iv_history (limit=90, requires >=5 points), which uses its own
        per-calculation ATM-strike selection on a *daily*-grain table — a
        different series than the *hourly* one we're building here, and one
        that can't drive a self-referential two-pass backfill of itself.

        Same percentile math (count-below / total * 100), applied instead to
        OUR OWN reconstructed atm_iv series, with a time-based trailing window
        (days, not row count — hourly density makes row counts a poor proxy
        for "days of history").
        """
        rows = self.repo.get_volatility_snapshots_for_percentile_backfill(currency, start, end)
        by_expiration: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_expiration.setdefault(row["expiration"], []).append(row)

        window = timedelta(days=self.IV_PERCENTILE_EXPIRY_LOOKBACK_DAYS)
        updated = 0

        for expiration, exp_rows in by_expiration.items():
            exp_rows.sort(key=lambda r: r["snapshot_hour"])

            for idx, row in enumerate(exp_rows):
                current_iv = row["atm_iv"]
                if current_iv is None:
                    continue

                cutoff = row["snapshot_hour"] - window
                history = [
                    float(r["atm_iv"]) for r in exp_rows[:idx]
                    if r["atm_iv"] is not None and r["snapshot_hour"] >= cutoff
                ]
                if len(history) < self.IV_PERCENTILE_EXPIRY_MIN_HISTORY:
                    continue

                below = sum(1 for iv in history if iv < float(current_iv))
                percentile = (below / len(history)) * 100

                self.repo.update_iv_percentile_expiry(
                    snapshot_hour=row["snapshot_hour"],
                    currency=currency,
                    expiration=expiration,
                    iv_percentile_expiry=percentile,
                )
                updated += 1

            progress(f"  {currency} {expiration}: {len(exp_rows)} rows scanned")

        return updated
