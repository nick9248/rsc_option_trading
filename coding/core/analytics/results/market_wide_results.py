"""
Result models for market-wide (cross-expiry) metrics.

Frozen dataclasses per refactor_design_spec.md section 2.5. Mirror the dict
shapes historically produced by ``MarketWideCalculator``'s ``calculate_*``
methods and assembled into ``OnChainAnalyzer.market_wide_structured``.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from coding.core.analytics.results.gex_dex_results import GexDexResult


@dataclass(frozen=True)
class TermStructureEntry:
    """One expiration's point on the IV term structure curve."""

    expiration: str
    dte: int
    atm_iv: float


@dataclass(frozen=True)
class TermStructureResult:
    """IV term structure across expirations."""

    entries: Tuple[TermStructureEntry, ...]
    shape: str  # "CONTANGO" | "BACKWARDATION" | "FLAT"
    spread: float  # unsigned pts (scoring)
    spread_signed: float  # signed pts (display)
    iv_by_dte: Dict[int, float]


@dataclass(frozen=True)
class SkewTermStructureEntry:
    """
    One expiration's row in the SKEW TERM STRUCTURE table
    (institutional_metrics_spec.md section 3(c), Task C4) -- the
    delta-interpolated RR25/BF25 for one expiry, plus each metric's own
    30d percentile/regime (institutional_metrics_spec.md section 1's
    HistoricalNormalizer pattern, applied per-expiry against
    ``volatility_skew_history``).

    ``rr_25d``/``bf_25d``/``atm_iv_interp`` are None exactly when
    ``VolatilitySurfaceCalculator.calculate_risk_reversal_butterfly()``
    returned None for that quantity (chain does not bracket the target
    delta -- never extrapolated, section 3(b) step 5). The percentile
    fields are None independently whenever the underlying value is None,
    OR whenever the metric's own trailing 30d history has fewer than
    ``HistoricalNormalizer.MIN_OBS`` observations (C1's "insufficient
    history" pattern -- expected to be the case for a while after this
    table starts accumulating, per Decision D10).
    """

    expiration: str
    dte: float  # fractional days (spec's table shows e.g. "0.6d", "153.6d")
    atm_iv_interp: Optional[float]
    n_quotes_used: Optional[int]

    rr_25d: Optional[float]
    rr_percentile_30d: Optional[float]
    rr_regime_30d: Optional[str]
    rr_n_30d: int

    bf_25d: Optional[float]
    bf_percentile_30d: Optional[float]
    bf_n_30d: int


@dataclass(frozen=True)
class SkewTermStructureResult:
    """
    Full SKEW TERM STRUCTURE across expirations
    (institutional_metrics_spec.md section 3(c)).

    ``rr_slope``: RR25(back) - RR25(front) -- back is the LAST entry
    (farthest DTE), front is the FIRST entry (nearest DTE) of ``entries``
    (already sorted by DTE ascending), matching the spec's own worked
    numeric example exactly (25JUL26 -3.80 vs 25DEC26 -4.34 -> -0.54);
    the surrounding prose ("over the two nearest standard expiries") is
    reconciled to that numeric example, not read literally -- see the
    Task C4 report's judgment-call note. None when fewer than 2 entries
    have a non-None ``rr_25d``.
    """

    entries: Tuple[SkewTermStructureEntry, ...]
    rr_slope: Optional[float]


@dataclass(frozen=True)
class GammaRolloffRow:
    """
    One expiry's row in the GAMMA ROLL-OFF table
    (institutional_metrics_spec.md section 5, Task C6).

    ``share_pct``/``cum_share_pct`` are computed on ``|net_gex|`` (a
    gross-magnitude denominator, section 5(b)) and are ``None`` for every
    row when the book has no gamma anywhere (``GammaRolloffResult.
    gross_total == 0``). ``net_gex``/``cum_net_gex`` stay signed -- the
    column header this feeds must say "signed" (section 5(c)).
    """

    expiration: str
    dte_days: float
    net_gex: float
    share_pct: Optional[float]
    cum_share_pct: Optional[float]
    cum_net_gex: float


