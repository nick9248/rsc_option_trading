import logging
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig

logger = logging.getLogger(__name__)


class StrikeSelector:
    """
    Filters and ranks an options chain to find the optimal OTM call
    to buy after a displacement event.

    Filtering gates (applied in order):
    1. option_type must be "call"
    2. delta within [min_delta, max_delta]
    3. dte within [min_dte, max_dte]
    4. open_interest >= min_oi (asset-specific threshold)
    5. bid/ask IV spread relative to mid IV <= max_bid_ask_spread_relative

    Ranking: minimise composite score = delta distance from preferred_delta
             + DTE out-of-preferred-window penalty + IV spread penalty.

    Enrichment: adds premium_usd and three profit-target spot prices.
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

        Parameters
        ----------
        asset:
            "BTC" or "ETH" — determines which OI threshold to apply.
        options_chain:
            List of option dicts from the market data layer.
        current_price:
            Current spot/index price used as fallback for premium_usd calculation.

        Returns
        -------
        Enriched option dict with ``premium_usd`` and profit-target fields,
        or ``None`` if no contract passes all filters.
        """
        min_oi = self._config.min_oi_btc if asset == "BTC" else self._config.min_oi_eth
        candidates = [opt for opt in options_chain if self._passes_filters(opt, min_oi)]

        if not candidates:
            logger.warning("No qualifying OTM calls found for %s", asset)
            return None

        best = self._rank(candidates)
        return self._enrich(best, current_price)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _passes_filters(self, opt: dict, min_oi: int) -> bool:
        """Return True only if the option passes all liquidity and structure gates."""
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

        bid_iv = opt.get("bid_iv", 0.0) or 0.0
        ask_iv = opt.get("ask_iv", 0.0) or 0.0
        if bid_iv > 0 and ask_iv > 0:
            mid_iv = (bid_iv + ask_iv) / 2.0
            spread_relative = (ask_iv - bid_iv) / mid_iv
            if spread_relative > self._config.max_bid_ask_spread_relative:
                return False

        return True

    def _rank(self, candidates: list[dict]) -> dict:
        """
        Return the highest-ranked candidate using a composite score.

        Score components (lower is better):
        - delta distance from preferred_delta  (primary driver)
        - DTE out-of-preferred-window penalty  (flat 0.05 when outside window)
        - relative IV spread penalty           (scaled by 0.1 to keep it minor)
        """
        preferred_delta = self._config.preferred_delta
        preferred_dte_min = self._config.preferred_dte_min
        preferred_dte_max = self._config.preferred_dte_max

        def score(opt: dict) -> float:
            delta_dist = abs(opt.get("delta", 0.0) - preferred_delta)

            dte = opt.get("dte", 0)
            dte_penalty = 0.0 if preferred_dte_min <= dte <= preferred_dte_max else 0.05

            bid_iv = opt.get("bid_iv", 0.0) or 0.0
            ask_iv = opt.get("ask_iv", 0.0) or 0.0
            if bid_iv > 0 and ask_iv > 0:
                mid_iv = (bid_iv + ask_iv) / 2.0
                spread_penalty = (ask_iv - bid_iv) / mid_iv * 0.1
            else:
                spread_penalty = 0.0

            return delta_dist + dte_penalty + spread_penalty

        return min(candidates, key=score)

    def _enrich(self, opt: dict, current_price: float) -> dict:
        """
        Add USD-denominated premium and spot-price profit targets to the option dict.

        Deribit mark_price is quoted in BTC/ETH terms, so:
            premium_usd = mark_price × underlying_price

        Profit targets estimate the spot price at which the option would be
        worth 50 %, 100 %, or 200 % more than the entry premium (rough estimate;
        ignores theta/vega path dependency):
            target = strike + premium_usd × multiplier
        where multiplier = 1.5 / 2.0 / 3.0 respectively.
        """
        mark_price = opt.get("mark_price", 0.0) or 0.0
        underlying = opt.get("underlying_price", current_price) or current_price
        premium_usd = mark_price * underlying

        strike = opt.get("strike", 0.0)

        result = dict(opt)
        result["premium_usd"] = round(premium_usd, 2)
        result["target_50pct_price"] = round(strike + premium_usd * 1.5, 2)
        result["target_100pct_price"] = round(strike + premium_usd * 2.0, 2)
        result["target_200pct_price"] = round(strike + premium_usd * 3.0, 2)
        return result
