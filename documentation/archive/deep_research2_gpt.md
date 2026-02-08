# Institutional Review of the ML Regime Detection Plan as an Options Trading Core

## Scope, assumptions, and what the submitted plan actually covers

The document you provided is not an “institutional options trading plan” in the usual sense (i.e., explicit option strategy families, trade selection rules, sizing, hedging, risk limits, execution playbooks, and performance objectives). It is an institutional-style **market regime detection and forecasting system design**, intended to **replace/augment a rule-based regime classifier** and to become a production component for crypto trading decisions. fileciteturn0file0

That distinction matters because **a regime model is not an edge by itself**; it is (at best) a **state classifier / conditional risk model** that can improve risk-adjusted returns *only if* it is paired with a clear, testable “policy” mapping regimes → exposures → trades, and that whole pipeline is evaluated on **downstream trading outcomes** (net of costs + constraints), not just classification metrics. The current plan gestures at this (“backtesting integration,” “strategy scoring improved Sharpe”) but does not specify the downstream options strategy, the constraints, or a capital/risk budget. fileciteturn0file0

Because the downstream options policy is unspecified, this report critiques (a) the regime system you provided and (b) the missing institutional components that must exist for it to become a defensible options trading plan on venues like entity["company","Deribit","crypto derivatives exchange"]. fileciteturn0file0turn11search6turn11search3

## Data and feature integrity for institutional options use

### The “historical backfill is solved” claim is directionally right, but operationally under-specified

Your plan hinges on obtaining enough historical options information to train a regime model and claims the key breakthrough is pulling historical options trades via the API and computing Greeks from implied vol. fileciteturn0file0

At the API level, the core premise is supported: the endpoint `public/get_last_trades_by_currency_and_time` returns trade-level fields including **price, mark_price, index_price, instrument_name, and `iv` (implied volatility)** for options trades, with pagination (`has_more`) and a per-call `count` capped (documented “maximum 1000”). citeturn3view0turn4view0 This is sufficient to assemble a large trade tape if the platform allows deep historical querying and if you engineer around practical rate limits and pagination. citeturn3view0turn10view2

However, your plan’s “~1 week” claim is a **throughput and compliance question**, not a conceptual one:

- entity["organization","Deribit Support","help center"] documents a credit-based rate limiting system (with tiered sustained/burst rates and additional public access limitations), and recommends authenticated traffic for higher, more transparent limits. citeturn10view2turn17search5  
- Connection constraints (e.g., max simultaneous connections per IP and session limits) exist and matter when you attempt multi-worker historical ingestion. citeturn17search6turn17search2  
- Market structure events (notably daily settlement around **08:00 UTC** with a brief trading pause and order-handling restrictions) can break ingestion and live inference/execution if not explicitly handled. citeturn10view3turn17search4  

**Institutional critique:** the plan is missing a concrete ingestion design with (1) paging logic, (2) idempotent storage + deduplication, (3) rate-limit-aware scheduling, (4) backfill verification (coverage by hour/day), and (5) a legal/ToS check on automated collection. In particular, entity["company","Deribit","crypto derivatives exchange"] membership terms include language restricting systematic data collection and redistribution without approval; even if you are collecting for internal research, institutional setups typically document this risk explicitly. citeturn17search24

### A critical flaw: “infer open interest from cumulative volume” is not valid

Your Phase 0 plan proposes: “Infer Open Interest from cumulative volume.” fileciteturn0file0

This is not just imprecise—it is structurally wrong. **Open interest (OI)** is the number of outstanding contracts that remain open; it is not cumulative traded volume. Exchanges can have enormous volume with flat or declining OI, and OI changes are driven by whether trades open/close positions on each side. citeturn16view0

More importantly, the API already provides OI for derivatives via summary endpoints. For example, `public/get_book_summary_by_instrument` returns `open_interest` and defines it as outstanding contracts (with units depending on instrument type). citeturn16view0