@dataclass(frozen=True)
class GammaRolloffResult:
    """
    Full GAMMA ROLL-OFF profile across all expiries
    (institutional_metrics_spec.md section 5, Task C6): built from
    ``GexDexCalculator.calculate_rolloff_profile``'s dict return, rows
    sorted chronologically (ascending DTE).

    ``gamma_cliff_7d`` is a presentation flag ("more than 30% of gamma mass
    expires within 7 days"), not a trading signal -- see
    ``format_gamma_rolloff_section``, which states that explicitly on the
    rendered line. ``cum_share_7d``/``cum_share_30d`` are ``None`` exactly
    when ``gross_total == 0`` (no gamma anywhere).
    """

    rows: Tuple[GammaRolloffRow, ...]
    gamma_cliff_7d: bool
    cum_share_7d: Optional[float]
    cum_share_30d: Optional[float]
    gross_total: float


@dataclass(frozen=True)
class ForwardVolBucket:
    """
    One adjacent-expiry pair's row in the FORWARD VOL table
    (institutional_metrics_spec.md section 8, Task C9): forward variance
    between the nearer (``t1_days``) and farther (``t2_days``) expiry,
    via variance additivity (r = q = 0).

    ``fwd_vol_pct`` is ``None`` exactly when ``negative_variance`` is
    ``True`` (the calendar-spread-arb/data-error signal -- genuine
    implied vol cannot produce a negative forward variance; it signals a
    stale ATM IV on one leg, not a trade). ``event_premium`` is
    ``Optional[float]``: ``None`` whenever neither immediate neighbour
    bucket has a usable (non-negative-variance) forward vol to compare
    against -- covers both "only 2 expiries, no neighbours at all" and
    "the only neighbour is itself negative-variance".
    """

    from_expiry: str
    to_expiry: str
    t1_days: float
    t2_days: float
    sigma1_pct: float
    sigma2_pct: float
    fwd_var: float
    fwd_vol_pct: Optional[float]
    negative_variance: bool
    event_premium: Optional[float]
    flags: Tuple[str, ...]


@dataclass(frozen=True)
class ForwardVolResult:
    """
    Full FORWARD VOL curve across adjacent expiry pairs
    (institutional_metrics_spec.md section 8, Task C9).

    ``has_negative_variance`` drives the report's "DATA QUALITY" header
    warning (spec section 8(c) edge cases) -- derived, not independently
    settable, so it can never desync from the buckets it summarizes.
    """

    buckets: Tuple[ForwardVolBucket, ...]

    @property
    def has_negative_variance(self) -> bool:
        return any(b.negative_variance for b in self.buckets)


@dataclass(frozen=True)
class FuturesBasisEntry:
    """Annualized basis for one futures instrument."""

    instrument_name: str
    dte: Optional[int]
    mark_price: float
    index_price: float
    # Optional[float]: None when annualization is suppressed for a
    # sub-daily tenor, or the tenor is already expired (bugfix_spec.md
    # Item 5; Decision D12 — None means weight-zero downstream, never
    # "neutral"). The raw (unannualized) basis is still computable from
    # mark_price/index_price even when this is None.
    annualized_premium_pct: Optional[float]


@dataclass(frozen=True)
class FuturesBasisResult:
    """Futures basis across all future instruments."""

    entries: Tuple[FuturesBasisEntry, ...]
    # {expiry_label: annualized_pct} — synthesis reads this. Values are
    # Optional[float] (Decision D12): None for a suppressed/expired tenor,
    # filtered out (weight-zero) rather than treated as neutral by every
    # consumer (see synthesis.py's basis_values list-comprehension filter).
    futures_basis: Dict[str, Optional[float]]

    def to_dict(self) -> Dict[str, Any]:
        """
        Reproduce the legacy ``calculate_futures_basis`` structured-dict
        shape (``{"futures_basis": {...}}``) for the remaining dict
        consumer — ``market_wide_structured``, read by
        ``SynthesisMapper.build_market_wide`` until T7 migrates it off the
        dict (T6 carryover, A4 review).
        """
        return {"futures_basis": dict(self.futures_basis)}


