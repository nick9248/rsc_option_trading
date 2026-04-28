# On-Chain Options Analysis: Decision-Making Flow Guide

**Purpose**: How to read the on-chain analysis report — where to start, what each metric means, how they filter each other, and what conclusions to draw.

---

## 1. Overview — The Three-Layer Reading Framework

The report is structured in three analytical layers. You must read them in order because each layer constrains the interpretation of the next.

```
Layer 1: Market-Wide Context
    ↓ Sets the vol regime and overall directional bias
Layer 2: Expiry-Level Analysis
    ↓ Shows per-expiry positioning, GEX levels, and flow
Layer 3: Strike-Level Detailalso
    ↓ Confirms or contradicts the expiry-level read
```

**Why this order matters**: A P/C ratio of 1.63 (bearish) on one expiry means something different depending on whether the overall vol regime is EXPLOSIVE vs SUPPRESSED, and whether dealers are long or short gamma. Reading strike-level detail first leads to false precision — you are anchoring on a number without context.

**Time allocation**: In a 10-minute read, spend roughly 3 minutes on Layer 1 (market-wide), 5 minutes on Layer 2 (expiry sweep), and 2 minutes on Layer 3 confirmation. The synthesis output (`synthesis.txt`) gives you the engine's conclusion — use it as a cross-check against your own read, not as a replacement for reading the report.

---

## 2. Step 1 — Market-Wide Context (First 3 Minutes)

**Location in report**: The `MARKET METRICS` section at the top.

This section gives you four pieces of information that define the environment for every trade you consider.

### DVOL (Volatility Index)

DVOL is the BTC 30-day implied volatility index (equivalent to VIX for crypto). It is the market's aggregate estimate of 30-day forward realized volatility.

| DVOL Range | Environment | Implication |
|---|---|---|
| > 80 | Vol spike / stress event | Premium is expensive. Avoid buying options unless you have strong directional conviction. |
| 60–80 | Elevated vol | IV is above average. Favor defined-risk structures. Selling vol has positive expected value but risk is high. |
| 40–60 | Normal range | No vol edge. Trade direction, not vol. |
| 25–40 | Compressed vol | Premium is cheap. Buying vol has structural edge. |
| < 25 | Historically low vol | Buy vol aggressively. Risk events are underpriced. |

**BTC 2026-03-16 example**: DVOL = 52.48. This is the normal range — no strong edge in buying or selling vol on DVOL alone.

### IV Percentile vs IV Rank

These are distinct measures that can diverge significantly.

- **IV Percentile (365d)**: What percentage of the past 365 days had IV *below* today's level. 84.2% means IV has been lower than today on 84.2% of days in the past year.
- **IV Rank (365d)**: Where today's IV sits within the range of the past year, scaled from min to max. 34.1% means today's IV is 34% of the way from the 52-week low to the 52-week high.

**Why they diverge**: IV percentile is non-parametric (distribution-based), IV rank is range-based. If there was a major vol spike 8 months ago that pushed the 52-week high to 150%, today's IV of 52% scores low on rank (far from that high) but still high on percentile (most recent days had lower IV). The percentile is the more reliable measure for assessing whether premium is cheap or expensive right now.

**BTC 2026-03-16 example**: IV Pctile = 84.2%, IV Rank = 34.1%. The divergence confirms there was a major past spike (probably the bear market). Premium is genuinely expensive on a distributional basis — the 84th percentile is a clear signal. The 34th rank is misleading because it is diluted by an extreme historical high.

**Rule**: When pctile and rank diverge by more than 30 points, use the percentile as your primary signal. Rank is distorted by one or two extreme historical events.

### VRP (Volatility Risk Premium)

VRP = DVOL minus 30-day realized volatility. Positive = implied vol is pricing more risk than has actually occurred.

| VRP | Signal |
|---|---|
| > +10 pts | Strong sell vol signal — premium significantly overpriced |
| +5 to +10 pts | Moderate sell vol signal |
| -5 to +5 pts | Neutral — no structural edge in vol direction |
| -5 to -10 pts | Moderate buy vol signal |
| < -10 pts | Strong buy vol signal — market underpricing realized risk |

**BTC 2026-03-16 example**: DVOL = 52.48, 30d RV = 51.0%. VRP = +1.5 pts. This is inside the neutral band. Despite the high IV percentile, the market is not significantly overpricing vol relative to what has been realized — realized vol is already elevated. The synthesis correctly concludes: "Volatility is fairly priced. No strong edge in selling or buying premium."

This is the IV pctile vs VRP tension: percentile says expensive, VRP says fairly priced. VRP is the more actionable signal for premium selling decisions because it accounts for what the market is actually doing (realizing vol), not just the historical distribution.

### Funding Rate

The perpetual futures 8-hour funding rate is a contrarian signal.

| 8h Funding (annualized) | Signal |
|---|---|
| > +20% annualized | Extremely crowded long. Setup is fragile. Forced liquidations can cascade. |
| +10% to +20% | Crowded long. Bearish contrarian lean. |
| -5% to +5% | Neutral leverage. No crowding risk. |
| -10% to -20% | Crowded short. Bullish contrarian lean. |
| < -20% | Extremely crowded short. Squeeze potential is high. |

Note: Funding is contrarian — extreme positive funding is bearish (longs are overcrowded), extreme negative is bullish (shorts overcrowded). The system triggers a fragility flag when directional flow signals are bullish AND funding is positive above +15% annualized. This combination means: direction agrees, but the setup is overloaded.

