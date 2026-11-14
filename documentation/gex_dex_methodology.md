# GEX/DEX Calculation Methodology

## Overview

This document describes how Gamma Exposure (GEX) and Delta Exposure (DEX) are calculated from options market data, as implemented in `coding/core/analytics/gex_dex_calculator.py` (the code is the source of truth). The GEX formula is the commonly published one (gamma x spot^2 x 0.01). The sign of the result rests on an assumption about who holds the options (see "Sign conventions"). That assumption is an inference, not a measurement.

Which component computes which level:

| Component | What it computes |
|---|---|
| `GexDexCalculator` (`gex_dex_calculator.py`) | Per-strike GEX/DEX, Call Resistance, Put Support, and `hvl` / `gamma_flip` (the last sign change of cumulative net GEX along the strike axis). This is what is stored in the database and used by the synthesis engine. |
| `GammaProfileCalculator` (`gamma_profile_calculator.py`) | The Zero Gamma Level (`zero_gamma_level`): net (call minus put) gamma x OI re-priced at a grid of hypothetical spot prices (0.5x to 1.5x spot, sticky-strike Black-Scholes), and where it changes sign (`zero_gamma_level` is the crossing nearest to spot). |
| MenthorQ replica (`menthorq_replication_calculator.py`) | Calls `GexDexCalculator` for the per-strike numbers, then computes several candidate rules per level. The level that is drawn is picked by `DEFAULT_LEVEL_CANDIDATES` (see "HVL" below). |

## Formulas

### Net GEX (Gamma Exposure)

```
Net GEX = (Call Gamma - Put Gamma) × Spot Price² × 0.01
```

**Components:**
- **Call Gamma**: Gamma weighted by open interest for all call options at a strike
  - `Call Gamma = Σ(gamma_i × OI_i)` for all calls at the strike (all expiries passed to the calculator)
- **Put Gamma**: Gamma weighted by open interest for all put options at a strike
  - `Put Gamma = Σ(gamma_i × OI_i)` for all puts at the strike
- **Gamma**: Black-Scholes gamma (r = q = 0) from the mark IV, evaluated at the index price (per USD of underlying move, per 1 coin of contract). In the live on-chain analysis it is computed in `OnChainAnalysisService._compute_bs_gamma`, not read from the Deribit ticker. The MenthorQ replica can also evaluate gamma at each expiry's forward (`gamma_underlying="forward"`), but its default and the `GexDexCalculator` anchor is the index price.
- **OI**: Open interest in coins (contract multiplier 1 coin).
- **Spot Price**: The index price (`analyzer.index_price`, from `get_index_price`) in the live analysis; the replica uses the stored index price. It is one number for all expiries. If the index price cannot be fetched, the live analysis falls back to the median underlying price of the nearest expiry's instruments (`OnChainAnalysisService`, `nearest_expiry_median_underlying_price`), and stops with an error when that is missing too.
- **Spot²**: Converts the coin-denominated gamma exposure into USD.
- **0.01**: Scales exposure to 1% underlying price move

**Why Spot²?**

Net gamma × OI is the change in delta (in coins) per 1 USD move of the underlying. A 1% move is `S × 0.01` USD, so delta changes by `Γ × OI × S × 0.01` coins. Multiplied by the price `S` to express that in USD, this gives `Γ × OI × S² × 0.01`: the USD value of the coins the delta changes by, per 1% move. That is the quantity a delta hedger would have to trade, under the assumption of who holds the options.

For example:
- At BTC = $70,000, a 1% move = $700
- A position of 1 coin with gamma = 0.001 changes delta by 0.001 × $700 = 0.7 coins
- In USD: 0.7 × $70,000 = $49,000 = 0.001 × ($70,000)² × 0.01

**Mathematical Derivation:**

```
GEX = USD value of the delta change for a 1% price move
    = Γ × ΔS × S
    = Γ × (S × 0.01) × S
    = Γ × S² × 0.01
```

Where:
- Γ (gamma) = rate of change of delta
- S = spot price
- ΔS = price change for 1% move = S × 0.01

### Net DEX (Delta Exposure)

```
Net DEX = Call Delta + Put Delta
```

**Components:**
- **Call Delta**: Delta weighted by open interest for all calls at a strike
  - `Call Delta = Σ(delta_i × OI_i)` for all calls (delta > 0)
