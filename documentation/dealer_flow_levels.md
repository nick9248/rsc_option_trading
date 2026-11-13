# Flow-based dealer levels (one expiry)

GUI tab "Dealer Levels" (module 10) and `scripts/dealer_flow_levels.py`. Code: `coding/service/option_flow/dealer_flow_levels_service.py`
(computation), `coding/core/analytics/option_flow/dealer_levels.py` (result types), `dealer_levels_figure.py` (chart),
`coding/gui/tabs/dealer_flow_levels_tab.py` (tab).

## Method

1. **Dealer position.** Every trade has a taker (crossed the book) and a maker. We assume the maker is a dealer. The dealer
   position of an option is minus the cumulative taker flow since the option was listed (`dealer_net = -taker_net`, Glassnode's
   taker-flow method, reusing `DealerInventoryCalculator`). Deribit gives no customer-or-dealer information, so this is an
   assumption, not an observation.
2. **Gamma exposure per strike.** `GEX = (dealer_calls x gamma + dealer_puts x gamma) x spot^2 x 1 %` in USD per 1 % move of the
   spot. Gamma is Black-Scholes from the mark IV of the newest stored chain snapshot (the same function the MenthorQ replication
   uses, index price as underlying). Positive GEX = dealers long gamma (they trade against moves); negative = short gamma (they
   chase moves).
3. **Levels.** Call wall = strike with the largest positive GEX, put wall = largest negative, gamma flip = the strike where the
   running sum over strikes changes sign (`DealerInventoryCalculator._detect_key_levels`).
4. **Comparison view.** The standard OI-based GEX of the same expiry (`compute_exposure`: dealers long calls, short puts, whole
   open interest) is drawn beside it with its own levels.

T0 (the start of the flow) = the first stored trade of the expiry, but not before 2026-04-25 (start of the complete trade history).

## Plausibility check (the project's binding gate, decision D9)

A dealer cannot hold more than the open interest of an option. The view passes only if trade-history coverage since T0 is at
least 95 %, at most 5 % of the checked options exceed their open interest, and not too many traded options lack open interest
(constants imported from `on_chain_analysis_service.py`). The result states the failing reason. The check counts options, not
exposure: a tiny far strike with 7 contracts of flow against 0 open interest fails as much as a large one.

## Limits

- No ground truth for either view; they can disagree strongly (the sample below does).
- Flow is read up to now, the chain snapshot is the newest stored one (shown, with a warning when older than 6 hours).
- Only one expiry at a time; the on-chain analysis already contains the same inferred view per expiry ("Inferred" sign
  convention, gated by D9). This tab is the stand-alone, chart-first version with the OI-based view beside it.

## Sample (2026-11-13, BTC 27NOV26, spot 68,338, chain 21:17 UTC, 48,382 trades since 2026-08-27)

| | call wall | put wall | gamma flip | total GEX per 1 % |
|---|---|---|---|---|
| flow-based | 73,000 | 68,000 | 68,000 | -$3.4M |
| OI-based | 77,000 | 68,000 | not computed | -$69.2M |

Plausibility check not passed: 9 of 92 options exceed their open interest (9.8 %), coverage 100 %.