**BTC 2026-03-16 example**: 8h funding = 0.0002% (annualized ≈ 0.26%). Effectively zero. No crowding in either direction. No fragility. This gives the directional signals full credibility — there is no crowded positioning to unwind.

---

## 3. Step 2 — Market Regime Classification

After reading the market-wide metrics, classify the regime. This determines your overall strategic posture.

The system derives two sub-classifications:

### Vol Regime

| Regime | Conditions | Trading Posture |
|---|---|---|
| EXPLOSIVE | Negative total GEX + IV > 60th pctile + skew fear signal | Directional long options only. No short premium. Dealers will amplify moves. |
| ELEVATED | IV > 60th pctile + (VRP > +5 OR term structure stressed) | Sell vol with defined risk. Prefer spreads over naked. |
| SUPPRESSED | Positive total GEX + IV cheap/normal | Range-bound environment. Sell premium. Dealers are pinning. |
| NORMAL | No strong signal | Neutral. Trade direction. |

### Market Regime (Direction + Vol combined)

| Market Regime | Meaning |
|---|---|
| VOLATILE_BULLISH | Bullish direction + explosive/elevated vol. Outsized upside with violent pullbacks possible. |
| VOLATILE_BEARISH | Bearish direction + explosive vol. Cascading downside risk. |
| TRENDING_UP | Bullish direction + normal vol. Clean uptrend. |
| TRENDING_DOWN | Bearish direction + normal vol. Clean downtrend. |
| RANGE_BOUND_NEUTRAL | No directional lean + vol suppressed or normal. Classic IC environment. |
| RANGE_BOUND_BULLISH | Bullish lean + vol suppressed by positive gamma. Slow grind higher. |
| RANGE_BOUND_BEARISH | Bearish lean + vol suppressed. Slow grind lower. |
| RANGE_BOUND_ELEVATED | No direction + vol expensive. Best premium selling environment. |
| TRANSITION | Near-term and far-term signals conflict. Reduce sizing. |

**BTC 2026-03-16 example**: Regime = VOL BULLISH. This is VOLATILE_BULLISH. Interpretation: the direction signal is bullish, but vol is elevated. The synthesis correctly warns: "negative gamma will amplify moves." This means upside moves can be fast and large, but pullbacks will also be sharp. Long calls or call spreads with defined risk are appropriate. Avoid naked short positions on either side.

---

## 4. Step 3 — GEX/DEX: Understanding Dealer Positioning

**Location in report**: The `GEX/DEX ANALYSIS` section within each expiry, plus the aggregate section.

GEX and DEX are the two most important structural signals in the report. They tell you what dealers are forced to do mechanically as the market moves, regardless of sentiment.

### GEX — Gamma Exposure

GEX is the aggregate gamma position of market makers (dealers). Dealers are always on the opposite side of retail/institutional option buyers.

| GEX Sign | Dealer Position | Market Effect |
|---|---|---|
| Positive (dealers long gamma) | Dealers bought options from clients | Dealers buy dips, sell rallies. Dampening. Market tends toward mean reversion and range-bound behavior. |
| Negative (dealers short gamma) | Dealers sold options to clients | Dealers buy when price falls (to hedge) and sell when price rises, but in a destabilizing way. Amplifying. Breakouts and sustained directional moves become more likely. |

**Magnitude matters**: A GEX of +$5M vs +$500M has very different dampening power. Use the GEX/spot ratio to normalize across time and price levels. At BTC $73K: GEX/spot > 20 is meaningfully positive (pinning), < -20 is meaningfully negative (amplifying).

**Per-expiry vs total GEX — this distinction is critical**:

- **Per-expiry GEX**: The gamma environment for *that specific expiry*. Use this to identify which expiries are structurally volatile vs stable, and to find the key price levels (call resistance, put support, zero gamma line) that are relevant for each window.
- **Total/Aggregate GEX**: The sum across all expirations, representing the dealer's total portfolio gamma. This determines the vol regime classification. Near-term options carry ~5x the gamma of far-term options at the same notional, so aggregate GEX is naturally dominated by the front end.

**Do not confuse them**: A front-month expiry with negative per-expiry GEX can cause amplified moves over the next 24-72 hours even if aggregate GEX is positive (because far-term positive gamma offsets it in aggregate but does not help the front-end dealer). The risk flag in the synthesis uses largest-expiry GEX precisely to catch this — positive aggregate with negative dominant-expiry GEX is a known blind spot.

### Key GEX Levels (Per Expiry)

Each expiry reports three structural price levels:

1. **Call Resistance** (highest positive GEX strike): The level where call-side dealer gamma is concentrated. Price approaching this level will face mechanical selling pressure as dealers hedge their long gamma (sell as price rises). Rallies often stall here.

2. **Put Support** (most negative GEX strike): The level where put-side dealer gamma is concentrated. Price approaching this level from above will face mechanical buying as dealers hedge (buy as price falls). Support often holds near this level in positive-GEX environments.

3. **Zero Gamma Level (HVL — High Volatility Level)**: The strike where cumulative GEX crosses from negative to positive (or vice versa). This is the pivot point. Above HVL: positive gamma environment, dampening. Below HVL: negative gamma environment, amplifying. If spot is near the HVL, small moves can shift the entire market dynamic.

**BTC 2026-03-16 example (17MAR26 expiry, expires today)**:
- Call Resistance: $74,000 (Net GEX: +$2.16M)
- Put Support: $72,000 (Net GEX: -$2.91M)
- Zero Gamma Level: $73,500
- Total Net GEX: -$3.44M (NEGATIVE — dealers short gamma, amplifying)
- Spot: $73,409

