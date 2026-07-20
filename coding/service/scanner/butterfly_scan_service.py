"""
Long call butterfly scanner service. Regime-gated defined-risk complement to
StraddleScanService/IronCondorScanService -- see
docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md for the
full evaluation history (backtest results, the credit-vs-max-loss return-
convention bug, the regime-gate validation) before touching the scoring math.

THE ONE DATA SOURCE RULE (same as StraddleScanService/IronCondorScanService):
fetches market data exclusively via
DeribitApiService.get_option_chain_snapshot(currency).
"""
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.scanner import realized_vol
from coding.service.scanner.defined_risk_candidate_builder import build_butterfly_candidates
from coding.service.scanner.defined_risk_chain import build_liquid_chain
from coding.service.scanner.regime_gate_service import RegimeGateService

logger = logging.getLogger(__name__)


class ButterflyScanService:
    MIN_DTE = 5
    MAX_DTE = 400

    def __init__(
        self,
        api_service: Optional[DeribitApiService] = None,
        repository: Optional[DatabaseRepository] = None,
        regime_gate_service: Optional[RegimeGateService] = None,
    ):
        """
        Args:
            api_service: Injected Deribit API service (tests / callers that
                already manage a connection). If None, scan() opens and
                closes its own DeribitApiService for the duration of the call.
            repository: Injected DatabaseRepository (tests). If None, a
                default one is created lazily -- only when actually needed
                (DTE-matched RV lookup, or a self-computed regime snapshot)
                -- matching IronCondorScanService's own lazy-DB convention.
                Never constructed eagerly here, so instantiating this service
                (or calling scan() with an explicit `regime`) never requires
                a live DB connection.
            regime_gate_service: Injected RegimeGateService (tests). If None,
                a default one is created lazily inside scan(), and only when
                `regime` isn't passed to scan() -- see the `regime` arg below.
        """
        self.api_service = api_service
        self.repository = repository
        self.regime_gate_service = regime_gate_service

    def scan(self, currency: str, regime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Run one long call butterfly scan for a currency.

        Args:
            regime: Pre-computed RegimeGateService.compute() result. Pass
                this when the caller (ProspectiveCollector) already computed
                it once for the cycle, shared with IronCondorScanService, to
                avoid a duplicate onchain_analysis_snapshots/ohlcv query. If
                None, computes its own (for standalone/GUI manual-trigger use).

        Returns:
            {as_of, currency, index_price, expiries: [...], excluded: [...]}
            -- same top-level shape as IronCondorScanService.scan(). Each
            expiries entry: {expiry, dte, F, regime, best, candidates}.
        """
        snapshot = self._fetch_snapshot(currency)
        as_of = snapshot["as_of"]
        index_price = snapshot["index_price"]
        futures_by_expiry = snapshot["futures_by_expiry"]
        if regime is None:
            regime_service = self.regime_gate_service or RegimeGateService(repository=self.repository)
            regime = regime_service.compute(currency, as_of=as_of.replace(tzinfo=None))

        contracts_by_expiry: Dict[str, List[Dict[str, Any]]] = {}
        for contract in snapshot["contracts"]:
            contracts_by_expiry.setdefault(contract["expiry"], []).append(contract)

        expiry_entries: List[Dict[str, Any]] = []
        excluded: List[Dict[str, Any]] = []

        for expiry, expiry_contracts in contracts_by_expiry.items():
            dte = expiry_contracts[0]["dte"]
            if dte < self.MIN_DTE or dte > self.MAX_DTE:
                excluded.append({"expiry": expiry, "dte": dte,
                                  "reason": f"DTE {dte:.1f} outside [{self.MIN_DTE},{self.MAX_DTE}]"})
                continue

            future_price = futures_by_expiry.get(expiry)
            if not future_price:
                excluded.append({"expiry": expiry, "dte": dte, "reason": "no future price available"})
                continue

            liquid = build_liquid_chain(expiry_contracts, index_price)
            if not liquid:
                excluded.append({"expiry": expiry, "dte": dte, "reason": "no strike has both legs quoted"})
                continue

            sigma_sqrt_t = self._sigma_sqrt_t(currency, dte, as_of)
            candidates = build_butterfly_candidates(liquid, future_price, sigma_sqrt_t)
            if not candidates:
                excluded.append({"expiry": expiry, "dte": dte, "reason": "no butterfly candidates constructed"})
                continue

            for c in candidates:
                c["deribit_url"] = f"https://www.deribit.com/options/{currency}"
            candidates.sort(key=lambda c: -(c["ev"] if c["ev"] is not None else float("-inf")))

            expiry_entries.append({
                "expiry": expiry, "dte": dte, "F": future_price,
                "regime": regime, "best": candidates[0], "candidates": candidates,
            })

        expiry_entries.sort(key=lambda e: -(e["best"]["ev"] if e["best"]["ev"] is not None else float("-inf")))
        excluded.sort(key=lambda x: x["expiry"])

        return {"as_of": as_of, "currency": currency, "index_price": index_price,
                "expiries": expiry_entries, "excluded": excluded}

    def _fetch_snapshot(self, currency: str) -> Dict[str, Any]:
        if self.api_service is not None:
            return self.api_service.get_option_chain_snapshot(currency)
        with DeribitApiService() as api:
            return api.get_option_chain_snapshot(currency)

    def _sigma_sqrt_t(self, currency: str, dte: float, as_of: datetime) -> Optional[float]:
        """DTE-matched RV (not the regime's fixed 10d/30d) -- the sigma input for this
        specific expiry's candidate probability/EV scoring, matching IronCondorScanService's
        own RV-implied methodology (min_pnl_score)."""
        repo = self.repository or DatabaseRepository()
        window_days = realized_vol.dte_matched_window(dte)
        rv = realized_vol.compute_realized_vol(repo, currency, window_days, as_of.replace(tzinfo=None))
        if not rv:
            return None
        return (rv / 100.0) * math.sqrt(dte / 365.0)

    def generate_payoff_chart(self, scan_result: Dict[str, Any], expiry: str, k1: float, k2: float, k3: float) -> str:
        """Same contract as IronCondorScanService.generate_payoff_chart -- see that docstring."""
        from coding.core.analytics.chart_generator import generate_butterfly_payoff_chart, inject_hover_js, inject_theme_toggle_js, save_chart
        from pathlib import Path

        entry = next((e for e in scan_result["expiries"] if e["expiry"] == expiry), None)
        if entry is None:
            raise ValueError(f"Expiry {expiry} not found in scan result")
        candidate = next((c for c in entry["candidates"] if c["k1"] == k1 and c["k2"] == k2 and c["k3"] == k3), None)
        if candidate is None:
            raise ValueError(f"Candidate {k1}/{k2}/{k3} not found among {expiry} candidates")

        fig = generate_butterfly_payoff_chart(scan_result["currency"], expiry, entry["dte"], entry["F"], candidate)
        filename = f"butterfly_{scan_result['currency']}_{expiry}_{int(k2)}"
        path = save_chart(fig, filename, subfolder="butterfly", save_png=False)
        inject_hover_js(Path(path))
        inject_theme_toggle_js(Path(path), fig)
        return path
