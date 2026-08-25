"""
Result models for signed delta-weighted, premium-weighted taker flow
(institutional_metrics_spec.md section 6 / task C7, the HIRO analog).

Frozen dataclasses, mirroring the pattern established by
``dealer_inventory_results.py``/``flow_results.py``.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EnrichedTrade:
    """
    One trade's recomputed signed BS delta and its contribution to the
    hourly aggregates, produced by ``DeltaFlowCalculator.enrich_trade``.

    Never constructed for a trade that failed enrichment (see
    ``DeltaFlowCalculator.enrich_trade``'s skip conditions) -- there is no
    "empty"/"zero" sentinel value; a trade either produces one of these or
    is counted in ``FlowBucket.skipped_count`` instead.
    """

    expiration: str
    direction: str  # "buy" | "sell" (the taker's side, Deribit convention)
    delta: float  # signed BS delta, recomputed from the trade's own iv/strike/index_price
    amount: float
    hiro_usd: float  # signed: taker_sign * delta * amount * index_price
    premium_usd: float  # signed: taker_sign * price * amount * index_price
    gross_delta_usd: float  # unsigned: |delta| * amount * index_price
    signed_contracts: float  # taker_sign * amount


@dataclass(frozen=True)
class FlowBucket:
    """
    One hourly (or, at report time, summed-over-N-hours) aggregate row --
    one per expiration that had at least one trade in the window, plus a
    currency-level ``expiration == "ALL"`` rollup. Mirrors
    ``flow_delta_hourly``'s columns exactly (migration 021 / dbf6803).

    ``hiro_usd`` positive = net bullish delta-notional (Decision D8: SIGNED
    delta is the primary metric -- selling a put or buying a call both
    register positive; buying a put or selling a call both register
    negative). ``gross_delta_usd`` is the unsigned hedging-impact magnitude
    (the brief's originally-specified ``|delta|`` formula), emitted
    alongside so nothing is lost.

    ``trade_count``/``skipped_count`` partition ALL trades seen in the
    window for this bucket's key into exactly two buckets -- every other
    numeric field here (including ``net_contracts``/``gross_contracts``,
    which do not themselves depend on delta/iv) is computed over the SAME
    ``trade_count`` set, not a larger one, so ``trade_count`` unambiguously
    describes what backs every column in the row. See
    ``DeltaFlowCalculator.compute_hourly_buckets``'s docstring for why this
    was a judgment call.
    """

    expiration: str  # e.g. "27MAR26", or "ALL" for the currency-level rollup
    hiro_usd: float
    premium_usd: float
    gross_delta_usd: float
    net_contracts: float
    gross_contracts: float
    trade_count: int
    buy_count: int
    sell_count: int
    skipped_count: int

    @property
    def skip_rate(self) -> Optional[float]:
        """
        ``skipped_count / (trade_count + skipped_count)``, or ``None`` when
        the denominator is zero (this bucket saw literally zero trades --
        there is nothing to compute a rate over, and 0.0 would falsely read
        as "checked, and everything passed" rather than "nothing was here
        to check"). Callers must render the ``None`` case distinctly from a
        real 0.0 skip rate.
        """
        total = self.trade_count + self.skipped_count
        if total == 0:
            return None
        return self.skipped_count / total