This is a telling setup for the expiry day: spot is sitting just $91 below the zero gamma level ($73,500). With negative total GEX, the dealer hedging dynamic is amplifying. Any move above $73,500 puts spot into positive-GEX territory for this expiry; a move below keeps it in negative-GEX. This creates a volatility pinch point at $73,500 — it is not just the nearest resistance, it is where the gamma regime flips.

### DEX — Delta Exposure

DEX is the aggregate market delta across all open options. It represents how much underlying directional exposure option holders have collectively accumulated.

| DEX Sign | Implication | Why |
|---|---|---|
| Positive | Net long delta. Dealers net short delta. Dealers must buy underlying to hedge. Upward pressure. | Clients hold calls (or long puts that expire, reducing delta). Dealers are short delta and buy spot/futures to neutralize. |
| Negative | Net short delta. Dealers net long delta. Dealers must sell underlying to hedge. Downward pressure. | Clients hold puts. Dealers are long delta and sell spot/futures to neutralize. |

DEX is the strongest directional scorer in the system (weight 0.8), above P/C ratio (0.7) and flow (0.6).

**BTC 2026-03-16 example (17MAR26)**: DEX = +125.6 BTC. DEX/spot = 125.6 / 73,409 ≈ 0.00171. This is a moderate bullish signal. Dealers are holding net short delta on this expiry and must buy spot to hedge — providing underlying support.

---

## 5. Step 4 — Max Pain and Put/Call Ratio Per Expiry

### Max Pain

Max pain is the strike at which the total dollar value of outstanding options expires worthless is maximized — in other words, the price at which the market maker's payout to option buyers is minimized. It functions as a gravitational center, particularly as expiry approaches.

**Max pain is not a precise target**. It is a structural constraint that exerts increasing influence inside the final week. Beyond 30 DTE, the OI landscape will shift significantly before expiry, so the gravitational pull is unreliable.

| Max Pain Distance from Spot | Interpretation |
|---|---|
| Within ±1% | Max pain is near the money. High probability it acts as a pin for expiry. |
| 1–5% away | Mild gravitational pull. Can influence late-expiry price action. |
| 5–10% away | Weak pull. More informative as an OI structural signal than as a price target. |
| > 10% away | Essentially no gravitational effect. Use for structural context only. |

**BTC 2026-03-16 examples**:
- **17MAR26** (expires today): Max pain = $73,000, spot = $73,409. Distance = +0.56%. This is an extremely tight max pain. Expiry pinning at $73K is plausible and consistent with the $73,500 zero gamma level just above.
- **20MAR26** (3 DTE): Max pain = $69,000, spot = $73,409. Distance = -6.0%. This is a significant divergence. With 3 DTE remaining, this expiry has OI structured around a much lower price. Either there will be a large move, or this OI is stale hedging that won't be tested. The point: do not read this as "price is going to $69K" — read it as "there is structural put OI concentrated significantly below spot."

### Put/Call Ratio

P/C ratio = Total Put OI / Total Call OI. Below 1.0 = more calls than puts (bullish positioning), above 1.0 = more puts (bearish or hedging).

| P/C Ratio | Signal | Note |
|---|---|---|
| < 0.40 | Strong bullish (call-dominated) | Extreme — contrarian caution. Overcrowded. |
| 0.40–0.60 | Strong bullish | Normal call dominance. |
| 0.60–0.80 | Bullish lean | More calls than puts. |
| 0.80–1.20 | Neutral | Balanced. |
| 1.20–2.00 | Bearish / hedging | Put dominance. Could be directional put buying or portfolio hedging. |
| > 2.00 | Extreme put dominance | Contrarian caution. May signal fear washout / potential bottom. |

**Critical**: P/C ratio does not distinguish between directional put buying (bearish) and protective hedging (risk management). A P/C of 1.63 with "Heavy OTM" skew and high notional in deep OTM strikes is more likely hedging than directional. Context from the flow section is required to distinguish these cases.

**BTC 2026-03-16 cross-expiry comparison**:

| Expiry | P/C | Flow | DTE | Interpretation |
|---|---|---|---|---|
| 17MAR26 (today) | 1.63 | Moderate Selling | 0 | High P/C on expiry day reflects hedging rolls. Not a directional signal. |
| 20MAR26 | 0.75 | Heavy Buying | 3 | Bullish positioning for the 20MAR expiry. |
| 27MAR26 | 0.66 | Heavy Buying | 10 | Clearly bullish OI structure mid-term. |
| 3APR26 | 0.45 | Heavy Selling | 17 | Extreme call dominance, but Heavy Selling flow. Contradictory — read carefully. |
| 24APR26 | 0.59 | Balanced | 38 | Bullish OI, mixed flow. |

The 17MAR bearish P/C (1.63) versus 27MAR bullish P/C (0.66) is not a contradiction — they are different time horizons. The 17MAR puts are likely near-expiry hedges being rolled or expired. The 27MAR OI reflects the genuine medium-term positioning.

**P/C in the context of term structure**: When near-term P/C is high and far-term P/C is low, it typically indicates hedging demand for near-term events (earnings, macro catalysts) while the medium/long-term view remains bullish. This is a healthy, non-threatening pattern. It becomes concerning only when P/C rises across all terms simultaneously.

---

## 6. Step 5 — Volatility Surface Analysis

**Location in report**: The `VOLATILITY SURFACE ANALYSIS` section within each expiry.

### 25-Delta Skew