@dataclass(frozen=True)
class RealizedVolatilityResult:
    """Multi-window realized volatility (10d/20d/30d), as decimals."""

    rv_by_window: Dict[int, float]  # {10: .., 20: .., 30: ..}, decimals (0.585 = 58.5%)

    # Task Wave-J-D Fix 1: ``calculate_realized_volatility_multi_window``
    # (market_wide_calculator.py, Wave-H-D) deliberately OMITS a window key
    # from ``rv_by_window`` when that window's RV couldn't be computed --
    # "missing means missing", never a fabricated 0.0. These properties
    # used to convert that deliberate omission right back into a fabricated
    # ``0.0`` via ``.get(window, 0.0)``, silently defeating the Wave-H-D
    # fix one layer up (a ``0.0`` here reads as "measured zero realized
    # vol", not "not computed", and feeds straight into score_vrp's
    # forward-RV-correction formula as a real number). ``.get(window)``
    # (no default) preserves the None instead.
    @property
    def rv_10d(self) -> Optional[float]:
        return self.rv_by_window.get(10)

    @property
    def rv_20d(self) -> Optional[float]:
        return self.rv_by_window.get(20)

    @property
    def rv_30d(self) -> Optional[float]:
        return self.rv_by_window.get(30)


@dataclass(frozen=True)
class VarianceRiskPremiumResult:
    """Variance risk premium (DVOL vs realized volatility)."""

    vrp: float  # points
    signal: str  # "VERY_EXPENSIVE" | "EXPENSIVE" | "FAIR" | "CHEAP" | "VERY_CHEAP"
    dvol: Optional[float]
    rv_30d: float


@dataclass(frozen=True)
class VolatilityConeWindowStats:
    """
    One window's full row in the legacy VOLATILITY CONE table
    (refactor_design_spec.md T10 -- added when wiring
    ``render_market_wide_from_result`` live surfaced that
    ``VolatilityConeResult`` carried only the percentile, not the
    current/25th/median/75th realized-vol figures the legacy text table
    shows in the same row).
    """

    current_rv: float
    p25: float
    p50: float
    p75: float
    percentile: float


@dataclass(frozen=True)
class VolatilityConeResult:
    """Percentile of current realized volatility vs its historical range, per window."""

    percentile_by_window: Dict[int, float]
    # Optional / additive: populated by the one production call site
    # (on_chain_analysis_service.py) so the formatter can render the full
    # 6-column legacy table; callers that only ever needed the percentile
    # (SynthesisMapper, existing tests) are unaffected by its absence.
    stats_by_window: Dict[int, VolatilityConeWindowStats] = field(default_factory=dict)

    # Task Wave-J-D Fix 1: same fabrication as RealizedVolatilityResult's
    # rv_Xd properties above -- a window absent from ``percentile_by_window``
    # means that window's cone percentile was never computed, not measured
    # as the 0th percentile. ``.get(window)`` (no default) preserves None.
    @property
    def cone_10d_pctile(self) -> Optional[float]:
        return self.percentile_by_window.get(10)

    @property
    def cone_20d_pctile(self) -> Optional[float]:
        return self.percentile_by_window.get(20)

    @property
    def cone_30d_pctile(self) -> Optional[float]:
        return self.percentile_by_window.get(30)


@dataclass(frozen=True)
class PerpetualFundingResult:
    """Perpetual funding rate and open interest, with trend."""

    perp_open_interest: float
    funding_rate: Optional[float]  # current funding, decimal
    funding_8h: Optional[float]  # decimal
    funding_trend: str  # "Rising" | "Falling" | "Stable"
    history_points: int


@dataclass(frozen=True)
class BlockTrade:
    """A single detected block (large-notional) trade."""

    timestamp: Optional[int]
    instrument_name: str
    amount: float
    direction: str
    notional: float
    implied_volatility: Optional[float]


@dataclass(frozen=True)
class Block:
    """
    One full block trade: every leg sharing a ``block_trade_id``, grouped
    into a single row (institutional_metrics_spec.md section 9 / Migration
    M2, Task D1) -- not one row per leg like the old notional-filter list.

    Not backfillable: only trades fetched after migration 022 (see
    ``BlockTradesResult.tracked_since``) can carry ``block_trade_id``.
    """

    block_trade_id: str
    leg_count: int  # API-declared block_trade_leg_count when present and
    # consistent across observed legs; falls back to observed_leg_count
    # when the field is null/missing (gate exhaustiveness: companion
    # fields are not guaranteed present on every leg).
    observed_leg_count: int  # legs actually present in the fetched trade
    # window -- may be less than leg_count if the window truncated the
    # group (e.g. legs straddling the pagination boundary).
    combo_id: Optional[str]  # combo structure name (e.g.
    # "BTC-STRD-31JUL26-63000") when present on any leg; None otherwise.
    combined_premium_usd: float  # sum(price * amount * index_price) over
    # observed legs -- premium in USD, NOT the same quantity as
    # BlockTrade.notional (amount * index_price, underlying notional).
    total_amount: float
    instruments: Tuple[str, ...]
    timestamp: Optional[int]  # earliest observed leg timestamp, ms epoch