- **Put Delta**: Delta weighted by open interest for all puts at a strike (negative)
  - `Put Delta = Σ(delta_i × OI_i)` for all puts (delta < 0 for puts)

Delta comes from the Deribit ticker `greeks` in the live analysis (gamma is the exception, see above). `net_dex` is in coins (not USD) and is not multiplied by spot. It is the sum of delta × open interest over all calls and puts, i.e. what all holders of the open contracts hold together, with no assumption about who the holders are. It makes no statement about whether the market is bullish or bearish: open interest has a buyer and a seller for every contract, so the sign only says that call delta outweighs put delta (positive) or the reverse (negative), weighted by open interest. The dealer-side figure is the negative of it (`dealer_delta_exposure = -net_dex`), under the assumption that dealers are short whatever holders hold.

The MenthorQ replica has its own, separate DEX (`compute_net_delta_exposure`): `spot × Σ(delta × OI)` in USD, with delta evaluated at each expiry's forward and a leg without delta or open interest left out (counted in `instruments_missing_delta`). Do not compare it with `GexDexCalculator.net_dex` without converting units.

## Key Levels

Every level below is a strike picked from the per-strike `net_gex` (see "Sign conventions": `net_gex` = call gamma minus put gamma, i.e. the dealer gamma **if** dealers are long calls and short puts). The levels are positions on the strike axis. They are not measured support or resistance.

### Call Resistance

The strike price with the **maximum positive Net GEX** (`_detect_key_levels`; `None` if no strike is positive).

- A positive Net GEX means call gamma outweighs put gamma at that strike. Under the assumed convention, dealers are net long gamma there.
- Standard hypothesis (not verified by this project): a dealer who is long gamma hedges by selling into rallies and buying dips, which dampens moves around that strike, so the strike may act as resistance or a magnet.

### Put Support

The strike price with the **most negative Net GEX** (by absolute value; `None` if no strike is negative).

- A negative Net GEX means put gamma outweighs call gamma at that strike. Under the assumed convention, dealers are net short gamma there.
- Standard hypothesis (not verified by this project): a dealer who is short gamma hedges by buying into rallies and selling into dips, which amplifies moves around that strike. The name "support" comes from the level's label, not from this hypothesis: short gamma does not by itself imply that price is held up at that strike.

### HVL (cumulative GEX sign-change strike)

Three different rules exist in the code. They are not the same number, so say which one is meant.

1. **`GexDexKeyLevels.hvl`** (also `gamma_flip` and `cumulative_gex_zero_strike`; identical values; stored as `hvl_level` in the database; the report labels it "Cumulative GEX Zero Strike"). It is the **last strike at which cumulative net GEX changes sign** (the running sum of `net_gex` over ascending strikes goes from positive to negative or the reverse), or a strike where the cumulative sum is exactly zero. If it never changes sign, `hvl` is `None` ("not computed"); there is no fallback to the nearest-to-zero strike any more. This is the rule used by `GexDexCalculator`, by the market-wide aggregate, and by the synthesis engine (`hvl_strike`, `aggregate_hvl`).
2. **The HVL the MenthorQ replica draws.** `DEFAULT_LEVEL_CANDIDATES["hvl"] = "cumulative_net_gex_argmin"` (`strike_of_cumulative_minimum`): the strike where cumulative net GEX (summed from the lowest strike up) is **lowest**, i.e. the turning point of the cumulative curve, **not** its zero crossing. `LEVEL_RULE_DESCRIPTION` says the same. This rule was chosen because it matched MenthorQ's charts best on the dates compared (see the comment above `DEFAULT_LEVEL_CANDIDATES`); MenthorQ itself only publishes "HVL = inflection of the GEX profile".
3. **Other candidates** that `compute_exposure` returns next to it (in `levels["hvl"]`) but does not draw: `spot_sweep_gex_argmin` (the spot at which the re-priced net GEX profile is lowest), `spot_sweep_gex_argmin_nearest_strike`, `spot_sweep_zero_crossing_nearest_spot` (the Zero Gamma Level), and `cumulative_sign_change_existing` (rule 1).

