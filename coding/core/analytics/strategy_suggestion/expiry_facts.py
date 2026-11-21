"""
The facts of one expiry as text, built only from the analysis result (no network, no model): what the model is shown.

The same row functions as the dashboard are used, so every number the model sees is a number on the dashboard. The chain table lists
the quotes in USD (coin price x index) so the model can pick strikes that exist and see what a leg costs; the same quotes price the
suggested legs afterwards in code (a buy at the ask, a sell at the bid).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.onchain_dashboard.expiry_section import (
    delta_flow_window_text, evidence_text, key_level_rows, statistic_rows)
from coding.core.analytics.onchain_dashboard.formatting import number_text
from coding.core.analytics.onchain_dashboard.market_section import volatility_rows
from coding.core.analytics.onchain_dashboard.overview_section import market_metric_rows
from coding.core.analytics.results.analysis_result import ExpirationBundle, OnChainAnalysisResult

CHAIN_ROWS = 30
"""Strikes shown: the ones closest to spot that have open interest or a quote."""
CHAIN_RANGE_FRACTION = 0.25
"""Only strikes within this fraction of spot are shown."""


@dataclass(frozen=True)
class LegQuote:
    """Quote of one option in USD per coin; None when that side is missing (never 0)."""

    bid_usd: Optional[float]
    ask_usd: Optional[float]
    mark_usd: Optional[float]
    implied_volatility_pct: Optional[float]
    open_interest: float


@dataclass(frozen=True)
class ExpiryFacts:
    """The text shown to the model and the quotes that price its answer."""

    currency: str
    expiration: str
    spot: float
    text: str
    quotes: Dict[Tuple[float, str], LegQuote]
    """(strike, "call" or "put") -> quote, for the strikes shown in the text only: nothing else may be used or priced."""


def _usd(price_in_coins: Optional[float], index_price: float) -> Optional[float]:
    """Coin price x index; Deribit quotes 0 / None for no bid or no ask, which is a missing side."""
    return price_in_coins * index_price if price_in_coins else None


def _mark_usd(price_in_coins: Optional[float], index_price: float) -> Optional[float]:
    return None if price_in_coins is None else price_in_coins * index_price


def _quotes(bundle: ExpirationBundle, index_price: float) -> Dict[Tuple[float, str], LegQuote]:
    quotes: Dict[Tuple[float, str], LegQuote] = {}
    for instrument in bundle.enriched_instruments:
        kind = {"C": "call", "P": "put"}.get(instrument.get("option_type"))
        if kind is None:
            continue                                   # an unknown type is not guessed to be a put
        quotes[(float(instrument["strike"]), kind)] = LegQuote(
            bid_usd=_usd(instrument.get("bid_price"), index_price), ask_usd=_usd(instrument.get("ask_price"), index_price),
            mark_usd=_mark_usd(instrument.get("mark_price"), index_price), implied_volatility_pct=instrument.get("mark_iv"),
            open_interest=float(instrument.get("open_interest") or 0))
    return quotes


def _money(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.0f}"


def _shown_strikes(quotes: Dict[Tuple[float, str], LegQuote], spot: float) -> List[float]:
    """The strikes of the chain table: the closest to spot within the range that have open interest or an ask, in strike order."""
    candidates = {strike for strike, _ in quotes
                  if abs(strike - spot) <= CHAIN_RANGE_FRACTION * spot
                  and any(quotes.get((strike, kind)) and (quotes[(strike, kind)].open_interest > 0 or quotes[(strike, kind)].ask_usd)
                          for kind in ("call", "put"))}
    return sorted(sorted(candidates, key=lambda strike: abs(strike - spot))[:CHAIN_ROWS])


def _chain_table(quotes: Dict[Tuple[float, str], LegQuote], strikes: List[float]) -> List[str]:
    lines = ["strike | call bid / ask / IV% / OI | put bid / ask / IV% / OI   (USD per coin, OI in coins; - = no quote)"]
    for strike in strikes:
        cells = []
        for kind in ("call", "put"):
            quote = quotes.get((strike, kind))
            if quote is None:
                cells.append("not listed")
                continue
            iv = "-" if quote.implied_volatility_pct is None else f"{quote.implied_volatility_pct:.1f}"
            cells.append(f"{_money(quote.bid_usd)} / {_money(quote.ask_usd)} / {iv} / {quote.open_interest:,.0f}")
        lines.append(f"{strike:,.0f} | {cells[0]} | {cells[1]}")
    return lines


def _rows(title: str, rows: Sequence[Sequence[str]]) -> List[str]:
    """A titled block of rows; a label that appears twice (the market and volatility rows share some) is written once."""
    seen = set()
    unique = [row for row in rows if not (row[0] in seen or seen.add(row[0]))]
    return [f"## {title}"] + [" | ".join(cell for cell in row if cell != "") for row in unique] + [""]


def build_expiry_facts(result: OnChainAnalysisResult, expiration: str) -> ExpiryFacts:
    """
    Facts of one expiry of one analysis result.

    Raises:
        LookupError: when the result has no such expiry, or it has no Greeks-enriched instruments (nothing to quote).
    """
    bundle = result.bundle(expiration)
    if bundle is None:
        raise LookupError(f"no expiry {expiration} in the analysis result")
    if not bundle.enriched_instruments:
        raise LookupError(f"expiry {expiration} has no instruments with quotes")
    market = result.market_wide
    spot = result.underlying_price
    flow_bucket = next((bucket for bucket in result.delta_flow_buckets if bucket.expiration == expiration), None)

    lines: List[str] = [f"# {result.currency} options, expiry {expiration}, analysis of {result.generated_at:%Y-%m-%d %H:%M} UTC", ""]
    lines += _rows("Market", market_metric_rows(result) + (volatility_rows(market) if market is not None else []))
    lines += _rows("Key levels of this expiry", key_level_rows(bundle, spot))
    lines += _rows("Statistics of this expiry",
                   statistic_rows(bundle, flow_bucket, delta_flow_window_text(result)) + [["Evidence behind the flow", evidence_text(bundle)]])
    if market is not None and market.skew_term_structure is not None:
        skew = [[entry.expiration, number_text(entry.atm_iv_interp, 1, "%"), number_text(entry.rr_25d, 2), number_text(entry.bf_25d, 2)]
                for entry in market.skew_term_structure.entries if entry.expiration == expiration]
        lines += _rows("Skew of this expiry (expiry, ATM IV, 25d risk reversal, 25d butterfly)", skew or [["not computed"]])
    all_quotes = _quotes(bundle, spot)
    strikes = _shown_strikes(all_quotes, spot)
    quotes = {key: quote for key, quote in all_quotes.items() if key[0] in strikes}        # only what the model was shown may be used
    lines += ["## Chain (the only strikes you may use are the ones listed)"] + _chain_table(quotes, strikes)
    return ExpiryFacts(result.currency, expiration, spot, "\n".join(lines), quotes)