Skew = 25-delta put IV minus 25-delta call IV. A positive skew means OTM puts are more expensive than OTM calls. This is normal in equity and crypto markets — participants consistently pay more to hedge downside.

| 25d Skew | Signal | Interpretation |
|---|---|---|
| > 12% | Extreme fear | Crash protection priced aggressively. Near crash/liquidation risk environment. |
| 8–12% | Heavy hedging | Significant fear. Put sellers have structural edge. |
| 4–8% | Normal | Standard hedging premium. No edge on either side. |
| 0–4% | Complacent | Puts relatively cheap. Tail risk underpriced. Consider buying cheap put protection. |
| < 0% | Inverted skew | Rare. Extreme upside mania or structural market distortion. |

Skew is a **fear indicator**, not a directional predictor. High skew does not mean price will fall — it means the market is paying for downside protection regardless of directional view. Knowing puts are expensive tells you *where the premium is*, not *which way price goes*.

**BTC 2026-03-16**: Front skew = +3.8% (17MAR26: 25d put at 55.9%, 25d call at 52.1%). This is in the "complacent" zone — puts are only marginally more expensive than calls. Far-term skew increases: 24APR26 = +6.4%, 27MAR26 = +5.9%. This is a normal forward skew curve — participants pay slightly more for protection on longer time horizons. No crash fear signal here.

### Term Structure (Contango vs Backwardation)

Term structure describes how IV varies across expiry dates.

| Shape | Description | Implication |
|---|---|---|
| Contango | Far-term IV > near-term IV | Normal market structure. Near-term vol expected to normalize. No near-term stress. |
| Backwardation | Near-term IV > far-term IV | Front-end stress. Market pricing an imminent event or current crisis. |

A steep backwardation (front IV significantly higher than back) is a red flag — something is expected to happen soon, and the market is paying heavily for near-term protection.

**BTC 2026-03-16**: Term structure = CONTANGO (-0.7 pts spread). This is nearly flat contango — the difference between front and back month IV is minimal. The front expiry (17MAR26) shows ATM IV of 52.2%, the longer terms settle around 49–51%. Normal, unstressed term structure. No near-term event premium.

The ATM IV by expiry sweep in the synthesis:
- 17MAR26: 52.2% → 18MAR26: 53.2% → 19MAR26: 56.3% → 20MAR26: 56.9% → 27MAR26: 53.0% → 3APR26: 51.6% → 24APR26: 50.1% → 26JUN26: 49.5%

Note the IV bump at 19MAR/20MAR (56%+) relative to adjacent expiries. This is a front-end kink — IV is higher for the 19–20MAR expiries than for the 17MAR spot expiry and the 27MAR mid-term. This suggests specific event pricing for those dates (perhaps a macro event or significant options gamma expiry).

### VWAP IV vs Mark IV

- **VWAP IV**: Volume-weighted average IV of all trades executed. Reflects where trades actually occurred.
- **Mark IV**: Current theoretical mid-market IV from the exchange.
- **Diff**: VWAP - Mark.

| VWAP vs Mark | Interpretation |
|---|---|
| VWAP > Mark (+) | Buyers were aggressive — bidding above mid. Buying pressure. |
| VWAP < Mark (-) | Sellers were aggressive — hitting bid below mid. Selling pressure. |
| Near zero | Balanced two-sided flow. |

**BTC 2026-03-16 examples**:
- 17MAR26: VWAP 60.8%, Mark 68.2%, Diff -7.4% → Sellers aggressive. Consistent with the "Moderate Selling" flow classification.
- 18MAR26: VWAP 64.3%, Mark 62.0%, Diff +2.4% → Buyers aggressive. Consistent with "Heavy Buying."
- 20MAR26: VWAP 57.1%, Mark 70.8%, Diff -13.7% → Strong seller aggression. Sellers willing to transact well below theoretical mid — notable.

---

## 7. Step 6 — Buy/Sell Flow Analysis

**Location in report**: The `BUY/SELL FLOW ANALYSIS` section within each expiry.

Flow analysis classifies whether trades (over the past 24 hours) were buyer-initiated or seller-initiated at the instrument level, then aggregates to an expiry-level bias.

### Flow Bias Interpretation

| Bias | Implication |
|---|---|
| Heavy Buying | Buyers are aggressively lifting offers. Bullish pressure. |
| Moderate Buying | Buyer-lean but not aggressive. Mild bullish. |
| Mixed/Neutral | Neither side dominant. Noise. |
| Moderate Selling | Sellers hitting bids. Mild bearish pressure. |
| Heavy Selling | Aggressive seller activity. Bearish pressure. |

**Context required for flow**: Heavy Buying in puts is bearish (buying downside protection), while Heavy Buying in calls is bullish. The expiry-level summary combines calls and puts into a single bias — but the strike-level breakdown reveals which side is driving it.

**Reading flow by option type**: Always check the calls/puts breakdown:
```
17MAR26: Calls: Buy 330.9 / Sell 375.8 | Puts: Buy 891.9 / Sell 1,114.1 → Moderate Selling
```
Both calls and puts are net selling (sell volume > buy volume). The put selling dominates in absolute size — this means traders are selling puts more than buying them. Combined with the negative net flow on the top selling strikes (68K put, 71K put), this looks like put sellers, not put buyers — a mildly bullish signal when interpreted correctly (selling puts = accepting downside risk, which is a bullish trade).

### Top Strikes by Flow Pressure

The top buying/selling strikes tell you where the smart money is focused.

