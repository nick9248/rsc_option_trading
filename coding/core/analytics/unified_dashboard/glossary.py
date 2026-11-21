"""
The tooltip texts of the unified dashboard: for each Overview tile, gauge, card and chip, what it is, how to read it and an
example. The wording follows the project's own definitions (the calculators and documentation/gex_dex_methodology.md); the
examples use made-up round numbers and say so.
"""
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Tooltip:
    """One tooltip: three short plain-language parts."""

    what: str
    how_to_read: str
    example: str

    def text(self) -> str:
        """The three parts on separate lines, as shown in the page."""
        return f"What it is: {self.what}\nHow to read it: {self.how_to_read}\nExample: {self.example}"


TOOLTIPS: Dict[str, Tooltip] = {
    # ---- tiles
    "dvol": Tooltip(
        "Deribit's 30-day implied volatility index: the yearly volatility the options market is pricing, in percent.",
        "Higher means options are expensive and a bigger move is priced in. The 'p' number is where today sits among the last 30 days "
        "(p90 = higher than 90% of them).",
        "DVOL 55 means about 55% a year, which is roughly 55 x sqrt(30/365) = 16% either way over 30 days (an illustration, not a forecast)."),
    "iv_rank": Tooltip(
        "Where DVOL sits between its lowest and highest value of the last 365 days: (DVOL - low) / (high - low), from 0 to 100.",
        "Near 0 = volatility is cheap for this year, near 100 = it is at its yearly high. One spike can stretch the range, so also "
        "look at the IV percentile (the share of days with lower IV); the two can disagree.",
        "Rank 34 means DVOL is a third of the way up the year's range."),
    "net_gex": Tooltip(
        "Net gamma exposure of all expiries in USD per 1% move of the price. It assumes dealers are long calls and short puts; Deribit "
        "does not show who holds what, so this is an assumption.",
        "Negative = dealers are assumed short gamma: their hedging buys rises and sells falls, which amplifies moves. Positive = "
        "the opposite, hedging dampens moves. Check the data-health card: missing greeks make this number unreliable.",
        "-1.5M USD means a 1% rise would make dealers buy about 1.5M USD more of the underlying to stay hedged (if the assumption holds)."),
    "taker_ratio": Tooltip(
        "Taker buy volume divided by taker sell volume on Binance perpetual futures for one UTC day. A taker is the side that crosses the spread.",
        "Above 1 buyers were more aggressive, below 1 sellers were. It shows who pushed the price, not how large the positions are, "
        "and says nothing about whether trades opened or closed positions.",
        "0.915 means sellers were about 9% more aggressive than buyers that day."),
    "perp_oi": Tooltip(
        "Open interest of the Binance perpetual future in USD: the total value of contracts still open.",
        "Rising open interest means more leverage in the market, falling means positions are being closed. It does not tell the "
        "direction of the positions by itself.",
        "Open interest rising from 8B to 9B USD while the price rises means new positions are being added, not only old ones moving."),
    "basis": Tooltip(
        "The median yearly premium of the dated futures over the spot index, in percent.",
        "A high premium means traders pay up to hold long futures (strong leveraged demand, and a carry trade opportunity). Near zero or "
        "negative means weak demand or fear.",
        "8% means a futures contract trades 8% a year above the index price."),
    "vrp": Tooltip(
        "Variance risk premium: implied volatility minus realized volatility, in volatility points (the project's definition: IV - RV).",
        "Positive = options are expensive compared with the volatility that actually happened, negative = cheap. Realized volatility is "
        "the past, so it is not a forecast of what comes next.",
        "+6 means options price 6 volatility points more than the market has recently delivered."),
    "funding": Tooltip(
        "The rate longs and shorts of the perpetual future pay each other every 8 hours.",
        "Positive = longs pay shorts (crowded long side), negative = shorts pay longs. Extreme values often come with crowded positioning.",
        "0.01% per 8 hours is about 11% a year (0.01% x 3 x 365)."),
    # ---- gauges
    "dvol_percentile": Tooltip(
        "Where today's DVOL stands among the last 30 days, as a share (percentile).",
        "A full arc means today is higher than almost every day of the last 30; an empty arc means it is lower than almost every day.",
        "p87 means DVOL is higher than 87% of the last 30 days."),
    "iv_percentile": Tooltip(
        "The share of days in the last 365 days on which implied volatility was lower than it is now.",
        "High = implied volatility is expensive compared with the past year. It counts days, so it ignores how far apart the "
        "extremes are (that is what IV rank measures).",
        "86% means implied volatility was lower on 86% of the days of the last year."),
    # ---- chips
    "chip_chain": Tooltip(
        "The time of the option chain snapshot that the levels and exposures were built from (UTC).",
        "Older than a couple of hours means the levels may no longer fit the price; see the data-health card for every source's age.",
        "Chain 17:40 UTC: all option numbers describe the market at 17:40."),
    "chip_gamma": Tooltip(
        "Whether net dealer gamma over all expiries is short or long, under the assumption that dealers are long calls and short puts.",
        "Short gamma = hedging tends to amplify moves; long gamma = it tends to dampen them. It is an assumption, not an observation.",
        "Short gamma with the price near a big put strike can mean faster moves than usual."),
    "chip_upside": Tooltip(
        "The decision engine found no meaningful level above the price within 15% of it (its map range).",
        "Treat the upside as unmapped, not as open sky: it only means the data shows no level there, so a move up has no mapped stopping point.",
        "Price 58,000 and no level up to 66,700 (15% above): an upward move has nothing mapped to stall at."),
    # ---- cards
    "card_price_map": Tooltip(
        "Price levels within 20% of the price where open interest, dealer hedging or flow concentrate: call resistance, put support, max pain, zero gamma and others.",
        "Levels are where positioning concentrates, not predictions. The more sources agree on one price, the more notable it is. This list reaches 20% from the price; the decision engine's own map reaches only 15%.",
        "Put support 53,000 and call resistance 63,000 frame the range where dealer hedging is largest."),
    "card_gamma": Tooltip(
        "Net gamma exposure of each expiry in USD per 1% move (assumed dealer view): bars left of the middle are short gamma, right are long.",
        "It shows how much of the gamma disappears as each expiry passes: when a big short-gamma expiry settles, its amplifying effect ends.",
        "A large left bar on the nearest expiry means the strongest amplifying effect ends when it settles."),
    "card_volatility": Tooltip(
        "Implied volatility of each expiry (the term structure) next to realized volatility and where it sits in its own history (vol cone).",
        "Near expiries above far ones (backwardation) means stress: the market expects the movement soon. Implied above realized means options are rich.",
        "Implied 59% vs realized 40% over 10 days: options price more movement than has happened."),
    "card_evidence": Tooltip(
        "The summary of the evidence report: the reference range around the price (nearest mapped support and resistance), which side is unmapped, and how many evidence items back it.",
        "Levels and evidence, not a forecast and not a score: there is no ranking of scenarios and no confidence label, because the old scores were never calibrated. Open the tab for each item's strength, quality and caveats.",
        "Reference range 53,000 to 63,000 with the upside unmapped means support and resistance were found on the downside only."),
    "card_skew": Tooltip(
        "Skew per expiry: how much more or less expensive puts are than calls (25-delta risk reversal = call IV minus put IV, in volatility points).",
        "Negative = puts cost more (protection is in demand), positive = calls cost more. The more negative, the stronger the fear of a fall.",
        "A risk reversal of -9 means 25-delta puts trade 9 volatility points above 25-delta calls."),
    "card_open_interest": Tooltip(
        "How the open option contracts split across expiries, as a share of notional value.",
        "A big share in the nearest expiries means much of the positioning is about to settle, so the picture changes quickly after those expiries.",
        "60% in the next two expiries: large positions will be settled within days."),
    "card_blocks": Tooltip(
        "The largest block trades of the last 24 hours: negotiated trades that often combine several legs.",
        "Direction is what the taker did, not why. A single leg of a spread can look like a directional bet. Several legs of one structure are one idea.",
        "Buy 500 calls and sell 500 higher calls at once is a call spread, not 500 bullish bets."),
    "card_health": Tooltip(
        "How old each data source is at the moment this page was built.",
        "Old data makes levels stale. Check this card before relying on a number.",
        "Spot 2 minutes old, chain 3 hours old: the option levels may lag the price."),
    "card_expiries": Tooltip(
        "One row per listed expiry with its levels, open interest and gamma regime. Max pain is the strike where option buyers lose the most at settlement.",
        "Click an expiry to open its charts. Max pain is an old market hypothesis that the price drifts toward that strike near expiry; treat it as a reference, not a rule.",
        "Max pain 60,000 for next Friday: if the price settles there, the sum of option payouts is smallest."),
}


def tooltip_text(key: str) -> str:
    """
    The tooltip text for a key.

    Raises:
        KeyError: for an unknown key (a typo in the page code must not silently produce a tile without a tooltip).
    """
    return TOOLTIPS[key].text()