@dataclass(frozen=True)
class BlockTradesResult:
    """
    Market-wide block-trade section (institutional_metrics_spec.md section
    9 / Migration M2, Task D1): ``blocks`` groups trades by
    ``block_trade_id`` (one row per block); ``trades`` is the pre-existing
    notional-filter list, relabelled "large prints" in the rendered report
    since it measures large single-leg screen prints, not true blocks. A
    trade belonging to a block is excluded from ``trades`` -- the two
    lists never double-count the same trade.
    """

    trades: Tuple[BlockTrade, ...]  # large prints (screen prints, not
    # blocks): top 10 by notional, EXCLUDING any trade with a
    # block_trade_id (already counted in `blocks`).
    notional_threshold: float
    total_detected: int
    blocks: Tuple[Block, ...] = ()
    # institutional_metrics_spec.md section 9(c): "the block section must
    # state its start date" -- history is not backfillable, so this is a
    # fixed string (BLOCK_TRADE_ID_TRACKED_SINCE), not derived per-render.
    tracked_since: str = ""


@dataclass(frozen=True)
class CrossAssetCorrelationResult:
    """Price/DVOL correlation between this currency and another."""

    other_currency: str
    price_correlation: Optional[float]
    dvol_correlation: Optional[float]
    sample_size: int
    # bugfix_spec.md Item 11: the DVOL correlation is computed on log
    # CHANGES, whose observation count (n) differs from `sample_size`
    # (a PRICE-history count) -- the report must say "log changes, Nd" so a
    # reader comparing against a previously-stored levels-based value knows
    # both why the number changed AND its own sample size. None when
    # dvol_correlation is None (N/A or Insufficient data).
    dvol_correlation_observations: Optional[int] = None


