"""
OTM (Out-of-The-Money) contract finder service.

This module provides tools for finding optimal OTM contracts using multi-gate scoring:
- Gate 1: Liquidity (volume/bid-ask spread)
- Gate 2: Volatility regime (DVOL percentile)
- Gate 3: Directional bias (delta/gamma)
- Gate 4: Strike-expiry optimization (P&L metrics)
"""