> **Rules 1 and 2 are strike-axis points, not the Zero Gamma Level.** They depend only on how open interest is spread along the strike axis; they are not a re-priced dealer gamma flip (`GexDexCalculator` docstring). The actual Zero Gamma Level (`GexDexKeyLevels.zero_gamma_level`, shown in the report's "GAMMA PROFILE" block) re-prices net (call minus put) gamma x OI across a grid of spot prices (sticky-strike Black-Scholes, `GammaProfileCalculator`) and finds where it changes sign; with several crossings it is the one nearest to spot. The two can sit on opposite sides of spot. The market-wide aggregate has no `zero_gamma_level` (always `None`), because merged strike rows have lost each option's own IV and expiry.

The HVL says nothing by itself about where dealers are long or short gamma. The earlier statements "above HVL dealers are short gamma, below HVL long gamma" are not supported by the code: cumulative GEX is a running sum over strikes, and its sign below or above a strike is not the gamma regime at spot. The regime at spot comes from the sign of the re-priced profile at spot (`net_gex_at_spot`, `gamma_regime`).

**Calculation of rule 1** (`_detect_key_levels`):
1. Compute cumulative GEX as running sum across strikes (sorted ascending).
2. Walk the strikes; whenever the sign of the cumulative sum differs from the previous strike's (or it is exactly zero), remember that strike. The last one found is `hvl`.
3. If none is found, `hvl = None`.

### Gamma flip

In `GexDexCalculator` the name `gamma_flip` is the same number as `hvl` (rule 1), kept for historical reasons. It is **not** the re-priced dealer gamma flip; that is `zero_gamma_level`. In `DealerInventoryCalculator` (flow-based view, see `dealer_flow_levels.md`) "gamma flip" is the same kind of strike-axis rule applied to the flow-based GEX: the last strike at which the running sum over ascending strikes changes sign between two non-zero values (the exact-zero case is not handled; `DealerInventoryCalculator._detect_key_levels`).

## Published sources

The GEX formula (gamma × spot² × 0.01, with a call-minus-put sign split) is widely published, for example by SqueezeMetrics and SpotGamma. The project's own code docstring cites that convention. Details that the sources do not fix (which expiries, which spot, which HVL rule) are choices made in this code. MenthorQ publishes only the definitions of its levels, not formulas, units or the expiry set (docstring of `menthorq_replication_calculator.py`); the list below is what the earlier version of this document cited, and the quotations were not re-verified against the pages:

1. MenthorQ - options analytics platform (https://www.mentorq.com/guides, the URL as previously written; the spelling of the product in the code is "MenthorQ")
2. QuantData - derivatives data provider (https://help.quantdata.us/)
3. Perfiliev, "How to Calculate Gamma Exposure and Zero Gamma Level" (https://perfiliev.co.uk/market-commentary/)

## Implementation Details

### Data Aggregation

1. **Fetch Data:**
   - Book summary: OI, volume, mark_price, underlying_price
   - Ticker endpoint per instrument: delta, theta, vega, mark_iv. Gamma is computed (Black-Scholes at the index price from mark_iv), not read from the ticker. An instrument whose ticker call fails is dropped and counted.
   - An instrument that arrives with a missing gamma or delta keeps its open interest, contributes 0 to the sums, and is counted in `instruments_missing_gamma` / `oi_missing_gamma` on the result, so a gap is disclosed rather than read as zero exposure.

2. **Aggregate by Strike:**
   ```python
   for each option:
       if option_type == "C":
           call_gamma[strike] += gamma × OI
           call_delta[strike] += delta × OI
       elif option_type == "P":
           put_gamma[strike] += gamma × OI
           put_delta[strike] += delta × OI
   ```

3. **Calculate Net Exposures:**
   ```python
   for each strike:
       net_gamma = call_gamma - put_gamma
       net_gex = net_gamma × spot_price² × 0.01
       net_dex = call_delta + put_delta
   ```

4. **Compute Cumulative:**
   ```python
   cumulative_gex = running_sum(net_gex for strikes in sorted order)
   ```

5. **Detect Key Levels:**
   - Call Resistance: `max(net_gex)` (positive values only)
   - Put Support: `min(net_gex)` (most negative; negative values only)
   - HVL / gamma flip: last sign change of `cumulative_gex` (`None` if there is none)

### Code Location

**Core Module:**
- `coding/core/analytics/gex_dex_calculator.py` - Main calculator class

**Service Integration:**
- `coding/service/on_chain/on_chain_analysis_service.py` - Fetches Greeks and calls calculator
- `coding/service/data_collection/prospective_collector.py` - Runs the calculation every cycle and stores the results in `onchain_analysis_snapshots` (`total_net_gex`, `total_net_dex`, `call_resistance_strike`, `put_support_strike`, `hvl_level`)
- `coding/core/analytics/reporting/gex_dex_formatter.py` - Report text

**Tests:**
- `tests/unit/analytics/test_gex_dex_calculator.py`, `tests/unit/analytics/test_gex_dex_completeness.py`

### Sign conventions

- `net_gex` = (call gamma - put gamma) × S² × 0.01. It is the assumed-dealer gamma number: it is the dealer gamma **if** dealers are long calls and short puts (the SqueezeMetrics/SpotGamma heuristic). That is an inference, not a measurement. Positive `net_gex` = dealers long gamma under that assumption; negative = dealers short gamma.
- `gamma_exposure_holder` = (call gamma + put gamma) × S² × 0.01. Pure arithmetic on open interest and Greeks, no assumption about who holds what. It is always >= 0.
- `net_dex` = call delta + put delta. It is the holder-side delta sum (no assumption).
- `dealer_delta_exposure` = `-net_dex` (dealers assumed short whatever holders hold). The long-calls / short-puts split applies to gamma only, not to delta.
- `dealer_gamma_exposure` is an alias of `net_gex`; `delta_exposure_holder` is an alias of `net_dex`.

The class docstring of `GexDexCalculator` is the one place this is defined. The flow-based view in `dealer_flow_levels.md` uses a different assumption (the dealer is the maker of every trade) and a different sign rule (gamma is added for calls and puts, the sign comes from the dealer position).

## Example Calculation

### Input Data (BTC at $70,000)

Strike $72,000:
- Call OI: 1,000, Gamma: 0.00005
- Put OI: 500, Gamma: 0.00003

**Step 1: Weight by OI**
```
call_gamma = 0.00005 × 1,000 = 0.05
put_gamma = 0.00003 × 500 = 0.015
```

**Step 2: Net Gamma**
```
net_gamma = 0.05 - 0.015 = 0.035
```

**Step 3: Calculate GEX**
```
net_gex = 0.035 × (70,000)² × 0.01
        = 0.035 × 4,900,000,000 × 0.01
        = 1,715,000
```

**Result:** Strike $72,000 has Net GEX of +1,715,000

If this is the maximum positive GEX, $72,000 is identified as **Call Resistance**.

## Verification Against MenthorQ (historical, February 2026)

> **Stale.** These numbers were produced by an older version of the code. The HVL of $20,000 below came from the nearest-to-zero fallback that has since been deleted (`_detect_key_levels` now returns `None` when cumulative GEX never changes sign). The replica's drawn levels were re-tuned later against MenthorQ charts of 2026-10-12 and 2026-11-06 (see `DEFAULT_LEVEL_CANDIDATES` in `menthorq_replication_calculator.py`). Kept as a record, not as current behaviour.

### Test Case: BTC 27MAR26 (Feb 16, 2026)

**Data Collected:**
- Spot Price: $68,725
- Total Instruments: 124 options (calls + puts)
- Greeks fetched via Deribit ticker API

**Our Results (Updated Formula):**
- Total Net GEX: -15,860,072.93
- Call Resistance: $95,000
- Put Support: $70,000
- HVL: $20,000

**MentorQ Reference (Feb 27, 2026):**
- Call Resistance: $72,000
- Put Support: $70,000
- HVL: $70,000

**Analysis:**

1. **Put Support Match:** Our $70k exactly matches MentorQ ✓
2. **Call Resistance Discrepancy:** $95k vs $72k
   - Top strikes by GEX are very close (2,124 vs 2,058)
   - May be due to date difference (Feb 16 vs Feb 27)
   - MentorQ might use volume weighting or proximity filtering
3. **HVL Discrepancy:** $20k vs $70k
   - Zero crossing detection depends on cumulative distribution
   - Time difference could affect market positioning

**Key Finding at the time:** The put support matched; the call resistance and HVL did not. Possible causes named then (not verified): different data collection timestamps, a different expiry set or filtering by MenthorQ, market changes between Feb 16 and Feb 27. A single matching level is weak evidence that the formula is right; the later replica comparison (24 of 24 per-expiry panel levels, 3 of 3 all-expiry levels on two dates, with a different expiry set per date) shows that the replica can reproduce MenthorQ, not that the formula is right: the replica matched after its rules and expiry set were chosen on those same dates (in-sample).

## Usage in System

### Generate On-Chain Analysis Report

```python
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from coding.service.deribit.deribit_api_service import DeribitApiService

api = DeribitApiService()
service = OnChainAnalysisService(api)

report = service.fetch_and_analyze(
    currency="BTC",
    expiration="27MAR26",
    progress_callback=lambda msg: print(msg)
)
```

The report includes GEX/DEX analysis with:
- Key levels identified
- Per-strike Net GEX and Net DEX
- Cumulative profiles
- Market interpretation (long/short gamma environment)

### Database Capture

```python
from coding.service.database.capture_strategies import GexDexCaptureStrategy

strategy = GexDexCaptureStrategy(api_service, db_repository)
strategy.capture(currency="BTC", expiration="27MAR26")
```

Stores GEX/DEX data in database for historical analysis and charting.

## Interpretation Guide

### Total Net GEX

The statements below are the standard hypothesis under the assumed convention (dealers long calls, short puts). They are not measured facts, and this project has not tested them.

- **Positive Total GEX:** call gamma outweighs put gamma. If dealers are long calls and short puts, they are net long gamma.
  - Hypothesis: they hedge by selling rallies and buying dips, which dampens price moves and lowers realized volatility.

- **Negative Total GEX:** put gamma outweighs call gamma. If dealers are long calls and short puts, they are net short gamma.
  - Hypothesis: they hedge by buying rallies and selling dips, which amplifies price moves and raises realized volatility.

### Total Net DEX

`total_net_dex` is the sum over all strikes of delta × open interest, in coins, for all holders of the open contracts together.

- **Positive:** call delta outweighs put delta (weighted by open interest).
- **Negative:** put delta outweighs call delta.
- The sign is not a statement about bullish or bearish positioning. Every contract has a long and a short side, and who is on which side is not observed. Under the dealer assumption, the dealers' delta is the negative of this number.

## Testing and Validation

### Unit Tests

Location: `tests/unit/analytics/test_gex_dex_calculator.py` and `tests/unit/analytics/test_gex_dex_completeness.py`.

They cover the formula (Spot² × 0.01), aggregation by strike and OI weighting, call resistance, put support and HVL detection, cumulative GEX, DEX, edge cases (empty data, missing Greeks), the market-wide aggregate (`aggregate_across_expirations`), the holder/dealer sign convention, and the gamma roll-off profile.

```bash
.venv\Scripts\python -m pytest tests/unit/analytics/test_gex_dex_calculator.py -v
```

The one-off script `scripts/investigate_gex_discrepancy.py` that this section used to describe no longer exists.

## Change History

**2026-02-16: Updated to Industry Standard Formula**

- **Changed:** Net GEX formula from `net_gamma × spot` to `net_gamma × spot² × 0.01`
- **Reason:** Industry standard methodology (MentorQ, QuantData, SpotGamma)
- **Impact:** GEX magnitudes scaled by factor of ~687 for BTC at $68k
- **Key Levels:** Unchanged (relative values determine max/min/zero crossing)
- **Validation:** 17 unit tests, real data comparison against MenthorQ (see the stale-results note above)

**Previous Formula (Incorrect):**
```
Net GEX = net_gamma × spot_price
```

This was off by a factor of (spot × 0.01), leading to incorrect magnitude scaling.

## Market-Wide Aggregate GEX/DEX

### Overview

In addition to per-expiry analysis, the system computes a **market-wide aggregate** that sums GEX and DEX across all expirations passed to it (the live on-chain analysis passes every expiry it analysed). This gives a single view across the term structure, under the same assumed-dealer convention as the per-expiry numbers.

### Method: `aggregate_across_expirations()`

```python
GexDexCalculator.aggregate_across_expirations(
    gex_dex_by_expiry: Dict[str, GexDexResult],  # per-expiry calculate() results
    spot_price: float,
    currency: str = "BTC",
) -> GexDexResult
```

**Algorithm:**
1. Skip any key named `"AGGREGATE"` (prevents recursion)
2. Merge `strike_data` from all expirations — summing gamma, delta, and OI at each strike
3. Re-run `_calculate_gex_dex()` (with the `spot_price` passed in), `_calculate_cumulative_profiles()`, and `_detect_key_levels()` on the merged data. The aggregate `hvl` therefore uses rule 1 above. The aggregate has no `zero_gamma_level`.
4. Return a `GexDexResult` with the same structure as `calculate()`, plus `expiration_count`

### Why Equal-Weight Summation (No DTE-Weighting)

Gamma already encodes time-to-expiry via the Black-Scholes formula: for the same strike and IV, a nearer expiry has a higher at-the-money gamma. Applying an additional DTE weight would double-count this effect. The code docstring (`aggregate_across_expirations`) states this as the SqueezeMetrics / SpotGamma / Glassnode practice; the "5x" figure of the earlier version of this document is not computed anywhere in the code and has been removed.

### How It's Used in the Synthesis Engine

The aggregate result is `result.market_wide.aggregate_gex_dex` (a `GexDexResult`) and is used by `SynthesisMapper.build_market_wide()` to populate five fields on `MarketWideMetrics`:

| Field | Source |
|-------|--------|
| `aggregate_total_gex` | `aggregate_gex_dex.total_net_gex` |
| `aggregate_total_dex` | `aggregate_gex_dex.total_net_dex` |
| `aggregate_call_resistance` | `aggregate_gex_dex.key_levels.call_resistance` |
| `aggregate_put_support` | `aggregate_gex_dex.key_levels.put_support` |
| `aggregate_hvl` | `aggregate_gex_dex.key_levels.hvl` |

The synthesis engine picks one GEX value, `gex_for_regime`: `aggregate_total_gex` when it is not `None`, otherwise the GEX of the largest-open-interest expiry (`synthesis.py`, "STEP 3"). It feeds the vol regime classifier (SUPPRESSED vs. EXPLOSIVE), the regime narrative and the GEX risk factor (`gex_total / spot < -50`). The aggregate is preferred because hedging responds to the total book, not to one expiry (a hypothesis, not tested here).

### Limitation: Aggregate Can Mask Localized Risk

A weekly expiry with deeply negative GEX near spot and a monthly expiry with positive GEX farther away may net to a mildly positive aggregate. The vol regime then points to suppression while the near-term expiry could still drive amplified moves. The only separate near-term check in the code is the gamma roll-off "GAMMA CLIFF" flag (more than 30 % of gamma mass within 7 days, `calculate_rolloff_profile`). The per-expiry GEX is not used as a separate stress check for the risk factor: it uses the same `gex_for_regime` as the regime.

### Report Section: `format_aggregate_gex_dex_section()`

Generates a formatted text block for the market-wide report. Unlike per-expiry sections, no per-strike table is shown (too many strikes to be readable). Shows:
- Spot price
- KEY LEVELS: Call Resistance, Put Support, HVL (rule 1)
- TOTALS: Total Net GEX (USD per 1 % move), Total Net DEX (coins)
- GEX/DEX environment interpretation (positive or negative total GEX, with the assumed-dealer caveat above)

---

## Future Enhancements

Potential improvements for closer alignment with institutional analytics:

1. **Volume Weighting:** Weight gamma by recent trading volume in addition to OI
2. **Liquidity Filtering:** Exclude strikes with low volume/OI thresholds
3. **Proximity Weighting:** Consider distance from spot when identifying key levels
4. **Time Decay Adjustment:** Not needed as written: gamma already uses time to expiry (Black-Scholes). Listed only for completeness.
5. **IV Surface Integration:** Gamma already uses each option's own mark IV; a fitted surface would be a different choice.

These would be implemented in a separate `EnhancedGexDexCalculator` class to maintain backward compatibility.

## References

1. MenthorQ. "What is GEX? Understanding Gamma Exposure Mechanics." https://www.mentorq.com/guides (URL as previously written, not re-checked)
2. QuantData Help Center. "What is Gamma Exposure (GEX)?" https://help.quantdata.us/
3. Perfiliev, A. "How to Calculate Gamma Exposure and Zero Gamma Level." https://perfiliev.co.uk/market-commentary/
4. Deribit API Documentation. "Greeks Calculation." https://docs.deribit.com/
5. SpotGamma. "Zero Gamma Level (GEX) Methodology." https://spotgamma.com/

---

**Document Version:** 1.2
**Last Updated:** 2026-11-14 (aligned with the code: HVL rules, sign conventions, DEX meaning, aggregate usage)
**Author:** Claude Code
