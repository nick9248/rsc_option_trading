import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

SHORT_HORIZONS = {
    "return_4h":  timedelta(hours=4),
    "return_8h":  timedelta(hours=8),
    "return_12h": timedelta(hours=12),
    "return_24h": timedelta(hours=24),
    "return_48h": timedelta(hours=48),
    "return_72h": timedelta(hours=72),
}

LONG_HORIZONS = {
    "return_7d":  timedelta(days=7),
    "return_30d": timedelta(days=30),
}

ALL_HORIZONS = {**SHORT_HORIZONS, **LONG_HORIZONS}
COVERAGE_WARN_THRESHOLD = 20
DATASET_MIN_ROWS = 30


class RegimeDatasetBuilder:
    """
    Queries the DB and produces a raw dataset DataFrame for the regime weight optimizer.
    All DB access is read-only. All datetimes are timezone-naive UTC.
    """

    def __init__(self, repository):
        self._repo = repository

    def build(self, currency: str = "BTC") -> pd.DataFrame:
        """
        Build the dataset DataFrame for the given currency.

        Fetches all regime_detections, resolves forward prices for 8 horizons,
        and returns a DataFrame with one row per detection.

        Drops rows where current_price is None or 0.
        Drops rows where all 8 horizons are None.
        Logs a warning if fewer than DATASET_MIN_ROWS rows remain.
        """
        raw = self._repo.get_regime_detections(
            currency,
            start_time=datetime(2020, 1, 1),
            end_time=datetime.now(),
        )
        if not raw:
            logger.warning(f"No regime detections found for {currency}")
            return pd.DataFrame()

        # Sort ascending by detected_at (DB returns DESC)
        raw.sort(key=lambda r: r["detected_at"])

        # Clean: drop rows with invalid current_price
        valid = [r for r in raw if r.get("current_price") and float(r["current_price"]) != 0.0]

        rows = []
        for rec in valid:
            T = rec["detected_at"]
            price = float(rec["current_price"])

            row = {
                "detected_at":       T,
                "currency":          rec["currency"],
                "current_price":     price,
                "trend_score":       float(rec["trend_score"]) if rec["trend_score"] is not None else 0.0,
                "volatility_score":  float(rec["volatility_score"]) if rec["volatility_score"] is not None else 0.0,
                "momentum_score":    float(rec["momentum_score"]) if rec["momentum_score"] is not None else 0.0,
                "onchain_score":     float(rec["onchain_score"]) if rec["onchain_score"] is not None else 0.0,
                "sentiment_score":   float(rec["sentiment_score"]) if rec["sentiment_score"] is not None else 0.0,
            }

            # Short horizons — scan the in-memory sorted list
            for col, H in SHORT_HORIZONS.items():
                row[col] = self._lookup_short(valid, currency, T, price, H)

            # Long horizons — query ohlcv_history
            for col, H in LONG_HORIZONS.items():
                row[col] = self._lookup_long(currency, T, price, H)

            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # Drop rows where all 8 horizons are None/NaN
        horizon_cols = list(ALL_HORIZONS.keys())
        all_none_mask = df[horizon_cols].isna().all(axis=1)
        df = df[~all_none_mask].reset_index(drop=True)

        if len(df) < DATASET_MIN_ROWS:
            logger.warning(
                f"Dataset too small for reliable optimization — "
                f"{len(df)} detections available, minimum recommended is {DATASET_MIN_ROWS}."
            )

        return df

    def _lookup_short(
        self,
        sorted_records: list,
        currency: str,
        T: datetime,
        price: float,
        H: timedelta,
    ) -> Optional[float]:
        """Find forward price in regime_detections within ±10% of H after T."""
        target = T + H
        window_start = T + H * 0.9
        window_end = T + H * 1.1

        candidates = [
            r for r in sorted_records
            if r["currency"] == currency
            and r["detected_at"] != T
            and window_start <= r["detected_at"] <= window_end
            and r.get("current_price")
            and float(r["current_price"]) != 0.0
        ]

        if not candidates:
            return None

        best = min(candidates, key=lambda r: abs(r["detected_at"] - target))
        found_price = float(best["current_price"])
        return (found_price - price) / price * 100.0

    def _lookup_long(
        self,
        currency: str,
        T: datetime,
        price: float,
        H: timedelta,
    ) -> Optional[float]:
        """Find forward price in ohlcv_history within ±10% of H after T."""
        target = T + H
        window_start = T + H * 0.9
        window_end = T + H * 1.1

        candles = self._repo.get_ohlcv_by_date_range(currency, window_start, window_end)
        if not candles:
            return None

        best = min(candles, key=lambda r: abs(r["date"] - target))
        found_price = float(best["close"])
        return (found_price - price) / price * 100.0

    def summary(self, df: pd.DataFrame) -> str:
        """
        Returns a formatted string showing horizon coverage statistics.
        Caller is responsible for printing it.
        """
        if df.empty:
            return "Dataset is empty — no coverage statistics available."

        n = len(df)
        lines = []
        horizon_cols = list(ALL_HORIZONS.keys())
        for col in horizon_cols:
            label = col.replace("return_", "")
            count = int(df[col].notna().sum())
            pct = count / n * 100 if n > 0 else 0.0
            if count < COVERAGE_WARN_THRESHOLD:
                logger.warning(
                    f"Horizon {label} has only {count} matched rows "
                    f"(< {COVERAGE_WARN_THRESHOLD} threshold)"
                )
            lines.append(f"  {label:>4}: {count:>4}/{n} ({pct:.0f}%)")

        return "\n".join(lines)