**Actionable fix:** remove “infer OI from volume” entirely; instead:
- Pull OI directly from `public/get_book_summary_by_instrument` (or by currency) at your chosen sampling schedule. citeturn3view1turn16view0  
- If you require historical OI time series and the venue does not provide deep history for OI, use a vendor that explicitly provides historical OI; for example, entity["company","Laevitas","crypto options analytics provider"] documents endpoints for historical total OI by currency/maturity. citeturn5search6  

This single correction is “institutional-grade mandatory,” because OI is a core state variable for regime inference (liquidity/positioning), and because misconstruing OI can invert the meaning of positioning features and poison training labels.

### You will need to model *futures basis and contract design* explicitly for BTC options

A subtle but important point for options on entity["company","Deribit","crypto derivatives exchange"]: their own research notes that “BTC options are actually BTC future options,” i.e., the options are written on (or reference) a futures instrument, introducing **basis / implied interest rate** risk and changing delta behavior relative to spot. citeturn10view4

Your plan currently treats “underlying price” generically and suggests computing Greeks from IV via the classic 1973 option framework. That can be directionally acceptable for baseline analytics, but only if the “underlying” used in Greeks matches the contract’s actual reference (spot vs future) and you track basis dynamics as a separate risk factor. citeturn10view4turn3view0turn0search3

**Minimum institutional requirement:** Greeks and P&L attribution must be computed under the correct numeraire and underlying reference (spot vs future vs index), or the hedge ratios you optimize will be systematically wrong. citeturn10view4turn11search14

### “Greeks from IV using Black–Scholes” is a reasonable baseline—but the model risk must be explicit

Your plan proposes: “Greeks can be calculated from the provided IV using Black-Scholes.” fileciteturn0file0 This is consistent with the idea that once IV is known, a parametric model can produce Greeks (delta/gamma/vega/theta) as sensitivities. citeturn0search3turn3view0

But crypto markets are empirically jumpy and high-vol; the literature on crypto option pricing and hedging emphasizes discontinuities/jumps and stressed regimes, which can degrade delta-hedging performance and make local Greeks less stable. citeturn9search8turn9search1

**Institutional improvement:** treat “Greeks-from-IV” as an **approximation layer** with:
- model risk flags (where/when B&S Greeks are unreliable),
- alternative sensitivity proxies (exchange-provided mark IV + greeks when available, or bump-and-reprice under implied surface shocks),
- and stress-tested hedging under jump and volatility-of-volatility regimes. citeturn9search8turn13view2  

## Model design, validation, and model-risk governance

### The architecture is ambitious; your biggest modeling risk is not “accuracy,” it is *weakly-defined truth*

Your plan outlines a multi-modal architecture with cross-attention, temporal modeling (Bi-LSTM/Transformer), and dual heads for current regime detection and multi-horizon predictions. fileciteturn0file0 The general idea is aligned with powerful multi-horizon forecasting approaches (e.g., gating/attention-based sequence models). citeturn1search1turn1search17

The institutional failure mode is: **you have not defined what “regime” means in a way that is economically anchored.** You propose generating labels from the existing rule-based detector (“use current MarketRegimeDetector on historical data”). fileciteturn0file0

That creates a “teacher-student” loop: the neural model is trained to reproduce the rule system’s output distribution, limited by the rule system’s bias/ceiling. Unless you add independent truth, the ML model may become a smoother, more confident version of your heuristics—possibly worse if it becomes overconfident on edge cases. This is a classic model risk scenario under the definition of model risk management: adverse consequences from incorrect or misused model outputs. citeturn13view1

**Institutional fix: define regimes with at least one economically objective labeling scheme**, then use the rule-based model as *one* signal, not the ground truth. Examples of objective regime targets include:
- realized volatility buckets + trend strength buckets,
- drawdown state (crash / recovery / calm),
- option-surface state variables (term structure, skew, VRP condition). citeturn10view4turn9search8turn1search0

Markov-switching models are a canonical baseline for latent regime inference in time series; even if you don’t deploy them, they are valuable as a benchmark and as a sanity check for regime persistence/transition structure. citeturn1search0

