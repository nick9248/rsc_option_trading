"""
Signed delta-weighted, premium-weighted taker flow (HIRO analog).

institutional_metrics_spec.md section 6 / task C7. Pure: receives already-
fetched trade rows and recomputes per-trade signed BS delta --
``historical_trades`` has no delta column (verified section 6(a): 99.82% of
trades recomputable from the row's own iv/strike/index_price/expiration).
Never queries the database or the API -- the caller
(``DatabaseRepository.get_trades_for_delta_flow`` /
``ProspectiveCollector``) fetches once and injects the trades, mirroring the
core-purity convention already established by ``BuySellFlowAnalyzer`` and
``DealerInventoryCalculator`` at this same table.

Decision D8 (BINDING): signed delta is the PRIMARY metric (``hiro_usd``) --
buying a put must read as bearish flow, not bullish, which an unsigned
``|delta|`` formula would get backwards. The unsigned magnitude is emitted
alongside as ``gross_delta_usd`` (the brief's originally-specified formula),
so nothing is lost -- see ``FlowBucket``'s docstring.

Sign convention: ``taker_sign = +1`` for a 'buy' trade, ``-1`` for 'sell' --
matches the established convention at this same table
(``DealerInventoryCalculator`` / ``DatabaseRepository.
get_signed_taker_flow_by_strike``: ``SUM(CASE WHEN direction='buy' THEN
amount ELSE -amount END)``). ``hiro_usd`` positive = net bullish delta-
notional (dealers must buy the underlying to stay hedged); this is NOT the
same sign semantics as the existing BUY/SELL FLOW ANALYSIS section's
``net_flow = buy_volume - sell_volume`` (a raw CONTRACT-count metric where
buying a put also reads positive) -- the two are deliberately different
views sharing the same underlying taker-direction sign, per task-C7-brief.md's
"sign consistency" requirement: the taker-direction sign itself must match
the established convention (it does); the delta-adjusted RESULT sign is
intentionally different for puts, exactly as D8 specifies.

Reuse, not reinvention: BS delta comes from the existing, already-tested
``BlackScholesCalculator.calculate_greeks`` (including its
``_expired_option_greeks`` tau<=0 branch and ``parse_instrument_name``'s
month-map expiry parsing) -- nothing here reimplements Black-Scholes math or
expiry-string parsing.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.results.delta_flow_results import EnrichedTrade, FlowBucket

logger = logging.getLogger(__name__)

_ALL_KEY = "ALL"


class DeltaFlowCalculator:
    """
    Pure: raw trade rows -> per-trade signed delta-notional and hourly
    aggregates, grouped per expiration plus a currency-level 'ALL' rollup.
    """

    def __init__(self, black_scholes: Optional[BlackScholesCalculator] = None):
        self.black_scholes = black_scholes or BlackScholesCalculator()

    def enrich_trade(self, trade: Dict[str, Any]) -> Optional[EnrichedTrade]:
        """
        Recompute one trade's signed BS delta and its contribution to the
        hourly aggregates.

        Returns ``None`` (the trade must be counted in the caller's
        ``skipped_count``, never silently dropped without a trace) for any
        of the following -- see the module's test file for the full
        enumeration this was verified against:

        - ``iv`` is ``None`` or ``<= 0`` (spec section 6(a): 0.18% of
          trades). Gated HERE, before calling
          ``BlackScholesCalculator.calculate_greeks`` -- that method
          defaults an invalid IV to 0.01 internally (a convenience for its
          live-chain-greeks caller, which must always render SOME greeks
          for the GUI). Reusing that default here would silently
          manufacture a normal-looking delta -- and therefore a normal-
          looking ``hiro_usd`` -- for a trade whose IV is actually missing,
          exactly the "insufficient data reads as a clean number" failure
          task-C7-brief.md warns about.
        - ``strike`` is ``None``, ``option_type`` not in ``("C", "P")``,
          ``direction`` not in ``("buy", "sell")``, ``amount``/
          ``index_price`` is ``None`` or ``<= 0``, ``expiration`` is
          falsy, ``instrument_name`` is missing, or ``trade_timestamp`` is
          missing. Defensive: ``historical_trades`` is verified 0-null on
          all of these over the last 7 days (73,623+ BTC+ETH trades), but
          this mirrors the ``strike IS NOT NULL AND direction IS NOT NULL``
          filter already applied at this table by
          ``get_trades_for_flow_analysis``/``get_signed_taker_flow_by_strike``.
        - ``instrument_name`` fails to parse
          (``BlackScholesCalculator.parse_instrument_name`` returns
          ``None``) -- without a parsed ``expiry_time`` there is no tau to
          compute.

        ``tau <= 0`` (trade at or after the option's 08:00 UTC expiry) is
        explicitly NOT a skip condition:
        ``BlackScholesCalculator.calculate_greeks`` already routes that
        case to ``_expired_option_greeks`` (intrinsic delta), reused as-is.

        Tau is anchored on the TRADE'S OWN timestamp, never wall-clock
        "now" -- a deliberate deviation from institutional_metrics_spec.md
        section 6(c)'s literal ``enrich_trade(self, trade, now_utc)``
        signature. The formula (section 6(b)) is
        ``tau_i = (expiry_08:00_UTC - trade_timestamp_UTC) / years`` --
        relative to when the trade happened, not to whenever this method
        happens to run. Taking a ``now_utc`` parameter would be correct
        only for a trade that just occurred and silently wrong (understated
        tau, mispriced delta) for every trade in a backfill or a delayed
        daemon run, the further ``trade_timestamp`` drifts from the actual
        call time. Naive-UTC on both sides of the subtraction, matching
        c4bff4e's fix for the identical class of bug in the live-greeks
        path (``prospective_collector.py``'s BS-fallback greeks).
        """
        iv = trade.get("iv")
        if iv is None or float(iv) <= 0:
            return None

        strike = trade.get("strike")
        option_type = (trade.get("option_type") or "").upper()
        direction = trade.get("direction")
        amount = trade.get("amount")
        index_price = trade.get("index_price")
        expiration = trade.get("expiration")
        instrument_name = trade.get("instrument_name")
        trade_timestamp_ms = trade.get("trade_timestamp")

        if strike is None:
            return None
        if option_type not in ("C", "P"):
            return None
        if direction not in ("buy", "sell"):
            return None
        if amount is None or float(amount) <= 0:
            return None
        if index_price is None or float(index_price) <= 0:
            return None
        if not expiration:
            return None
        if not instrument_name:
            return None
        if trade_timestamp_ms is None:
            return None

        parsed = self.black_scholes.parse_instrument_name(instrument_name)
        if parsed is None:
            return None

        strike = float(strike)
        amount = float(amount)
        index_price = float(index_price)
        iv_decimal = float(iv) / 100.0
        price = trade.get("price")
        price = float(price) if price is not None else 0.0

        trade_time_utc = datetime.fromtimestamp(
            float(trade_timestamp_ms) / 1000.0, tz=timezone.utc
        ).replace(tzinfo=None)
        tau_years = self.black_scholes.calculate_time_to_expiry(trade_time_utc, parsed["expiry_time"])

        greeks = self.black_scholes.calculate_greeks(
            spot_price=index_price,
            strike_price=strike,
            time_to_expiry=tau_years,
            implied_volatility=iv_decimal,
            option_type="call" if option_type == "C" else "put",
        )
        delta = greeks["delta"]

        contribution = self.compute_signed_contribution(
            direction=direction, delta=delta, amount=amount, index_price=index_price, price=price,
        )

        return EnrichedTrade(
            expiration=str(expiration),
            direction=direction,
            delta=delta,
            amount=amount,
            hiro_usd=contribution["hiro_usd"],
            premium_usd=contribution["premium_usd"],
            gross_delta_usd=contribution["gross_delta_usd"],
            signed_contracts=contribution["signed_contracts"],
        )

    @staticmethod
    def compute_signed_contribution(
        direction: str,
        delta: float,
        amount: float,
        index_price: float,
        price: float = 0.0,
    ) -> Dict[str, float]:
        """
        Pure sign math for one trade's contribution -- deliberately
        separated from ``enrich_trade``'s BS recompute so it can be tested
        directly against spec T6.1's hand-computed table (given deltas,
        not BS-derived ones).

        Formulas (spec section 6(b)):
            taker_sign = +1 if direction == 'buy' else -1
            hiro_usd   = taker_sign * delta * amount * index_price
            premium_usd = taker_sign * price * amount * index_price
            gross_delta_usd = |delta| * amount * index_price
            signed_contracts = taker_sign * amount
        """
        taker_sign = 1.0 if direction == "buy" else -1.0
        return {
            "hiro_usd": taker_sign * delta * amount * index_price,
            "premium_usd": taker_sign * price * amount * index_price,
            "gross_delta_usd": abs(delta) * amount * index_price,
            "signed_contracts": taker_sign * amount,
        }

    def compute_hourly_buckets(self, trades: List[Dict[str, Any]]) -> Dict[str, FlowBucket]:
        """
        Enrich every trade and aggregate into one ``FlowBucket`` per
        expiration that appeared in ``trades`` (enriched OR skipped), plus
        a currency-level ``"ALL"`` rollup.

        Returns ``{}`` for an empty ``trades`` list -- no fabricated "ALL"
        entry. Whether an all-zero "ALL" row should still be PERSISTED for
        an hour with genuinely zero trades (to distinguish "checked, found
        nothing" from "the daemon never ran this hour") is a PERSISTENCE
        decision, made by the caller (``ProspectiveCollector.
        _persist_delta_flow``), not this pure calculator -- mirrors the
        project's Core/Service layering (Core computes, Service decides
        what to do with an absence).

        An expiration that never appears in ``trades`` at all never gets a
        bucket, fabricated or otherwise -- unlike C3's currency-wide-
        coverage-proxy bug (a currency-level signal standing in for a
        per-expiry check), there is no "current chain" cross-reference
        available here to synthesize a false zero-trade entry for a
        listed-but-untraded expiry from. A trade with a falsy/missing
        ``expiration`` (verified 0 occurrences in current data, defensive
        only) is counted in "ALL"'s ``skipped_count`` but cannot be
        attributed to any per-expiration bucket, since there is no key to
        attribute it to.

        Design note on ``net_contracts``/``gross_contracts``: these do not
        themselves need a valid delta (they are just signed/unsigned
        ``amount`` sums, like ``get_signed_taker_flow_by_strike`` already
        computes without IV). They are still computed over the SAME
        successfully-enriched trade set as the delta-dependent columns
        (never over a larger set that includes IV-skipped trades) so that
        one ``trade_count``/``skipped_count`` pair unambiguously describes
        every column in the row -- a second, larger denominator for just
        two of the ten columns would make ``trade_count`` lie about what
        backs ``net_contracts``.  If a "never lose contract-count data to a
        bad IV row" view is ever needed, ``get_signed_taker_flow_by_strike``
        already serves it independently of this table.
        """
        accumulators: Dict[str, Dict[str, Any]] = {}

        def _get_acc(key: str) -> Dict[str, Any]:
            if key not in accumulators:
                accumulators[key] = {
                    "hiro_usd": 0.0, "premium_usd": 0.0, "gross_delta_usd": 0.0,
                    "net_contracts": 0.0, "gross_contracts": 0.0,
                    "trade_count": 0, "buy_count": 0, "sell_count": 0, "skipped_count": 0,
                }
            return accumulators[key]

        for trade in trades:
            expiration = trade.get("expiration")
            enriched = self.enrich_trade(trade)

            if enriched is None:
                if expiration:
                    _get_acc(str(expiration))["skipped_count"] += 1
                _get_acc(_ALL_KEY)["skipped_count"] += 1
                continue

            for key in (enriched.expiration, _ALL_KEY):
                acc = _get_acc(key)
                acc["hiro_usd"] += enriched.hiro_usd
                acc["premium_usd"] += enriched.premium_usd
                acc["gross_delta_usd"] += enriched.gross_delta_usd
                acc["net_contracts"] += enriched.signed_contracts
                acc["gross_contracts"] += enriched.amount
                acc["trade_count"] += 1
                if enriched.direction == "buy":
                    acc["buy_count"] += 1
                else:
                    acc["sell_count"] += 1

        return {key: FlowBucket(expiration=key, **values) for key, values in accumulators.items()}
