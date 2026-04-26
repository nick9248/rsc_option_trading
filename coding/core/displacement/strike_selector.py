import logging
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig

logger = logging.getLogger(__name__)


class StrikeSelector:
    """
    Filters and ranks options chain to find the optimal OTM call to buy
    after a displacement event.
    """

    def __init__(self, config: DisplacementConfig):
        self._config = config

    def select(
        self,
        asset: str,
        options_chain: list[dict],
        current_price: float,
    ) -> Optional[dict]:
        """
        Select the best OTM call from the options chain.

        Returns enriched option dict with profit targets, or None if no contract qualifies.
        """
        min_oi = self._config.min_oi_btc if asset == "BTC" else self._config.min_oi_eth
        candidates = [
            opt for opt in options_chain
            if self._passes_filters(opt, min_oi)
        ]

        if not candidates:
            logger.warning(f"No qualifying OTM calls found for {asset}")
            return None

        best = self._rank(candidates)
        return self._enrich(best, current_price)

    def _passes_filters(self, opt: dict, min_oi: int) -> bool:
        """
        Filter options by:
        - Option type: calls only
        - Delta: 0.10–0.20
        - DTE: 90–270 days
        - Open interest: meets minimum
        - Bid/ask spread: < 8% relative (requires two-sided quote)
        """
        if opt.get("option_type") != "call":
            return False
        delta = opt.get("delta", 0.0)
        if not (self._config.min_delta <= delta <= self._config.max_delta):
            return False
        dte = opt.get("dte", 0)
        if not (self._config.min_dte <= dte <= self._config.max_dte):
            return False
        oi = opt.get("open_interest", 0.0) or 0.0
        if oi < min_oi:
            return False

        # FIX: Reject illiquid contracts with missing bid or ask quotes
        bid_iv = opt.get("bid_iv", 0.0) or 0.0
        ask_iv = opt.get("ask_iv", 0.0) or 0.0
        if bid_iv <= 0 or ask_iv <= 0:
            return False  # no two-sided quote = illiquid

        mid_iv = (bid_iv + ask_iv) / 2.0
        spread_relative = (ask_iv - bid_iv) / mid_iv
        if spread_relative > self._config.max_bid_ask_spread_relative:
            return False
        return True

    def _rank(self, candidates: list[dict]) -> dict:
        """
        Rank candidates by:
        1. Delta closest to 0.15 (sweet spot)
        2. Tiebreaker: lowest bid/ask spread
        3. DTE preference: favor 120–180 days
        """
        preferred = self._config.preferred_delta
        preferred_dte_min = self._config.preferred_dte_min
        preferred_dte_max = self._config.preferred_dte_max

        def score(opt: dict) -> float:
            delta_dist = abs(opt.get("delta", 0.0) - preferred)
            dte = opt.get("dte", 0)
            dte_penalty = 0.0 if preferred_dte_min <= dte <= preferred_dte_max else 0.05
            bid_iv = opt.get("bid_iv", 0.0) or 0.0
            ask_iv = opt.get("ask_iv", 0.0) or 0.0
            # FIX: Check both quotes exist before computing spread
            if bid_iv > 0 and ask_iv > 0:
                mid_iv = (bid_iv + ask_iv) / 2.0
                spread_penalty = (ask_iv - bid_iv) / mid_iv * 0.1
            else:
                spread_penalty = 0.0
            # Lower score = better
            return delta_dist + dte_penalty + spread_penalty

        return min(candidates, key=score)

    def _enrich(self, opt: dict, current_price: float) -> dict:
        """
        Enrich selected option with:
        - Premium in USD
        - Profit targets for 50/100/200% gain
        """
        mark_price = opt.get("mark_price", 0.0)
        underlying = opt.get("underlying_price", current_price) or current_price
        premium_usd = mark_price * underlying

        strike = opt.get("strike", 0.0)

        result = dict(opt)
        result["premium_usd"] = round(premium_usd, 2)
        # Profit targets: at what spot price will the option realize these gains?
        # Breakeven: strike + premium_usd (in spot terms)
        # 50% gain: premium doubles → strike + premium_usd * 1.5 in index terms
        result["target_50pct_price"] = round(strike + premium_usd * 1.5, 2)
        result["target_100pct_price"] = round(strike + premium_usd * 2.0, 2)
        result["target_200pct_price"] = round(strike + premium_usd * 3.0, 2)

        return result