**Reading the table correctly**:
- "Top 5 by Buying Pressure" shows strikes with the highest net buying (Buy Vol - Sell Vol). These are strikes where buyers are more aggressive than sellers.
- Check the **type** column (C/P) and the **strike** to understand the directional implication.

**BTC 2026-03-16 example (17MAR26)**:
- Top buying: $70,000 Put (+312.1 net), $70,500 Put, $72,500 Put. Large put buying near spot.
- Top selling: $68,000 Put (-330.4 net), $71,000 Put, $66,000 Put.

Buying near-the-money puts ($70K-$72.5K) while selling deep OTM puts ($66K-$68K) is a classic **put spread roll pattern** — participants are buying near-money protection and funding it by selling deeper puts. This is portfolio hedging, not directional bearish speculation.

### Block Trades (Institutional Flow)

Block trades are large single transactions that reveal institutional intent. They are higher-signal than retail flow because they represent a deliberate institutional decision, not retail order flow aggregation.

**BTC 2026-03-16**: 6 buys ($24.7M) vs 4 sells ($50.2M). Sell-dominated. The largest: BTC-3APR26-85000-C SELL 600.0 BTC ($43.89M) at 47.9% IV. A $43.89M institutional call sale at $85K for April means an institution is either: (a) covered call writing against a long BTC position, (b) directionally selling upside, or (c) shorting vol at 47.9% with a $85K cap. Given the bullish market regime, (a) or (c) are more likely. This is the dominant block trade by size and should not be ignored — it represents significant premium collection that suppresses IV in the 3APR expiry.

---

## 8. Step 7 — Cross-Expiry Synthesis

After reading each expiry section, you need to build a unified picture.

### Near/Mid/Far Term Bias Aggregation

The synthesis output bins expiries into three time windows. This is more useful than reading each expiry in isolation.

**BTC 2026-03-16**:
- **Near-term (0-7 DTE)**: NEUTRAL bias. GEX +25.4M (dampening). OI: 29,416. The two expiries (17MAR and 20MAR) give conflicting signals — 17MAR has P/C 1.63 (bearish) but it expires today, while 20MAR has P/C 0.75 (bullish) with heavy buying. The near-term read is genuinely uncertain.
- **Mid-term (7-30 DTE)**: BULLISH bias. GEX +49.3M (dampening). OI: 198,758. The dominant expiry (27MAR) has P/C 0.66 and Heavy Buying — a clearly bullish structure. This is the signal with the most OI weight.
- **Far-term (30+ DTE)**: BULLISH bias. GEX +38.9M (dampening). OI: 250,610. Both major far expiries show call-dominant OI and bullish positioning.

**The pattern**: Near-term neutral, mid/far-term bullish. This is a classic "wait for the dust to settle on the front end, then buy the dip" setup. The uncertainty is concentrated in the next few days (17MAR expiry, 20MAR positioning), while the structural bullish case is intact beyond 10 days.

### Interpreting Conflicting Signals Across Expiries

Conflicting near vs far signals are common and meaningful.

**Example analysis — 17MAR vs 27MAR vs 3APR**:

| Expiry | P/C | Flow | Max Pain Distance | Interpretation |
|---|---|---|---|---|
| 17MAR26 (0d) | 1.63 | Moderate Selling | -0.6% | Expiry mechanics, not directional. Pin at $73K likely. |
| 27MAR26 (10d) | 0.66 | Heavy Buying | +2.2% | Genuine bullish positioning 10 days out. |
| 3APR26 (17d) | 0.45 | Heavy Selling | +0.8% | Extreme call dominance (0.45) with Heavy Selling. Contradictory. |

The 3APR26 read (P/C 0.45 + Heavy Selling) deserves explanation: P/C of 0.45 means massive call dominance, yet flow is Heavy Selling. This means the large institutional call sale ($43.89M BTC-3APR26-85000-C) is the dominant driver — it created massive call OI (numerator of P/C is high) while also being flagged as a sell in flow data. The OI is bullish-looking (lots of calls) but the flow is bearish (selling those calls). These are consistent once you recognize the block trade.

**Principle**: When OI structure and flow diverge, look at block trades for the explanation.

### OI Concentration as Institutional Signal

Heavy OI concentration at specific strikes often reflects institutional decisions (hedging programs, calendar spreads, structured products).

**BTC 2026-03-16 notable concentrations**:
- 27MAR26: $75,000 strike has 9,770 Call OI (by far the highest call strike). Max pain at $75K. Large institutional call wall.
- 25DEC26: $120,000 strike has 6,402 Call OI. Long-dated call concentration at 2x current price. Institutional long-term upside positioning.
- 24APR26: $70K-$75K range has the highest put OI concentration, suggesting downside hedging programs.

---

## 9. Decision Tree — The Full Flow