### A concrete modeling bug: Bi-directional LSTM is not causal

Your recommended “Option A” is Bi-LSTM for temporal modeling. fileciteturn0file0

For live regime detection/prediction, a Bi-LSTM uses both past and future context during inference (because it processes sequences in both directions). That is not available in real time, and if you train/evaluate with Bi-LSTM without strict causal masking, you can inadvertently introduce look-ahead leakage.

**Institutional fix:** switch to a **causal** temporal model for anything that will be used online:
- unidirectional LSTM/GRU,
- causal Transformer with masking,
- or TFT-style architectures that respect time causality. citeturn1search17turn1search2

### Calibration is not optional when you plan confidence gating

Your plan uses confidence thresholds (e.g., “use ML predictions when confidence > 0.75”) and includes a calibration loss via Brier score. fileciteturn0file0

Two institutional points:

1) Modern neural nets are often miscalibrated; post-hoc calibration (e.g., temperature scaling) is frequently required even if you optimize cross-entropy. citeturn1search2turn1search6  
2) If your confidence drives capital allocation (sizing, strategy selection, hedging intensity), then calibration errors become first-order risk drivers—not cosmetic metrics. citeturn13view1turn1search2  

**Actionable calibration protocol (institutional style):**
- Train model; freeze; calibrate probabilities on a rolling out-of-sample calibration set (e.g., last 30–90 days) using temperature scaling. citeturn1search2turn1search6  
- Monitor Expected Calibration Error and reliability diagrams; update calibration parameters more frequently than weights. citeturn1search2turn1search6  

### Backtesting and selection bias controls are missing and are mission-critical

Your plan proposes offline metrics (accuracy/F1/confusion/calibration) and then backtesting integration. fileciteturn0file0

Institutional research standards require **defenses against overfitting and multiple-testing bias**, especially once you start tuning architecture, features, and regime-to-strategy mapping. Work on backtest overfitting and deflated Sharpe formalizes why “best backtest Sharpe” can be a statistical mirage under multiple trials and non-normal returns. citeturn2search0turn2search1

**Actionable amendments:**
- For any regime-conditioned strategy backtest, report (at minimum) Sharpe + drawdown + skew/kurtosis + turnover + net-of-fees results, and include a deflated Sharpe (or a comparable multiple-testing adjustment) when you have tried many model/parameter variants. citeturn2search1turn2search0  
- Use combinatorially symmetric cross-validation (or rigorous walk-forward schemes) to estimate the probability the strategy is overfit. citeturn2search0  

### Governance: treat the regime model as a “trading model” requiring effective challenge

Even if you are not a bank, the governance principles in entity["organization","Federal Reserve","us central bank"] SR 11-7 are an institutional template: robust development, independent validation, and strong governance/controls, with “effective challenge” by informed, independent parties. citeturn13view1

Your plan contains pieces (monitoring, divergence alerts vs rule-based baseline), but it is missing:
- explicit model inventory/versioning controls tied to trading permissioning,
- pre-trade and post-trade model validation gates,
- and “kill-switch” conditions tied to P&L and risk, not just classification accuracy. citeturn13view1turn0file0  

## Strategy design and expected return drivers the regime model must serve

### Your plan needs an explicit “options edge thesis” to be institutionally evaluable

Institutional options trading is rarely “directional guessing.” It is usually harvesting and managing identifiable return sources such as:
- variance risk premium / volatility risk premia,
- skew risk premia,
- carry/roll-down in term structure,
- microstructure/liquidity premia,
- basis/funding differentials in crypto derivatives. citeturn1search3turn10view4turn9search27

Your regime model will only matter if it helps you **time, size, and hedge** exposure to these premia.

A concrete anchor from entity["company","Deribit","crypto derivatives exchange"] research: in their analysis, when term structure is in contango, the 30-day VRP mean is around +15 vol points (their framing: options overprice realized vol by ~15 points, with $ value scaling to vega). citeturn10view4 That is a tradable hypothesis: “sell vol when contango + favorable skew state,” but it must be framed with tail-risk controls and liquidity-aware execution.

