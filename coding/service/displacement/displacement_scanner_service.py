import logging
from pathlib import Path
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent
from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.core.displacement.displacement_detector import DisplacementDetector
from coding.core.displacement.conviction_scorer import ConvictionScorer
from coding.core.displacement.strike_selector import StrikeSelector
from coding.service.displacement.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)

# Load trained model if available
_MODEL_PATH = Path(__file__).parents[3] / "models" / "displacement_scorer_v1" / "scorer.joblib"


class DisplacementScannerService:
    """
    Orchestrates the full displacement detection pipeline:
    price fetch → detect → score → select strike → save → alert.
    """

    def __init__(self, config: DisplacementConfig, api_service, repository):
        self._config = config
        self._api = api_service
        self._repo = repository
        self._detector = DisplacementDetector(config)
        self._scorer = ConvictionScorer(config, model_path=_MODEL_PATH if _MODEL_PATH.exists() else None)
        self._selector = StrikeSelector(config)
        self._telegram = TelegramAlertService()

    def scan(self, assets: list[str]) -> list[DisplacementSignal]:
        """Run a full scan for all assets. Returns signals that met conviction threshold."""
        results = []
        for asset in assets:
            try:
                signal = self._scan_asset(asset)
                if signal:
                    results.append(signal)
            except Exception as e:
                logger.error("Scan failed for %s: %s", asset, e)
        return results

    def get_current_prices(self, asset: str) -> dict[str, float]:
        """Returns current price and 24h change for GUI display."""
        try:
            candles = self._api.get_price_ohlcv(asset, resolution_hours=1, lookback_hours=25)
            if not candles:
                return {"price": 0.0, "change_24h_pct": 0.0}
            now = candles[0]["close"]
            ago_24h = candles[min(24, len(candles) - 1)]["close"]
            change = (now - ago_24h) / ago_24h if ago_24h > 0 else 0.0
            return {"price": now, "change_24h_pct": change}
        except Exception as e:
            logger.error("get_current_prices failed for %s: %s", asset, e)
            return {"price": 0.0, "change_24h_pct": 0.0}

    # ── Internal pipeline ──────────────────────────────────────────

    def _scan_asset(self, asset: str) -> Optional[DisplacementSignal]:
        prices_dict = self._fetch_prices(asset)
        event = self._detector.check(asset, prices_dict)
        if not event:
            return None

        logger.info("Displacement detected for %s — scoring setup", asset)
        market_data = self._fetch_market_data(asset)
        conviction_pct, breakdown = self._scorer.score(event, market_data)

        if conviction_pct < self._config.alert_medium_threshold * 100:
            logger.info("Conviction %.1f%% below threshold — no alert", conviction_pct)
            return None

        conviction_label = "HIGH" if conviction_pct >= self._config.alert_high_threshold * 100 else "MEDIUM"
        contract = self._selector.select(asset, market_data["options_chain"], event.current_price)

        signal = self._build_signal(event, conviction_pct, conviction_label, breakdown, market_data, contract)
        self._repo.save_displacement_signal(signal)

        if self._telegram.send(signal):
            logger.info("Telegram alert sent for %s", asset)

        return signal

    def _fetch_prices(self, asset: str) -> dict[str, float]:
        candles = self._api.get_price_ohlcv(asset, resolution_hours=1, lookback_hours=168)
        if not candles:
            raise ValueError(f"No price data available for {asset}")

        def price_at(hours: int) -> float:
            idx = min(hours, len(candles) - 1)
            return candles[idx]["close"]

        return {
            "now": candles[0]["close"],
            "1h_ago": price_at(1),
            "4h_ago": price_at(4),
            "24h_ago": price_at(24),
            "7d_ago": price_at(168),
        }

    def _fetch_market_data(self, asset: str) -> dict:
        dvol_history = self._repo.get_dvol_history(asset, limit=400)
        ohlcv_history = self._repo.get_ohlcv_daily(asset, limit=1095)

        dvol_result = self._api.get_volatility_index_data(currency=asset, resolution=3600)
        dvol_data = dvol_result.get("data", [])
        dvol_current = dvol_data[-1][4] if dvol_data else 50.0

        funding_result = self._api.get_funding_chart_data(
            instrument_name=f"{asset}-PERPETUAL", length="8h"
        )
        funding_rows = funding_result.get("result", [])
        funding_rate = funding_rows[-1].get("interest_8h", 0.0) if funding_rows else 0.0

        options_chain = self._api.get_book_summary_by_currency(asset)

        return {
            "dvol_history": dvol_history,
            "dvol_current": dvol_current,
            "funding_rate": funding_rate,
            "ohlcv_history": ohlcv_history,
            "options_chain": options_chain,
        }

    def _build_signal(
        self,
        event: DisplacementEvent,
        conviction_pct: float,
        conviction_label: str,
        breakdown: dict,
        market_data: dict,
        contract: Optional[dict],
    ) -> DisplacementSignal:
        # Max pain distance: invert the scoring formula to recover the approximate raw value.
        # score_max_pain = min(100, distance / max_pain_distance_full_score * 100)
        # => distance ≈ score_max_pain / 100 * max_pain_distance_full_score
        max_pain_distance_pct = breakdown["max_pain"] / 100 * self._config.max_pain_distance_full_score

        if contract is not None:
            return DisplacementSignal(
                asset=event.asset,
                detected_at=event.detected_at,
                drop_24h_pct=event.drop_24h_pct,
                drop_1h_pct=event.drop_1h_pct,
                conviction_pct=conviction_pct,
                conviction_label=conviction_label,
                score_drop_magnitude=breakdown["drop_magnitude"],
                score_drop_speed=breakdown["drop_speed"],
                score_funding_rate=breakdown["funding_rate"],
                score_dvol_spike=breakdown["dvol_spike"],
                score_max_pain=breakdown["max_pain"],
                score_term_structure=breakdown["term_structure"],
                funding_rate_value=market_data["funding_rate"],
                dvol_sigma=self._scorer._last_dvol_sigma,
                max_pain_distance_pct=max_pain_distance_pct,
                term_structure_inversion_pct=self._scorer._last_term_inversion_pct,
                instrument_name=contract.get("instrument_name"),
                strike=contract.get("strike"),
                expiry_date=None,
                dte=contract.get("dte"),
                delta=contract.get("delta"),
                mark_iv=contract.get("mark_iv"),
                premium_usd=contract.get("premium_usd"),
                target_50pct_price=contract.get("target_50pct_price"),
                target_100pct_price=contract.get("target_100pct_price"),
                target_200pct_price=contract.get("target_200pct_price"),
            )
        else:
            return DisplacementSignal(
                asset=event.asset,
                detected_at=event.detected_at,
                drop_24h_pct=event.drop_24h_pct,
                drop_1h_pct=event.drop_1h_pct,
                conviction_pct=conviction_pct,
                conviction_label=conviction_label,
                score_drop_magnitude=breakdown["drop_magnitude"],
                score_drop_speed=breakdown["drop_speed"],
                score_funding_rate=breakdown["funding_rate"],
                score_dvol_spike=breakdown["dvol_spike"],
                score_max_pain=breakdown["max_pain"],
                score_term_structure=breakdown["term_structure"],
                funding_rate_value=market_data["funding_rate"],
                dvol_sigma=self._scorer._last_dvol_sigma,
                max_pain_distance_pct=max_pain_distance_pct,
                term_structure_inversion_pct=self._scorer._last_term_inversion_pct,
            )
