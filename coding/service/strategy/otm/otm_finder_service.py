"""
OTMFinderService — orchestrates all four gates and produces ranked OTMSignal list.

Data flow per asset:
  1. Fetch options chain (Deribit)
  2. Run Gate 1 (liquidity) — per contract
  3. Fetch all supporting data (DVOL, on-chain, funding history, etc.)
  4. Run Gate 2 (vol regime) — asset-level
  5. Run Gate 3 (directional) — asset-level
  6. Run Gate 4 (strike/expiry) — per surviving contract
  7. Kelly size each signal
  8. Return sorted OTMSignal list
"""
import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from coding.core.strategy.otm.models.otm_config import OTMConfig
from coding.core.strategy.otm.models.otm_signal import OTMSignal
from coding.core.strategy.otm.signals.liquidity_gate import LiquidityGate
from coding.core.strategy.otm.signals.volatility_regime_gate import VolatilityRegimeGate
from coding.core.strategy.otm.signals.directional_scorer import DirectionalScorer
from coding.core.strategy.otm.signals.strike_expiry_optimizer import StrikeExpiryOptimizer
from coding.core.strategy.otm.scoring.kelly_sizer import KellySizer
from coding.service.strategy.otm.fetchers.dvol_fetcher import DVOLFetcher
from coding.service.strategy.otm.fetchers.stablecoin_fetcher import StablecoinFetcher
from coding.service.strategy.otm.fetchers.ibit_fetcher import IBITFetcher

logger = logging.getLogger(__name__)

_DELTA_CANDIDATE_MIN = 0.05
_DELTA_CANDIDATE_MAX = 0.45


