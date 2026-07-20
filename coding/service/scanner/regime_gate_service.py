"""
Regime snapshot for the defined-risk scanners: net GEX (market-wide, summed
across a currency's expirations) and RV term structure (RV_10d vs RV_30d).

IMPORTANT (read before changing gate_pass's definition): validated
2026-07-20 via scripts/validate_regime_gate.py against 15-16 historical
iron condor/butterfly outcomes. The "calm regime" gate (net_gex>0 AND
rv_ratio<1) came back BACKWARDS in that small sample -- gate-passing
entries did WORSE than gate-failing ones. gate_pass is still computed here
using the ORIGINAL theoretically-motivated definition on purpose, so a
larger live sample (accumulated via defined_risk_scan_history) can judge
the hypothesis cleanly. Do not flip this definition based on the small
backtest sample. See
docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from coding.core.database.repository import DatabaseRepository
from coding.service.scanner import realized_vol

RV_10D_WINDOW = 10
RV_30D_WINDOW = 30


class RegimeGateService:
    def __init__(self, repository: Optional[DatabaseRepository] = None):
        self.repo = repository or DatabaseRepository()

    def compute(self, currency: str, as_of: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Returns:
            {net_gex, rv_10d, rv_30d, rv_ratio, gate_pass}. Any field may be
            None if the underlying data isn't available yet; gate_pass is
            False whenever either input is None (never raises, never
            defaults to True on missing data).
        """
        as_of = as_of or datetime.utcnow()
        rv_10d = realized_vol.compute_realized_vol(self.repo, currency, RV_10D_WINDOW, as_of)
        rv_30d = realized_vol.compute_realized_vol(self.repo, currency, RV_30D_WINDOW, as_of)
        rv_ratio = (rv_10d / rv_30d) if (rv_10d and rv_30d) else None
        net_gex = self._compute_net_gex(currency, as_of)
        gate_pass = bool(net_gex is not None and net_gex > 0 and rv_ratio is not None and rv_ratio < 1)
        return {
            "net_gex": net_gex, "rv_10d": rv_10d, "rv_30d": rv_30d,
            "rv_ratio": rv_ratio, "gate_pass": gate_pass,
        }

    def _compute_net_gex(self, currency: str, as_of: datetime) -> Optional[float]:
        """Market-wide net GEX: SUM(total_net_gex) across all of currency's
        expirations at the latest onchain_analysis_snapshots hour <= as_of."""
        with self.repo._db_cursor() as cursor:
            cursor.execute(
                "SELECT snapshot_hour FROM onchain_analysis_snapshots "
                "WHERE currency=%s AND snapshot_hour <= %s ORDER BY snapshot_hour DESC LIMIT 1",
                (currency, as_of),
            )
            hour_row = cursor.fetchone()
            if hour_row is None:
                return None
            cursor.execute(
                "SELECT SUM(total_net_gex) FROM onchain_analysis_snapshots "
                "WHERE currency=%s AND snapshot_hour=%s",
                (currency, hour_row[0]),
            )
            gex_row = cursor.fetchone()
            return float(gex_row[0]) if gex_row and gex_row[0] is not None else None
