# coding/service/displacement/backtest_service.py
"""
Backtests the displacement strategy on historical BTC/ETH price drops.

Run locally (NOT on VPS) with:
    python -m coding.service.displacement.backtest_service

Prerequisites:
    - ohlcv_history table populated (at least 1 year)
    - dvol_history table populated
    - funding_rate_history table populated
    - pip install scikit-learn joblib
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_DIR = Path("models/displacement_scorer_v1")


class BacktestService:
    """
    Finds historical displacement events, labels outcomes (profitable or not),
    and trains a logistic regression conviction scorer.
    """

    def __init__(self, config, repository, api_service):
        from coding.core.displacement.models.displacement_config import DisplacementConfig
        from coding.core.displacement.conviction_scorer import ConvictionScorer
        from coding.service.displacement.historical_options_fetcher import HistoricalOptionsFetcher

        self._config = config
        self._repo = repository
        self._api = api_service
        self._scorer = ConvictionScorer(config)
        self._fetcher = HistoricalOptionsFetcher(api_service)

    def run(
        self,
        assets: list[str] = None,
        profit_target_pct: float = 0.50,
    ) -> None:
        """
        Full backtest pipeline:
        1. Find historical displacement events from ohlcv_history
        2. Reconstruct signals at each event date
        3. Fetch historical options data and label outcomes
        4. Train logistic regression model
        5. Print backtest report
        """
        if assets is None:
            assets = ["BTC", "ETH"]

        all_events = []
        for asset in assets:
            events = self._find_historical_events(asset)
            logger.info("Found %d displacement events for %s", len(events), asset)
            all_events.extend(events)

        if not all_events:
            print("No historical events found. Check ohlcv_history data.")
            return

        print(f"Total events to backtest: {len(all_events)}")

        labeled = []
        for i, event in enumerate(all_events):
            print(f"Labeling event {i+1}/{len(all_events)}: {event['asset']} {event['date']}")
            row = self._label_event(event, profit_target_pct)
            if row is not None:
                labeled.append(row)

        if not labeled:
            print("No labeled events — insufficient historical options data.")
            return

        self._print_report(labeled)
        self._train_model(labeled)

    def _find_historical_events(self, asset: str) -> list[dict]:
        """Find all dates where asset dropped >= threshold. Apply cooldown deduplication."""
        ohlcv = self._repo.get_ohlcv_daily(asset, limit=1095)
        if len(ohlcv) < 2:
            return []

        events = []
        last_event_date = None

        for i in range(len(ohlcv) - 1):
            today = ohlcv[i]
            day_ago = ohlcv[min(i + 1, len(ohlcv) - 1)]
            week_ago = ohlcv[min(i + 7, len(ohlcv) - 1)]

            today_close = today.get("close", 0)
            day_close = day_ago.get("close", 1)
            week_close = week_ago.get("close", 1)

            if today_close <= 0 or day_close <= 0:
                continue

            drop_24h = (day_close - today_close) / day_close
            drop_7d = (week_close - today_close) / week_close if week_close > 0 else 0

            if (drop_24h < self._config.drop_24h_threshold and
                    drop_7d < self._config.drop_7d_threshold):
                continue

            event_date = today.get("date")
            if event_date is None:
                continue

            cooldown_days = self._config.cooldown_hours / 24
            if last_event_date and (event_date - last_event_date).days < cooldown_days:
                continue

            last_event_date = event_date
            ts_ms = int(datetime(
                event_date.year, event_date.month, event_date.day,
                tzinfo=timezone.utc,
            ).timestamp() * 1000)

            events.append({
                "asset": asset,
                "date": event_date,
                "ts_ms": ts_ms,
                "drop_24h_pct": drop_24h,
                "drop_7d_pct": drop_7d,
                "current_price": today_close,
            })

        return events

    def _label_event(
        self,
        event: dict,
        profit_target: float,
    ) -> Optional[dict]:
        """Reconstruct signals and label whether the trade was profitable."""
        from coding.core.displacement.models.displacement_event import DisplacementEvent

        asset = event["asset"]
        event_ts = event["ts_ms"]

        ohlcv = self._repo.get_ohlcv_daily(asset, limit=1095)
        dvol_history = self._repo.get_dvol_history(asset, limit=400)
        funding_history = self._repo.get_funding_rate_history(asset, limit=100)

        funding_rate = funding_history[0] if funding_history else 0.0
        dvol_current = dvol_history[0] if dvol_history else 50.0

        market_data = {
            "funding_rate": funding_rate,
            "dvol_current": dvol_current,
            "dvol_history": dvol_history,
            "ohlcv_history": ohlcv,
            "options_chain": [],  # max_pain and term_structure → 50 (neutral) without chain
        }

        displacement_event = DisplacementEvent(
            asset=asset,
            detected_at=datetime.fromtimestamp(event_ts / 1000, tz=timezone.utc),
            current_price=event["current_price"],
            drop_1h_pct=max(event["drop_24h_pct"] * 0.3, 0.0),  # approximate
            drop_4h_pct=max(event["drop_24h_pct"] * 0.6, 0.0),
            drop_24h_pct=event["drop_24h_pct"],
            drop_7d_pct=event["drop_7d_pct"],
            triggering_timeframe="24h",
        )
        _, breakdown = self._scorer.score(displacement_event, market_data)

        # Fetch historical options to determine P&L
        options = self._fetcher.fetch_options_at_event(
            asset=asset,
            event_ts_ms=event_ts,
            checkpoint_days=[30, 60, 90, 180],
        )

        if not options:
            return None

        # Pick best option: closest DTE to 150 days, then lowest entry price
        valid = [o for o in options if o.get("entry_mark_price") and o["entry_mark_price"] > 0]
        if not valid:
            return None

        best = min(valid, key=lambda o: (
            abs(o.get("dte_at_event", 180) - 150),
            o.get("entry_mark_price", 1.0),
        ))

        entry = best["entry_mark_price"]
        exit_prices = best.get("exit_prices", {})

        # JSON loads string keys — convert to int
        exit_prices_int = {int(k): v for k, v in exit_prices.items()}

        profitable = False
        max_gain = 0.0
        for days in [30, 60, 90, 180]:
            exit_p = exit_prices_int.get(days)
            if exit_p and exit_p > 0 and entry > 0:
                gain = (exit_p - entry) / entry
                max_gain = max(max_gain, gain)
                if gain >= profit_target:
                    profitable = True
                    break

        return {
            "asset": asset,
            "date": str(event["date"]),
            "drop_24h_pct": event["drop_24h_pct"],
            "signals": breakdown,
            "profitable": int(profitable),
            "entry_mark_price": entry,
            "max_gain_pct": round(max_gain * 100, 1),
        }

    def _print_report(self, labeled: list[dict]) -> None:
        n = len(labeled)
        profitable = [r for r in labeled if r["profitable"]]
        not_profitable = [r for r in labeled if not r["profitable"]]

        print("\n" + "=" * 55)
        print("DISPLACEMENT STRATEGY BACKTEST RESULTS")
        print("=" * 55)
        print(f"Events labeled:        {n}")
        print(f"Profitable (>50%):     {len(profitable)}  ({len(profitable) / n * 100:.0f}%)")
        if profitable:
            avg_gain = sum(r["max_gain_pct"] for r in profitable) / len(profitable)
            print(f"Avg max gain (winners): {avg_gain:.0f}%")
        if not_profitable:
            avg_loss = sum(r["max_gain_pct"] for r in not_profitable) / len(not_profitable)
            print(f"Avg max gain (losers):  {avg_loss:.0f}%")

        signal_names = [
            "drop_magnitude", "drop_speed", "funding_rate",
            "dvol_spike", "max_pain", "term_structure",
        ]
        print("\nSignal correlation with profitable outcome:")
        for sig in signal_names:
            vals = [r["signals"].get(sig, 50.0) for r in labeled]
            outcomes = [r["profitable"] for r in labeled]
            mean_v = sum(vals) / len(vals)
            mean_o = sum(outcomes) / len(outcomes)
            cov = sum((v - mean_v) * (o - mean_o) for v, o in zip(vals, outcomes)) / len(vals)
            std_v = (sum((v - mean_v) ** 2 for v in vals) / len(vals)) ** 0.5
            std_o = (sum((o - mean_o) ** 2 for o in outcomes) / len(outcomes)) ** 0.5
            corr = cov / (std_v * std_o) if std_v * std_o > 0 else 0.0
            print(f"  {sig:<22} {corr:+.3f}")
        print("=" * 55 + "\n")

    def _train_model(self, labeled: list[dict]) -> None:
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
            import joblib
        except ImportError:
            print("scikit-learn / joblib not installed. Run: pip install scikit-learn joblib")
            return

        signal_names = [
            "drop_magnitude", "drop_speed", "funding_rate",
            "dvol_spike", "max_pain", "term_structure",
        ]
        X = [[r["signals"].get(s, 50.0) for s in signal_names] for r in labeled]
        y = [r["profitable"] for r in labeled]

        if len(set(y)) < 2:
            print("All outcomes identical — cannot train classifier (need both wins and losses)")
            return

        split = max(1, int(len(labeled) * 0.7))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(C=1.0, max_iter=500, random_state=42)),
        ])
        pipeline.fit(X_train, y_train)

        if X_val:
            val_acc = sum(
                1 for xv, yv in zip(X_val, y_val) if pipeline.predict([xv])[0] == yv
            ) / len(X_val)
            print(f"Validation accuracy: {val_acc * 100:.1f}%")

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path = MODEL_DIR / "scorer.joblib"
        joblib.dump(pipeline, model_path)
        print(f"Model saved to {model_path}")
        logger.info("Conviction scorer model saved to %s", model_path)


if __name__ == "__main__":
    from coding.core.logging.logging_setup import init_logging
    from coding.core.displacement.models.displacement_config import DisplacementConfig
    from coding.core.database.repository import DatabaseRepository
    from coding.service.deribit.deribit_api_service import DeribitApiService

    init_logging(level="INFO")
    svc = BacktestService(
        config=DisplacementConfig(),
        repository=DatabaseRepository(),
        api_service=DeribitApiService(),
    )
    svc.run(assets=["BTC", "ETH"])