```
START: Open report. Spot = $73,409. Regime = VOL BULLISH.
│
├── STEP 1: What is the vol environment?
│   │
│   ├── DVOL (52.48) + IV Pctile (84th) + VRP (+1.5 pts)
│   │   │
│   │   ├── IV Pctile > 70th? YES → Premium elevated on a distributional basis
│   │   ├── VRP in ±5 neutral band? YES → No structural edge in selling/buying vol
│   │   └── Conclusion: Vol is ELEVATED but FAIRLY PRICED
│   │       → Avoid naked premium selling. Use defined-risk structures.
│   │       → Do NOT avoid options altogether — just use spreads.
│   │
├── STEP 2: Is there crowding risk?
│   │
│   ├── Funding rate (0.0002% 8h, ~0.26% ann.) → Near zero
│   │   └── No fragility. Directional signals get full weight.
│   │
├── STEP 3: What is the vol regime?
│   │
│   ├── Total Aggregate GEX sign?
│   │   ├── Near-term: GEX +25.4M (positive) → Dampening front end
│   │   ├── Mid-term: GEX +49.3M (positive) → Dampening mid
│   │   └── Far-term: GEX +38.9M (positive) → Dampening far
│   │       → Aggregate positive GEX → NOT explosive
│   │
│   ├── IV Pctile 84th + VRP +1.5 pts → ELEVATED vol regime
│   │
│   └── Vol Regime: ELEVATED
│
├── STEP 4: What is the directional signal?
│   │
│   ├── DEX: Positive across all expiries (+125.6, +102.0, +173.8 BTC)
│   │   → Dealers holding net short delta → buying spot to hedge → Bullish
│   │
│   ├── P/C ratio by expiry:
│   │   ├── Near-term (17MAR): 1.63 → BUT 0 DTE, score clamped, hedging mechanics
│   │   ├── Mid-term (27MAR): 0.66 → Bullish
│   │   ├── Far-term (24APR/26JUN): 0.59/0.75 → Bullish
│   │   └── Dominant signal: BULLISH
│   │
│   ├── Flow by expiry:
│   │   ├── 17MAR: Moderate Selling (puts dominate, but likely rolling)
│   │   ├── 18MAR, 19MAR, 20MAR: Heavy Buying
│   │   ├── 27MAR: Heavy Buying
│   │   └── Net flow: predominantly buying pressure
│   │
│   ├── Max pain gravity:
│   │   ├── 17MAR: $73K, -0.6% → pin gravity toward $73K (expiry today)
│   │   ├── 27MAR: $75K, +2.2% → weak upward pull
│   │   └── Near-flat → minimal max pain scoring
│   │
│   ├── Vanna/Charm: Both positive. With IV at 84th pctile (>60), positive vanna = bullish.
│   │
│   └── Directional Signal: BULLISH (confidence: 25%)
│       Note: 25% confidence is modest. No crowding, but signals are moderate not extreme.
│
├── STEP 5: What is the market regime?
│   │
│   ├── Direction BULLISH + Vol ELEVATED
│   └── Market Regime: VOLATILE_BULLISH
│       → Expect outsized upside with violent pullbacks
│       → Priority: long calls/call spreads with defined risk
│
├── STEP 6: What are the key structural levels?
│   │
│   ├── Near-term (today, 17MAR): Zero gamma at $73,500, put support $72K
│   ├── Mid-term (27MAR): Call resistance $75K–$76K, put support $70K
│   ├── Volume concentration: $73K–$73.5K is max pain / zero gamma cluster
│   └── Far resistance: $78K (27MAR), $80K (20MAR dominant), $85K (3APR block)
│
├── STEP 7: Skew and vol surface read
│   │
│   ├── Front skew +3.8% → Complacent. Puts relatively cheap.
│   ├── Mid skew +5.9% → Normal hedging demand.
│   └── No crash fear. Put premium normal to slightly cheap.
│       → Risk reversals (long call / short put) have skew-adjusted edge.
│
└── STEP 8: Trade conclusion
    │
    ├── Vol: Fairly priced (no edge in outright vol). Use defined-risk structures.
    ├── Direction: Bullish, 25% confidence, no fragility.
    ├── GEX: Positive aggregate → expect dampened moves but not explosive.
    ├── Regime: VOLATILE_BULLISH → buy vol on dips if IV cheapens.
    └── Recommendation: Bull Call Spread (24APR26 or 27MAR26 expiry)
        Target call resistance at $75K-$78K. Skew at +5.9% makes calls cheap relative to puts.
```

---

## 10. Reading Checklist — The 10-Point Quick Read

Run through this in order. Each step either confirms the thesis or raises a flag.

1. **Spot price + DVOL**: What is current vol relative to norm? (DVOL vs 40-60 normal range)

2. **IV Percentile**: Is premium cheap or expensive on a distributional basis? (>70th = expensive, <30th = cheap)

3. **VRP**: Is implied vol overpricing realized vol? (±5 pts = neutral, >+5 = sell signal, <-5 = buy signal)

4. **Funding rate**: Is positioning crowded? (>+0.03% 8h or <-0.03% 8h = crowding risk)

5. **Total GEX sign per term bucket**: Are dealers amplifying or dampening? (+ = dampening, - = amplifying)

6. **DEX direction**: Are dealer hedging flows bullish or bearish? (Positive = bullish, negative = bearish)

7. **P/C by expiry**: What does OI structure say across the term structure? (Ignore 0-DTE for direction, weight mid/far term)

8. **Flow bias by term bucket**: What has actual trading been doing in the last 24h? (Near + mid + far consensus)

9. **Skew level**: Is fear priced in? (+4-8% = normal, >8% = fear premium, <4% = complacent)

10. **Block trades**: Any institutional positions that distort the raw numbers? (Check before concluding on any expiry with extreme P/C)

**Synthesis cross-check**: After your own read, compare to the synthesis output. If they agree, confidence is higher. If they disagree, identify which signal is driving the divergence and decide whether the engine's weighting is correct for the current situation.

---

## 11. Worked Example — BTC 2026-03-16

Walking through the actual report using the framework above.

### Step 1: Market-Wide Read