class OTMFinderService:
    """
    Orchestrates the OTM contract finder pipeline for one or more assets.

    Dependencies are injected to allow testing without live API connections.
    """

    def __init__(
        self,
        config: OTMConfig,
        deribit_service=None,
        on_chain_service=None,
        repository=None,
    ) -> None:
        self._config = config
        self._deribit_service = deribit_service
        self._on_chain_service = on_chain_service
        self._repository = repository

        # Gates (pure computation, no external deps)
        self._gate1 = LiquidityGate(config)
        self._gate2 = VolatilityRegimeGate(config)
        self._gate3 = DirectionalScorer(config)
        self._gate4 = StrikeExpiryOptimizer(config)
        self._kelly = KellySizer(config)

        # Fetchers
        self._dvol_fetcher = DVOLFetcher()
        self._stablecoin_fetcher = StablecoinFetcher()
        self._ibit_fetcher = IBITFetcher()

    def find_signals(
        self,
        assets: List[str],
        direction: str = "auto",
        expiry_pref: str = "auto",
        gate2_override: bool = False,
        existing_positions: Optional[Dict[str, float]] = None,
    ) -> List[OTMSignal]:
        """
        Run the full OTM finder pipeline.

        Args:
            assets: ["BTC"], ["ETH"], or ["BTC", "ETH"]
            direction: forced direction or "auto" (Gate 3 decides)
            expiry_pref: forced expiry or "auto" (Gate 4 selects best)
            gate2_override: if True, scan even when Gate 2 < 40 (paper trading)
            existing_positions: {asset: usd_already_allocated_same_direction}

        Returns:
            List[OTMSignal] sorted descending by conviction_score.
        """
        if existing_positions is None:
            existing_positions = {}

        all_signals: List[OTMSignal] = []

        for asset in assets:
            try:
                signals = self._process_asset(
                    asset=asset,
                    direction=direction,
                    expiry_pref=expiry_pref,
                    gate2_override=gate2_override,
                    existing_same_direction_usd=existing_positions.get(asset, 0.0),
                )
                all_signals.extend(signals)
            except Exception as exc:
                logger.error("OTMFinderService: error processing %s: %s", asset, exc)

        all_signals.sort(key=lambda s: s.conviction_score, reverse=True)

        if all_signals:
            try:
                self._repository.save_otm_signals(all_signals)
            except Exception as exc:
                logger.error("Failed to save OTM signals: %s", exc)

        return all_signals

    def score_gate2(self, asset: str) -> dict:
        """
        Run only Gate 2 (DVOL fetch + VolatilityRegimeGate) and return the gate2 dict.

        Used by the GUI to refresh the live-conditions panel mid-run and by the
        tile auto-refresh timer without triggering the full 4-gate pipeline.

        Returns gate2 dict with keys: total_score, action, v1_score, v2v4_score,
        v3_score, garch_fcast_annualized. Returns {} on any error.
        """
        try:
            latest_dvol = self._dvol_fetcher.fetch_latest(asset)
            dvol_history = self._repository.get_dvol_history(asset)
            ohlcv_daily = self._repository.get_ohlcv_daily(asset)

            if self._on_chain_service is not None:
                on_chain = self._on_chain_service.fetch_and_analyze(asset)
                mw = getattr(on_chain, "market_wide_structured", {}) or {}
                vs = getattr(on_chain, "volatility_surface_structured", {}) or {}
                vrp_data = mw.get("vrp", {})
                atm_iv = vs.get("atm_iv", 0.60)
                rv_30d = vrp_data.get("rv_30d", atm_iv * 0.9)
                term_data = mw.get("iv_term_structure", None)
            else:
                atm_iv, rv_30d, term_data = 0.60, 0.54, None

            return self._gate2.score(
                dvol_history=self._normalize_dvol_history(dvol_history),
                current_dvol=latest_dvol or (max(dvol_history) if dvol_history else 70.0),
                atm_iv_30d=atm_iv,
                rv_30d_parkinson=rv_30d,
                ohlcv_daily=ohlcv_daily,
                term_structure_data=term_data,
            )
        except Exception as exc:
            logger.warning("score_gate2 failed for %s: %s", asset, exc)
            return {}

    @staticmethod
    def _normalize_dvol_history(dvol_history) -> list:
        """Normalize dvol_history to a plain list of floats.

        Repository may return list of floats or list of (datetime, float) tuples.
        """
        if dvol_history and isinstance(dvol_history[0], tuple):
            return [v for _, v in dvol_history]
        return list(dvol_history)

    def _process_asset(
        self,
        asset: str,
        direction: str,
        expiry_pref: str,
        gate2_override: bool,
        existing_same_direction_usd: float,
    ) -> List[OTMSignal]:
        """Run full pipeline for one asset. Returns list of OTMSignal."""
        logger.info("OTMFinderService: processing %s", asset)

        # Gate 1 runs FIRST (spec-compliant ordering: cheap filter eliminates candidates early)

        # Fetch options chain + Gate 1 filter
        chain = self._deribit_service.get_book_summary_by_currency(asset)
        underlying_price = (chain[0].get("underlying_price", 0.0) if chain else 0.0)

        candidates = [
            c for c in chain
            if _DELTA_CANDIDATE_MIN <= abs(c.get("delta", 0.0)) <= _DELTA_CANDIDATE_MAX
        ]

        liquid = []
        for c in candidates:
            c["asset"] = asset
            passed, reason = self._gate1.check(c)
            if not passed:
                logger.debug("Gate 1 FAIL %s: %s", c.get("instrument_name"), reason)
            else:
                liquid.append(c)

        if not liquid:
            logger.warning("%s: no liquid OTM candidates survived Gate 1", asset)
            return []

        # Fetch + update DVOL
        latest_dvol = self._dvol_fetcher.fetch_latest(asset)
        if latest_dvol is not None:
            try:
                conn = self._repository.get_connection()
                self._dvol_fetcher.save_to_db(
                    [(datetime.now(timezone.utc), latest_dvol)], asset, conn
                )
                conn.commit()
            except Exception as exc:
                logger.warning("Could not persist DVOL for %s: %s", asset, exc)

        # Fetch supporting data
        dvol_history = self._repository.get_dvol_history(asset)
        funding_history = self._repository.get_funding_rate_history(asset)
        pc_ratio_history = self._repository.get_pc_ratio_history(asset)
        rr25_history = self._repository.get_rr25_history(asset)
        ohlcv_daily = self._repository.get_ohlcv_daily(asset)
        stablecoin_inflow = self._stablecoin_fetcher.fetch_inflow_pct()
        ibit_pc = self._ibit_fetcher.fetch_pc_ratio() if asset == "BTC" else None
        ibit_avg = None  # TODO: compute 30d avg from stored history

        # Fetch on-chain analytics
        if self._on_chain_service is not None:
            analyzer = self._on_chain_service.fetch_and_analyze(asset)
            mw = getattr(analyzer, "market_wide_structured", {}) or {}
            vs = getattr(analyzer, "volatility_surface_structured", {}) or {}
            gex_dex = getattr(analyzer, "gex_dex_structured", {}) or {}
        else:
            mw, vs, gex_dex = {}, {}, {}

        funding_data = mw.get("perpetual_funding", {})
        term_data = mw.get("iv_term_structure", None)
        vrp_data = mw.get("vrp", {})
        atm_iv = vs.get("atm_iv", 0.60)
        rv_30d = vrp_data.get("rv_30d", atm_iv * 0.9)

        # Gate 2
        # dvol_history may be a list of floats or list of tuples — normalise to floats
        dvol_history_values = self._normalize_dvol_history(dvol_history)

        current_dvol = latest_dvol if latest_dvol is not None else (
            max(dvol_history_values) if dvol_history_values else 70.0
        )

        g2 = self._gate2.score(
            dvol_history=dvol_history_values,
            current_dvol=current_dvol,
            atm_iv_30d=atm_iv,
            rv_30d_parkinson=rv_30d,
            ohlcv_daily=ohlcv_daily,
            term_structure_data=term_data,
        )
        gate2_score = g2["total_score"]
        gate2_action = g2["action"]
        garch_fcast_annualized = g2.get("garch_fcast_annualized") or 0.05
        # Scale to 30-day move for Gate 4 breakeven filter
        garch_fcast = garch_fcast_annualized * math.sqrt(30.0 / 252.0)

        if gate2_action != "new_entries_allowed" and not gate2_override:
            logger.info("%s Gate 2 action=%s -- no new entries", asset, gate2_action)
            return []

        # Gate 3
        g3 = self._gate3.score(
            asset=asset,
            gex_dex=gex_dex,
            current_funding_rate=funding_data.get("current_rate", 0.0),
            funding_rate_history=funding_history,
            vol_surface=vs,
            rr25_history=rr25_history,
            pc_ratio_history=pc_ratio_history,
            block_trades=mw.get("block_trades", {"blocks_detected": False}),
            stablecoin_inflow_pct=stablecoin_inflow,
            ibit_pc_ratio=ibit_pc,
            ibit_pc_30d_avg=ibit_avg,
            ohlcv_daily=ohlcv_daily,
            spot_close=ohlcv_daily[-1]["close"] if ohlcv_daily else 0.0,
        )
        call_score = g3["call_score"]
        put_score = g3["put_score"]
        regime = g3["regime"]
        breakdown = g3["breakdown"]

        if direction == "auto":
            trade_direction = "call" if call_score >= put_score else "put"
        else:
            trade_direction = direction

        gate3_directional = call_score if trade_direction == "call" else put_score

        # Gate 4
        ranked = self._gate4.select(
            contracts=liquid,
            direction=trade_direction,
            call_score=call_score,
            put_score=put_score,
            gate2_score=gate2_score,
            garch_fcast_30d=garch_fcast,
            max_pain_strike=None,   # TODO: integrate CoinGlass fetcher
            spot_price=underlying_price,
            asset=asset,
        )

        if not ranked:
            logger.info("%s: no contracts survived Gate 4", asset)
            return []

        # Kelly sizer + build OTMSignal objects
        signals = []
        for c in ranked:
            sizing = self._kelly.compute_position_usd(
                gate2_score=gate2_score,
                gate3_directional_score=gate3_directional,
                existing_same_direction_usd=existing_same_direction_usd,
            )
            if sizing["position_usd"] == 0.0:
                continue

            conviction = sizing["conviction_score"]
            dte = c.get("dte", 14)
            tp = self._kelly.compute_take_profit(conviction, dte)

            signal = OTMSignal(
                signal_id=str(uuid4()),
                generated_at=datetime.now(timezone.utc),
                asset=asset,
                instrument_name=c.get("instrument_name", ""),
                direction=trade_direction,
                strike=float(c.get("strike", 0.0)),
                expiry=c.get("expiry", ""),
                dte=dte,
                expiry_category=c.get("expiry_category", "medium"),
                delta=float(c.get("delta", 0.0)),
                gamma=float(c.get("gamma", 0.0)),
                vega=float(c.get("vega", 0.0)),
                theta=float(c.get("theta", 0.0)),
                mark_iv=float(c.get("mark_iv", 0.0)),
                entry_premium=float(c.get("entry_premium", 0.0)),
                underlying_price=underlying_price,
                gate1_passed=True,
                gate2_score=gate2_score,
                gate3_call_score=call_score,
                gate3_put_score=put_score,
                gate3_directional_score=gate3_directional,
                conviction_score=conviction,
                d1_d7_score=breakdown.get("D1_D7", 0.0),
                d2_score=breakdown.get("D2", 0.0),
                d3_score=breakdown.get("D3", 0.0),
                d4_score=breakdown.get("D4", 0.0),
                d6_d9_score=breakdown.get("D6_D9", 0.0),
                d8_score=breakdown.get("D8", 0.0),
                d10_score=breakdown.get("D10", 0.0),
                ris_score=breakdown.get("RIS", 0.0),
                position_usd=sizing["position_usd"],
                p_win_prior=sizing["p_win_prior"],
                kelly_fraction=sizing["kelly_fraction"],
                take_profit_multiple=tp,
                stop_loss_pct=self._config.stop_loss_hard_floor_pct,
                time_stop_dte=max(1, dte // 2),
                vega_theta_ratio=c.get("vega_theta_ratio", 0.0),
                gamma_premium_ratio=c.get("gamma_premium_ratio", 0.0),
                breakeven_price=c.get("breakeven_price", 0.0),
                regime_flag=regime,
                gate2_suppressed=(gate2_action != "new_entries_allowed"),
            )
            signals.append(signal)
            existing_same_direction_usd += sizing["position_usd"]

        return signals
