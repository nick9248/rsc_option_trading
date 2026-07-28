"""
Historical normalization framework (institutional_metrics_spec.md section 1).

Turns a metric's raw current value into a percentile + z-score + regime
label against its own trailing history, so a report reads "where does this
sit relative to its own recent distribution" instead of a bare number
compared to an unrelated equity-derived threshold.

Pure math only: takes plain lists of floats, never touches psycopg2,
requests, or any repository. Everything else (repository queries, service
wiring, report rendering) feeds this class -- see
``coding/core/database/repository.py:get_metric_history`` and
``OnChainAnalysisService._build_normalized_metrics`` for the DB/service side.
"""

from dataclasses import dataclass
from math import sqrt
from typing import Dict, List, Optional, Sequence

MIN_OBS = 30
"""Below this many observations, percentile/z are None -- the honest answer
for "not enough history for a meaningful comparison" (spec T1.3)."""


@dataclass(frozen=True)
class NormalizedMetric:
    """One metric's value plus its 30d/90d percentile, z-score, and regime."""

    name: str
    value: float
    percentile_30d: Optional[float]
    z_30d: Optional[float]
    percentile_90d: Optional[float]
    z_90d: Optional[float]
    regime_30d: Optional[str]  # ladder applied to percentile_30d
    n_30d: int
    n_90d: int
    sufficient: bool  # n_30d >= MIN_OBS
    unit: str  # "USD" | "ratio" | "vol pts" | "%" | "coins"


@dataclass(frozen=True)
class MetricSpec:
    """One metric's raw inputs, batched through ``normalize_many``."""

    name: str
    value: float
    history_30d: Sequence[float]
    history_90d: Sequence[float]
    unit: str


class HistoricalNormalizer:
    """
    Computes percentile, z-score, and regime label for a metric's current
    value against its own trailing history (institutional_metrics_spec.md
    section 1(b)).

    Formulas (given trailing window W of observations x_1..x_n, oldest to
    newest, EXCLUDING the current value, and the current value v):

        percentile(v, W) = 100 * (count(x < v) + 0.5 * count(x == v)) / n
        mean             = (1/n) sum(x)
        sd               = sqrt((1/n) sum((x - mean)^2))   [population, ddof=0]
        z(v, W)          = (v - mean) / sd   if sd > 0 else None

    Mid-rank percentile (not strict `<`) so a constant series returns 50,
    not 0 -- the honest "where does this sit" answer when the distribution
    is degenerate. ddof=0 because the window is the complete observed
    history for that lookback, not a sample drawn from it.
    """

    MIN_OBS = MIN_OBS

    # Regime ladder: percentile -> label. Checked high-to-low; a single
    # ladder applied to every metric, no per-metric folklore thresholds.
    REGIME_BANDS = (
        (95.0, "EXTREME HIGH"),
        (80.0, "HIGH"),
        (60.0, "ELEVATED"),
        (40.0, "NORMAL"),
        (20.0, "SUBDUED"),
        (5.0, "LOW"),
    )
    REGIME_FLOOR_LABEL = "EXTREME LOW"

    @staticmethod
    def percentile(value: float, series: Sequence[float]) -> Optional[float]:
        """Mid-rank percentile of ``value`` within ``series``. None if empty."""
        n = len(series)
        if n == 0:
            return None
        below = sum(1 for x in series if x < value)
        equal = sum(1 for x in series if x == value)
        return 100.0 * (below + 0.5 * equal) / n

    @staticmethod
    def zscore(value: float, series: Sequence[float]) -> Optional[float]:
        """Population z-score of ``value`` within ``series``.

        None if ``series`` is empty or has zero variance (a constant
        series has no meaningful "how many standard deviations away").
        """
        n = len(series)
        if n == 0:
            return None
        mean = sum(series) / n
        variance = sum((x - mean) ** 2 for x in series) / n
        sd = sqrt(variance)
        if sd == 0.0:
            return None
        return (value - mean) / sd

    @classmethod
    def regime_label(cls, percentile: Optional[float]) -> Optional[str]:
        """Map a percentile to its regime label via the shared ladder."""
        if percentile is None:
            return None
        for floor, label in cls.REGIME_BANDS:
            if percentile >= floor:
                return label
        return cls.REGIME_FLOOR_LABEL

    def normalize(
        self,
        name: str,
        value: float,
        history_30d: Sequence[float],
        history_90d: Sequence[float],
        unit: str,
    ) -> NormalizedMetric:
        """
        Build a ``NormalizedMetric`` for one value against its 30d and 90d
        trailing history.

        Args:
            name: Metric name (e.g. "net_gex", "pcr_oi").
            value: Current reading. Signed metrics that legitimately cross
                zero (net GEX, RR, funding) are normalized on the raw signed
                value, never abs(value).
            history_30d: Trailing 30-day history, oldest-first, current
                value excluded, NULLs already dropped.
            history_90d: Trailing 90-day history, same conventions.
            unit: Display unit ("USD" | "ratio" | "vol pts" | "%" | "coins").

        Returns:
            NormalizedMetric. When len(history_30d) < MIN_OBS,
            percentile_30d/z_30d/regime_30d are None and sufficient=False,
            but ``value`` and both ``n_*`` counts are still populated.
        """
        n_30d = len(history_30d)
        n_90d = len(history_90d)
        sufficient = n_30d >= self.MIN_OBS

        if sufficient:
            percentile_30d = self.percentile(value, history_30d)
            z_30d = self.zscore(value, history_30d)
            regime_30d = self.regime_label(percentile_30d)
        else:
            percentile_30d = None
            z_30d = None
            regime_30d = None

        if n_90d >= self.MIN_OBS:
            percentile_90d = self.percentile(value, history_90d)
            z_90d = self.zscore(value, history_90d)
        else:
            percentile_90d = None
            z_90d = None

        return NormalizedMetric(
            name=name,
            value=value,
            percentile_30d=percentile_30d,
            z_30d=z_30d,
            percentile_90d=percentile_90d,
            z_90d=z_90d,
            regime_30d=regime_30d,
            n_30d=n_30d,
            n_90d=n_90d,
            sufficient=sufficient,
            unit=unit,
        )

    def normalize_many(self, specs: List[MetricSpec]) -> Dict[str, NormalizedMetric]:
        """Normalize a batch of ``MetricSpec``s, keyed by metric name."""
        return {
            spec.name: self.normalize(
                name=spec.name,
                value=spec.value,
                history_30d=spec.history_30d,
                history_90d=spec.history_90d,
                unit=spec.unit,
            )
            for spec in specs
        }