| Metric | Value | Interpretation |
|---|---|---|
| Spot | $73,409.13 | — |
| DVOL | 52.48 | Normal-to-elevated range |
| IV Percentile | 84.2% | Premium elevated (84% of days had lower IV) |
| IV Rank | 34.1% | Appears low — distorted by prior spike (see section 2) |
| VRP | +1.5 pts | Inside neutral band. Vol fairly priced. |
| 8h Funding | 0.0002% (≈0.26% ann.) | Essentially zero. No crowding risk. |
| 10d/20d/30d RV | 33.9% / 53.3% / 51.0% | RV is elevated and near DVOL — VRP is thin |
| Term Structure | CONTANGO (-0.7 pts) | Nearly flat. No front-end stress. |
| Expected Daily Move | $2,016 (2.7%) | Large by typical standards |

**Conclusion from Step 1**: Volume environment is elevated but not extreme. No crowding. No stress in the term structure. The expected daily move of 2.7% tells you that 1.5–3% intraday swings are priced as normal. Don't be surprised by them.

### Step 2: Market Regime

Positive aggregate GEX across all term buckets (near: +25.4M, mid: +49.3M, far: +38.9M) → not in negative gamma amplifying environment. IV pctile 84th with VRP inside neutral band → ELEVATED vol regime. Directional signal from P/C and DEX sweeps is bullish, especially mid/far term.

**Regime: VOLATILE_BULLISH**. The synthesis says: "Bullish direction but negative gamma will amplify moves." Wait — this is specifically the 17MAR26 expiry GEX that is negative (-$3.44M). The aggregate is positive. So the correct read is: the expiring-today contract has negative gamma (today could be choppy), but the broader structure is positive gamma (market is range-suppressed structurally). The synthesis narrative is referring to the dominant near-term expiry's environment.

### Step 3: GEX Levels

For today's trade, the relevant GEX levels are:

**17MAR26 (expires today)**:
- Spot: $73,409. Zero gamma: $73,500 (91 points above spot).
- Put support: $72,000, Call resistance: $74,000.
- GEX is negative (amplifying for today). Any move can run farther than expected.
- Near-term delta hedge (DEX = +125.6 BTC, dealers need to buy spot) provides underlying support.

**27MAR26 (10 DTE — the dominant mid-term expiry with 195K OI)**:
- Call resistance: ~$76K-$78K zone (5,252 Call OI at $80K, 9,770 Call OI at $75K).
- Put support: $70,000-$72,000 band.
- GEX is positive — this expiry is stabilizing for the 10-day window.

### Step 4: Max Pain + P/C

| Expiry | Max Pain | Distance | P/C | Flow |
|---|---|---|---|---|
| 17MAR26 | $73,000 | +0.56% | 1.63 | Mod. Selling |
| 20MAR26 | $69,000 | -6.39% | 0.75 | Heavy Buying |
| 27MAR26 | $75,000 | +2.12% | 0.66 | Heavy Buying |
| 3APR26 | $74,000 | +0.80% | 0.45 | Heavy Selling |
| 24APR26 | $70,000 | -4.87% | 0.59 | Balanced |
| 26JUN26 | $80,000 | +9.0% | 0.75 | Heavy Buying |

**Reading this table**:
- 17MAR: High P/C but 0 DTE — expiry mechanics. The put buying throughout the day has been rolling/hedging the large put OI (2,088 vs 1,281 calls). The flow (selling puts at $68K, buying puts at $70-72.5K) confirms roll behavior.
- 20MAR: $69K max pain with P/C 0.75 and Heavy Buying. Bullish for the immediate window.
- 27MAR: P/C 0.66, Heavy Buying, max pain $75K above spot. The dominant mid-term expiry is clearly bullish. 195K contracts of OI here means this is the primary weight driver.
- 3APR: P/C 0.45 is bullish-looking, but the $43.89M institutional call sell is what created the call OI while being classified as "Heavy Selling" in flow. Net: institution sold calls = they are NOT bullish, or they are hedging a long BTC position.
- 26JUN: $80K max pain (+9% above spot) with Heavy Buying and P/C 0.75. Long-term participants positioning for continued upside.

**Synthesis**: Mid and far term are clearly bullish. Near-term is noise. Medium confidence bullish overall.

### Step 5: Vol Surface

Front skew = +3.8% (17MAR26). Puts are only marginally more expensive than calls. This is below the 4-8% "normal" range — the market is relatively complacent about downside for this expiry. For mid-term (27MAR), skew = +5.9% — puts command more premium at 10 DTE. The skew curve slopes upward with DTE, which is normal.

ATM IV term structure shows a mild kink at 19-20MAR (56.3%/56.9%) versus adjacent expiries. This is front-end distortion — likely pricing in weekend/Monday event risk — and should not be read as vol curve stress.

### Step 6: Flow

| Expiry | Calls Buy/Sell | Puts Buy/Sell | Bias |
|---|---|---|---|
| 17MAR26 | 330.9 / 375.8 | 891.9 / 1,114.1 | Moderate Selling |
| 18MAR26 | 390.8 / 341.3 | 511.3 / 279.2 | Heavy Buying |
| 19MAR26 | 398.7 / 348.1 | 432.4 / 126.9 | Heavy Buying |
| 20MAR26 | 1,171.3 / 843.9 | 1,089.8 / 795.8 | Heavy Buying |
| 24APR26 | 420.2 / 836.9 | 1,021.7 / 741.9 | Balanced |

The 17MAR selling is concentrated in puts being net-sold at $68K (deep OTM). Deep OTM put sellers are bullish — they are accepting downside risk below $68K for premium. This is not a bearish flow signal despite the "Moderate Selling" classification.

