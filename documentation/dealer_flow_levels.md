# Flow-based dealer levels (one expiry)

The Dealer levels tab of the Report (Report is module 6, see `documentation/report.md`) and `scripts/dealer_flow_levels.py`. Code: `coding/service/option_flow/dealer_flow_levels_service.py`
(computation), `coding/core/analytics/option_flow/dealer_levels.py` (result types), `dealer_levels_figure.py` (chart),
`coding/service/report/report_sections.py` (`build_dealer_levels_section`, the report section).

## Method

1. **Dealer position.** Every trade has a taker (crossed the book) and a maker. We assume the maker is a dealer. The dealer
   position of an option is minus the cumulative taker flow since the option was listed (`dealer_net = -taker_net`, Glassnode's
   taker-flow method, reusing `DealerInventoryCalculator`). Deribit gives no customer-or-dealer information, so this is an
   assumption, not an observation.
2. **Gamma exposure per strike.** `GEX = (dealer_calls x gamma + dealer_puts x gamma) x spot^2 x 0.01` in USD per 1 % move of the
   spot (`DealerInventoryCalculator.calculate`; the dealer positions are in contracts, not weighted by open interest). Gamma is
   Black-Scholes (r = 0) from the mark IV of the newest stored chain snapshot (the same function the MenthorQ replication
   uses, index price as underlying). Gamma is added for calls and puts; the sign comes only from the dealer position. Positive GEX
   = dealers net long gamma at that strike under the assumption above; the usual hypothesis is that they then trade against moves.
   Negative = net short gamma; the hypothesis is that they then chase moves. This is a hypothesis, not measured.
3. **Levels.** Call wall = strike with the largest positive GEX, put wall = largest negative, gamma flip = the last strike at
   which the running sum of GEX over ascending strikes changes sign between two non-zero values, `None` if it never does
   (`DealerInventoryCalculator._detect_key_levels`). The gamma flip is a point on the strike axis, not a re-priced dealer gamma
   flip (the Zero Gamma Level of `GammaProfileCalculator`); see `gex_dex_methodology.md`.
4. **Comparison view.** The standard OI-based GEX of the same expiry (`compute_exposure`: net GEX = call gamma minus put gamma,
   i.e. dealers assumed long calls and short puts, whole open interest) is drawn beside it with its own levels. Its "gamma flip" is
   `cumulative_sign_change_existing`, the same strike-axis rule as `GexDexKeyLevels.hvl` (not the HVL the MenthorQ replica draws,
   and not the Zero Gamma Level).

T0 (the start of the flow) = the first stored trade of the expiry, but not before 2026-04-25 (start of the complete trade history,
`_DEALER_INVENTORY_COVERAGE_STABLE_DATE`). When the first trade is earlier than that date, the flow starts on 2026-04-25 and a note
says earlier trades are missing from the dealer position.

## Plausibility check (the project's binding gate, decision D9)

A dealer cannot hold more than the open interest of an option. The view passes only if all of these hold (constants imported from
`on_chain_analysis_service.py`):

- Trade-history coverage since T0 is at least 95 %. Coverage is the share of hours since T0 with at least one stored trade of the
  currency (all expiries of that currency together, `get_trade_hour_coverage`), not the activity of this one expiry.
- At most 5 % of the checked options have a cumulative taker flow larger than their open interest (`|taker_net| > open interest`).
- Not more than 20 % of the traded options lack an open-interest figure to check against (and not all of them).
- A valid index price and at least one stored trade for the expiry.

The result states every failing reason. The check counts options, not exposure.

### Why options fail the check

The method assumes that every taker trade opens a position, so the cumulative taker flow equals the dealer's position. Many trades
close positions instead. Then the cumulative taker flow can be larger than what is still open, and the check reports a violation.
The dealer position of those options is wrong by an unknown amount, and so is the gamma computed from it.

### Failures are not limited to small strikes

Do not read a failed check as "only a few tiny strikes". On the data of mid-November 2026 (figures measured by the project owner, not recomputed from this document's code) the strikes that fail carry a
large part of the flow-based gamma on some expiries:

| Expiry | Options failing | Share of the flow-based gamma on the failing strikes |
|---|---|---|
| 27NOV26 | 10 of 92 | 48.6 % |
| 25DEC26 | 10 of 114 | 31.5 % |
| 20NOV26 | 5 of 59 | 7.3 % |

Weighting the check by size would therefore not make these views pass. For an expiry where the failing strikes carry a large
share of the gamma, treat the flow-based view as unreliable (the figures above are a snapshot of one day and change with the data).

## Limits

- No ground truth for either view; they can disagree strongly (the sample below does).
- Flow is read up to the time of the computation. The chain snapshot is the newest stored one. The comparison caption under the
  chart shows the chain snapshot time (`dealer_levels_caption`: "chain snapshot <UTC time>") and the section header shows it as
  "Chain snapshot <time>; trades up to the report time". There is no age warning: an old snapshot is not flagged, so read the
  time.
- Only one expiry at a time; the on-chain analysis already contains the same inferred view per expiry ("Inferred" sign
  convention, gated by D9). This tab is the stand-alone, chart-first version with the OI-based view beside it.

## Sample (2026-11-13, BTC 27NOV26, spot 68,338, chain 21:17 UTC, 48,382 trades since 2026-08-27)

| | call wall | put wall | gamma flip | total GEX per 1 % |
|---|---|---|---|---|
| flow-based | 73,000 | 68,000 | 68,000 | -$3.4M |
| OI-based | 77,000 | 68,000 | not computed | -$69.2M |

Plausibility check not passed: 9 of 92 options exceed their open interest (9.8 %), coverage 100 %. (Sample as written on
2026-11-13; a later measurement of the same expiry shows 10 of 92, see the table above.)