@dataclass(frozen=True)
class MarketWideResult:
    """Aggregate of every market-wide (cross-expiry) metric for one currency."""

    spot_price: float
    currency: str
    dvol: Optional[float]
    iv_percentile_365d: Optional[float]
    aggregate_gex_dex: Optional[GexDexResult]
    term_structure: Optional[TermStructureResult]
    futures_basis: Optional[FuturesBasisResult]
    realized_volatility: Optional[RealizedVolatilityResult]
    variance_risk_premium: Optional[VarianceRiskPremiumResult]
    volatility_cone: Optional[VolatilityConeResult]
    perpetual_funding: Optional[PerpetualFundingResult]
    block_trades: Optional[BlockTradesResult]
    cross_asset_correlation: Optional[CrossAssetCorrelationResult]
    failed_sections: Tuple[str, ...]  # M5: names of sections whose calculation raised
    # institutional_metrics_spec.md section 3(c) (Task C4). Additive, with
    # a default: MarketWideOrchestrator.run() does not populate this (it
    # has no repository/DB access) -- OnChainAnalysisService attaches it
    # via dataclasses.replace() after run() returns (see
    # _build_skew_term_structure), the same "post-process the frozen
    # result" pattern _apply_pcr_percentile_classification already uses.
    skew_term_structure: Optional[SkewTermStructureResult] = None

    # institutional_metrics_spec.md section 5 (Task C6). Additive, with a
    # default: MarketWideOrchestrator.run() does not populate this (it has
    # no access to the per-expiry total_net_gex map -- that only exists
    # inside OnChainAnalysisService._fetch_greeks_and_store_gex_dex, a
    # different phase) -- OnChainAnalysisService attaches it via
    # dataclasses.replace() after run() returns, the same "post-process the
    # frozen result" pattern skew_term_structure already uses.
    gamma_rolloff: Optional[GammaRolloffResult] = None

    # institutional_metrics_spec.md section 8 (Task C9). Additive, with a
    # default, same "post-process the frozen result via dataclasses.
    # replace()" pattern as skew_term_structure/gamma_rolloff above --
    # OnChainAnalysisService attaches it after MarketWideOrchestrator.run()
    # returns. Unlike skew_term_structure, this one needs no repository
    # (no percentile history lookups, pure live-chain calculation) -- it
    # is attached the same way purely to stay consistent with the
    # established pattern, not because it has the same DB dependency.
    forward_vol: Optional[ForwardVolResult] = None

    def to_flat_dict(self) -> Dict[str, Any]:
        """
        Reproduce the flattened dict ``SynthesisMapper.build_market_wide``
        reads today (``analyzer.market_wide_structured``). Keys for a
        sub-result that is ``None`` are omitted — downstream readers already
        use ``mw.get(key) or default`` (see ``synthesis.py``), so omission is
        equivalent to an absent/never-set key in the legacy dict.
        """
        flat: Dict[str, Any] = {
            "spot_price": self.spot_price,
            "dvol": self.dvol,
            "iv_percentile_365d": self.iv_percentile_365d,
        }

        ts = self.term_structure
        if ts is not None:
            flat["shape"] = ts.shape
            flat["spread"] = ts.spread
            flat["spread_signed"] = ts.spread_signed
            flat["iv_by_dte"] = dict(ts.iv_by_dte)

        fb = self.futures_basis
        if fb is not None:
            flat["futures_basis"] = dict(fb.futures_basis)

        rv = self.realized_volatility
        if rv is not None:
            # Task Wave-J-D Fix 1: rv_Xd is now Optional[float] -- None when
            # that specific window wasn't computed (not the whole section
            # missing, which is the `rv is not None` check above). A
            # present-but-None value is still consistent with this method's
            # own "omission is equivalent to an absent key" contract, since
            # every documented consumer already reads via `mw.get(key) or
            # default` (None is falsy) rather than assuming presence means
            # non-None.
            flat["rv_10d"] = rv.rv_10d
            flat["rv_20d"] = rv.rv_20d
            flat["rv_30d"] = rv.rv_30d

        vrp = self.variance_risk_premium
        if vrp is not None:
            flat["vrp"] = vrp.vrp
            flat["signal"] = vrp.signal

        cone = self.volatility_cone
        if cone is not None:
            # Same Optional[float]-per-window note as rv_Xd above.
            flat["cone_10d_pctile"] = cone.cone_10d_pctile
            flat["cone_20d_pctile"] = cone.cone_20d_pctile
            flat["cone_30d_pctile"] = cone.cone_30d_pctile

        pf = self.perpetual_funding
        if pf is not None:
            flat["perp_oi"] = pf.perp_open_interest
            flat["perp_funding_trend"] = pf.funding_trend
            if pf.funding_rate is not None:
                flat["funding_rate"] = pf.funding_rate
            if pf.funding_8h is not None:
                flat["funding_8h"] = pf.funding_8h

        bt = self.block_trades
        if bt is not None:
            # institutional_metrics_spec.md section 9 / Migration M2 (Task
            # D1 review round 2, Important #3): `large_prints` (the
            # pre-existing notional-filter list of large single-leg screen
            # prints) and `blocks` (real block_trade_id groups) are
            # exported under SEPARATE keys -- the old code exported the
            # large-prints list under the key "block_trades", which
            # conflated exactly the two things section 9 was written to
            # separate. Downstream: synthesis.py's SynthesisMapper reads
            # the typed model directly (not this flat dict), so this key
            # rename only affects to_flat_dict() consumers (currently the
            # characterization golden-master JSON snapshot).
            flat["large_prints"] = [
                {
                    "timestamp": t.timestamp,
                    "instrument": t.instrument_name,
                    "size": t.amount,
                    "amount": t.amount,
                    "direction": t.direction,
                    "notional": t.notional,
                    "iv": t.implied_volatility,
                }
                for t in bt.trades
            ]
            flat["blocks"] = [
                {
                    "block_trade_id": b.block_trade_id,
                    "leg_count": b.leg_count,
                    "observed_leg_count": b.observed_leg_count,
                    "combo_id": b.combo_id,
                    "combined_premium_usd": b.combined_premium_usd,
                    "total_amount": b.total_amount,
                    "instruments": b.instruments,
                    "timestamp": b.timestamp,
                }
                for b in bt.blocks
            ]

        corr = self.cross_asset_correlation
        if corr is not None:
            if corr.price_correlation is not None:
                flat["btc_eth_price_corr"] = corr.price_correlation
            if corr.dvol_correlation is not None:
                flat["btc_eth_dvol_corr"] = corr.dvol_correlation

        return flat