The 18, 19, 20MAR expiries all show Heavy Buying, which drives the near-term NEUTRAL aggregate (17MAR selling partly offsets). Without the 17MAR expiry roll mechanics, near-term would read more bullish.

### Final Conclusion

**Framework output for BTC 2026-03-16**:

- **Regime**: VOL BULLISH (VOLATILE_BULLISH)
- **Direction**: BULLISH, 25% confidence
- **Vol**: Fairly priced at VRP +1.5. No structural edge in buying or selling vol as a standalone trade.
- **Key levels**: Zero gamma / max pain cluster at $73,000-$73,500 today. Mid-term call wall at $75,000-$78,000.
- **Dealer structure**: Positive aggregate GEX = dampened environment for the next 10-30 days. Negative GEX on expiring-today 17MAR = today specifically can be choppy.
- **Institutional**: Large institutional call sale at $85K April. They are not positioned for a near-term explosive rally above $85K.
- **Trade**: Bull call spread on 27MAR26 (10 DTE) or 24APR26 (38 DTE) expiry. Buy near-ATM call, sell at $75K-$78K call resistance. Skew at +5.9% means calls are relatively cheap vs puts — advantageous for call buyers.

---

## 12. Common Mistakes and Misreadings

### Mistake 1: Treating IV Rank and IV Percentile as the Same Thing

They measure different things and will diverge significantly after a historical vol spike. In this report, IV Percentile is 84th but IV Rank is 34th. A trader looking only at IV Rank would conclude "premium is cheap" — which is wrong. Use percentile as the primary signal. IV rank is only reliable when there has been no extreme outlier event in the past 52 weeks.

### Mistake 2: Using Max Pain as a Precise Price Target

Max pain is a structural gravity point, not a forecast. "Max pain at $73K" does not mean price will close at $73K. It means there is OI-weighted gravitational force toward that level, strongest in the final 24-48 hours before expiry. In liquid crypto markets, max pain gravity is weaker than in equity options because the OI base can shift rapidly. Treat it as a secondary confirmation, not a primary signal.

### Mistake 3: Assuming High P/C = Bearish Directional Positioning

P/C ratio can be elevated for three completely different reasons: (1) directional put buying (bearish), (2) portfolio hedging (risk management, not directional), or (3) roll activity on near-expiry puts. The 17MAR26 P/C of 1.63 is example (3) — it is a 0-DTE expiry with puts rolling out. The flow data (put selling at $68K, rolling patterns) is the tool to distinguish between these cases. Never read a high P/C in isolation.

### Mistake 4: Conflating Per-Expiry GEX with Aggregate GEX

The 17MAR26 expiry has negative GEX (-$3.44M) — amplifying. But the aggregate GEX across all expiries is positive (near +25.4M, mid +49.3M, far +38.9M). If you only read the 17MAR GEX and conclude "dealers are short gamma, expect explosive moves," you are missing the broader stabilizing structure. The aggregate tells you the net portfolio behavior of dealers; the per-expiry tells you the localized behavior for specific horizons.

### Mistake 5: Treating Heavy Buying in Puts as Bearish

"Heavy Buying" in the flow section is buyer-initiated trades dominating. "Heavy Buying in puts" means buyers of put options — which is bearish directionally. BUT: the strike level matters. Buying near-the-money puts close to expiry is often hedging or rolling existing long positions. Buying deep OTM puts far out in time is directional. Buying puts while simultaneously selling deeper puts (visible in the top buys/sells tables) is a spread, which is limited-risk hedging with a defined bearish target — not panic protection.

### Mistake 6: Ignoring the VWAP vs Mark IV Signal

VWAP IV vs Mark IV tells you whether buyers or sellers were more aggressive in execution. A large negative spread (VWAP significantly below Mark) means sellers were hitting bids — they wanted to sell and were willing to accept below-mid prices. This is a higher-conviction signal than just flow classification. In the 20MAR26 expiry, VWAP 57.1% vs Mark 70.8% (diff -13.7%) is a strong seller-aggression signal — institutions were dumping this expiry's options. Combined with the "Heavy Buying" bias (buys dominate in volume), this is a mixed signal: more buys in volume, but sellers were more desperate. The large put selling at $60K-$68K (block sizes) resolves this: institutions were selling deep OTM puts (premium collection = bullish trade) while the retail-dominated call buying drove the flow bias to Heavy Buying.

### Mistake 7: Overweighting the Synthesis Output

The synthesis is the engine's interpretation of the data. It has a 25% directional confidence score for good reason — the signals are not strongly aligned. A 25% confidence BULLISH signal is not the same as a conviction call. The confidence score tells you how much to size your directional expression. At 25%, this is a reduced-size or optionality-only trade, not a full capital deployment.

### Mistake 8: Forgetting the GEX Zero Line as a Volatility Regime Pivot

When spot is near the per-expiry zero gamma level, small price moves can shift the entire hedging dynamic for that expiry. In the 17MAR26 expiry, spot is $73,409 vs zero gamma at $73,500 — a $91 gap. Moving above $73,500 transitions dealers from short-gamma to long-gamma on this expiry, dramatically changing how they hedge. This is not just a resistance level — it is a regime change threshold. Expiry-day trades near the zero gamma line carry higher-than-usual gamma risk.

---

*This guide references the BTC on-chain analysis report generated 2026-03-16 15:55:44. All specific data points are from that report. Thresholds and scoring logic reference the synthesis engine specification in `synthesis_logic.md`.*