Similarly, classic variance risk premium research formalizes the concept via variance swaps and options replicating portfolios. citeturn1search3 In crypto, the existence and regime-dependence of risk premia (including VRP) is also documented in more recent work using options data and regime clustering on risk-neutral densities. citeturn9search3turn9search30

### A practical map: regimes should map to Greek targets, not just labels

For an institutional options book, “regime classes” should map to **target exposures** (Δ/Γ/Vega/Θ and often vanna/volga), plus constraints (max loss under scenarios, margin utilization, liquidity). This is where the regime model becomes a risk layer.

Below is a defensible **template** for a regime-to-policy mapping (illustrative, not a recommendation to trade a specific strategy without your constraints and costs):

| Regime state variable (from model) | Typical environment hypothesis | Target exposure (portfolio-level) | Typical strategy families | Primary failure mode |
|---|---|---|---|---|
| High VRP, liquid, contango term structure | Implied > expected realized | +Θ, -Vega, controlled -Γ | short strangles/straddles, risk-defined spreads | jump/gap risk; liquidity evaporation citeturn10view4turn9search8 |
| Low/negative VRP, backwardation | Implied cheap vs realized/tails | -Θ, +Vega, +Γ | long gamma (straddles), calendars/diagonals | bleed if realized stays low citeturn10view4turn1search3 |
| Trend + low-to-moderate vol | directional drift dominates | directional Δ via risk-defined convexity | call/put spreads, risk reversals | basis drift vs spot; skew shifts citeturn10view4turn11search14 |
| Stress / jump regime | fat tails active | convexity prioritized, strict risk caps | long convex fly/straddle, crash hedges | execution slippage, liquidations citeturn9search8turn10view3 |

This table is the missing “policy layer” that your plan does not specify. Without it, the model is a dashboard feature rather than a trading plan.

### Payoff geometry matters: regime models can accidentally push you into the wrong convexity

A major institutional pitfall is letting a regime classifier implicitly encourage “selling vol” for too long because the model is trained to smooth regimes and penalize switching. If that happens, you can systematically accumulate **short convexity** into a tail event.

To emphasize why the regime-to-policy layer must be explicit, here is an illustrative payoff comparison at expiry (not a backtest; just geometry). Premiums are computed under the classic 1973 framework with assumptions shown on the chart. citeturn0search3

![Illustrative payoff diagram](sandbox:/mnt/data/payoff_diagram.png)

Even without any forecasting, this chart shows the core economic decision: are you being paid enough carry/VRP to hold concave payoffs (short straddle/strangle), and do you have credible hedges and kill-switches for the convexity blow-up regime? citeturn10view4turn9search8

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["options volatility surface skew plot","implied volatility smile skew term structure chart"],"num_per_query":1}

## Risk management, stress testing, and execution realism

### Benchmarking against institutional margin and risk standards

Institutional options desks (and clearinghouses) typically think in **portfolio scenario distributions**, not simple stops. A useful benchmark is how entity["organization","The Options Clearing Corporation","options clearinghouse us"] describes its STANS margin methodology: portfolio-level Monte Carlo simulations intended to achieve high assurance that the portfolio value plus posted collateral is not materially negative over a short risk horizon. citeturn10view0turn13view0

Crypto venues implement analogous scenario-based risk matrices. entity["company","Deribit","crypto derivatives exchange"]’s Portfolio Margin Engine (PME) describes valuing the portfolio over a grid of underlying price and volatility moves, with parameter settings that can change (risk team discretion). citeturn13view2turn11search11

Your plan’s risk section is mostly “model risk” (drift, confidence, divergence), but what is missing for an institutional options plan is a **trading risk envelope** that is at least as strict as the venue’s own risk matrix. fileciteturn0file0turn13view2

**Actionable institutional upgrade:** adopt a two-layer risk system:

