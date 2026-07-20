"""
Shared liquid-chain builder for the defined-risk scanners. Groups raw
option-chain contracts (as returned by DeribitApiService.get_option_chain_
snapshot) by strike, keeping only strikes with both legs present, and
tags each leg's liquidity via defined_risk_candidate_builder.passes_liquidity.
"""
from typing import Any, Dict, List

from coding.service.scanner.defined_risk_candidate_builder import passes_liquidity


def build_liquid_chain(contracts: List[Dict[str, Any]], index_price: float) -> Dict[float, Dict[str, Any]]:
    """
    Returns:
        {strike: {call_bid, call_ask, call_ok, put_bid, put_ask, put_ok}}
        in USD terms (bid_usd/ask_usd, already provided by
        get_option_chain_snapshot) -- only for strikes with BOTH legs
        present. Liquidity (*_ok) is checked on the raw (non-USD)
        bid_price/ask_price, matching StraddleScanService's own convention.
    """
    by_strike: Dict[float, Dict[str, Any]] = {}
    for c in contracts:
        by_strike.setdefault(c["strike"], {})[c["option_type"]] = c

    liquid: Dict[float, Dict[str, Any]] = {}
    for strike, legs in by_strike.items():
        if "C" not in legs or "P" not in legs:
            continue
        call, put = legs["C"], legs["P"]
        liquid[strike] = {
            "call_bid": call.get("bid_usd"), "call_ask": call.get("ask_usd"),
            "call_ok": passes_liquidity(call.get("bid_price"), call.get("ask_price"), call.get("open_interest")),
            "put_bid": put.get("bid_usd"), "put_ask": put.get("ask_usd"),
            "put_ok": passes_liquidity(put.get("bid_price"), put.get("ask_price"), put.get("open_interest")),
        }
    return liquid