- **Layer A: venue-consistent scenario risk.** Replicate the exchange’s risk matrix (or a stricter internal one) and require that every proposed position keeps worst-case loss and margin utilization under hard limits. citeturn13view2turn11search3  
- **Layer B: tail overlays.** Add jump and vol-of-vol shocks beyond the venue grid, because margin grids are not a guarantee against gap risk. Crypto hedging literature explicitly separates calm vs stressed scenarios and highlights jump-driven hedge degradation. citeturn9search8  

A practical stress grid aligned with PME-style thinking (illustrative; calibrate to your book) would include:

- Spot/futures moves: ±5%, ±10%, ±15% (noting PME parameters cite ±15% for BTC/ETH in one documented configuration). citeturn13view2  
- Parallel IV shift: -30%, +45% (again aligning with one documented PME volatility range configuration). citeturn13view2  
- Skew twist: steepen/flatten risk reversal by fixed vol points for wings (because Deribit’s own work shows VRP behavior differs by skew regime). citeturn10view4  
- Basis shock: futures-spot basis widen/narrow (critical because BTC options reference futures). citeturn10view4  

### Stops, position sizing, and hedging: what the plan must specify

Your regime plan does not define sizing, stop-loss rules, or hedge mechanics. For options trading, these must be defined in the language of:

- **Exposure budgets:** max gross vega, max net gamma, max net delta, gross notional, and concentration caps by expiry/strike. citeturn11search3turn10view0  
- **Liquidity-aware sizing:** smaller size where bid/ask and market impact dominate; crypto options research finds illiquidity has measurable effects on option returns and should be treated as a priced risk. citeturn9search27  
- **Hedge instruments and hedge frequency:** spot, futures, perpetuals; and how you manage basis and funding as part of hedge carry. citeturn10view4turn15search14  

**Institutional critique:** A “stop-loss” framed as “close the trade when premium is down X%” is usually inferior to a **scenario-based stop** for options books (e.g., if projected loss under a 10% spot move + IV upshock exceeds limit, cut risk), because option P&L is path-dependent and nonlinear. Clearinghouse and exchange margin frameworks reinforce the scenario mindset. citeturn10view0turn13view2

### Execution and microstructure: the plan must incorporate exchange mechanics

A solid institutional plan includes not only signal generation but a “last-mile” execution doctrine. For entity["company","Deribit","crypto derivatives exchange"] specifically (examples of required operational hooks):

- **Settlement window handling:** daily settlement around 08:00 UTC includes a brief matching pause and rejects API actions with a settlement-in-progress condition. You need “no-trade/no-cancel” logic and a safe mode around that boundary. citeturn10view3turn17search4  
- **Order management best practice:** use targeted mass cancels (`cancel_all_by_currency` / `cancel_all_by_instrument`) and quoting cancels when relevant; this is explicitly documented as latency-critical during fast moves. citeturn17search1turn17search19  
- **Connection management:** prefer WebSocket subscriptions over polling; avoid opening/closing sockets like REST; respect connection limits. citeturn17search2turn17search6  
- **Institutional block liquidity:** large trades may be executed via Block RFQ / block trades, which appear in public trade history with identifying fields; this matters for data labeling and for your own execution choices in thin markets. citeturn17search3turn17search7  

**Institutional fix:** add an “execution module spec” that defines:
- entry style (maker vs taker, choice by liquidity state),
- re-quoting logic and cancel thresholds under rate limits,
- and fallback behavior under API `too_many_requests` errors. citeturn10view2turn17search5  

## Prioritized improvements and a refined blueprint that becomes an actual institutional options plan

### Highest-impact corrections to the submitted regime plan

1) **Replace “infer OI from volume” with real OI series.** Pull `open_interest` from summary endpoints; optionally augment with entity["company","Laevitas","crypto options analytics provider"] for historical OI series by maturity. citeturn16view0turn5search6  

2) **Remove Bi-LSTM from any live path.** Use causal temporal modeling to avoid look-ahead bias. citeturn1search17turn0file0  

3) **Stop training purely on rule-based labels.** Introduce an economically grounded labeling scheme (realized vol/trend/drawdown/surface states), and treat the rule-based detector as a baseline model/feature. citeturn13view1turn1search0turn0file0  

4) **Prove value on trading objectives, not classification metrics.** Classification metrics are necessary but insufficient; adopt backtest-overfitting controls (deflated Sharpe, PBO approaches) and report net-of-fees/impact results. citeturn2search1turn2search0turn0file0  

5) **Explicitly include basis risk in feature set and hedging.** Because “BTC options are actually BTC future options,” you must track basis and its effects on delta/hedge P&L. citeturn10view4  

### Minimal institutional “complete plan” structure to add around the regime model

Below is a blueprint that converts your current artifact into an actual institutional options trading plan (each element is a deliverable with explicit acceptance criteria):

**Trading objective and edge thesis**
- Define whether the primary objective is VRP harvesting, tail-hedged carry, directional convexity, relative value on skew/term structure, or market making. citeturn1search3turn10view4  
- Specify the “why now / why sustainable” hypothesis, e.g., VRP conditional on contango + skew regime, illiquidity premium harvesting with strict tail caps, etc. citeturn10view4turn9search27  

**Trade selection and portfolio construction**
- Define the trade universe (expiries, strikes, instruments; spot vs futures underlyings) consistent with contract specs (European, cash-settled, expiry timing). citeturn11search6turn11search33  
- Define a regime-conditioned policy mapping: regime probs → target Greeks → allowable strategy families. citeturn0file0turn1search2  

**Risk management protocols**
- Hard limits: max loss under scenario grid, max margin utilization, max Greeks by bucket, max concentration. Benchmark your grid to exchange portfolio margin logic. citeturn13view2turn11search3turn10view0  
- Hedge doctrine: instruments, frequency, basis/funding treatment, and stressed-mode behavior. citeturn10view4turn9search8turn15search14  

**Execution tactics**
- Rate-limit-aware order management, mass cancel, settlement window safe mode, and a block/RFQ pathway for institutional size. citeturn17search1turn10view3turn17search7  

**Evaluation and stress testing**
- Performance: Sharpe/Sortino/Calmar + max drawdown + tail risk metrics + turnover + net-of-fees + capacity estimates.  
- Statistical hygiene: deflated Sharpe / PBO analysis when iterating on signals/models. citeturn2search1turn2search0  
- Scenario: spot shocks, IV shocks, skew twists, basis shocks, jump scenarios; validate hedging behavior in stressed regimes. citeturn13view2turn9search8turn10view4  

**Model governance**
- Adopt SR 11-7-like governance: model inventory, independent validation (effective challenge), monitoring, change control, and kill-switch processes. citeturn13view1turn0file0  

### A concrete implementation diagram that reflects institutional control flow

```text
Market data (spot/futures/options/on-chain/sentiment)
  └─► Feature alignment + data QC (timestamp integrity, leakage checks, missingness)
        └─► Regime model (causal, calibrated probabilities + uncertainty)
              └─► Policy layer (regime probs → target Greeks → candidate trades)
                    └─► Risk engine (scenario grid + Greeks limits + margin simulation)
                          └─► Execution engine (liquidity-aware routing, cancels, RFQ/block)
                                └─► Post-trade attribution (P&L by delta/gamma/vega/basis)
                                      └─► Monitoring + drift + governance gates
                                            └─► Controlled retraining / recalibration
```

### Final diagnostic: the plan is “institutional ML infrastructure,” not yet an institutional options plan

As written, your submission is strongest as a production-grade ML system specification (data pipelines, model structure, monitoring, deployment phases). fileciteturn0file0

To become a true institutional options trading plan, it must be paired with:
- an explicit edge thesis tied to known drivers like VRP/skew/basis/illiquidity, citeturn1search3turn10view4turn9search27  
- an explicit regime-conditioned trading policy,
- and an institutional risk/execution framework benchmarked to CCP/exchange scenario thinking and rigorous model governance standards. citeturn10view0turn13view2turn13view1