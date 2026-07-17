"""
Long-straddle scanner service.

v1 "manual scan" slice: a single synchronous scan() call the GUI triggers on
demand. No persistence, no Telegram send yet — those are increment 2 and are
intentionally NOT wired here so they bolt on cleanly (scan() returns a plain
dict; a future recorder/notifier just consumes that dict, it never has to
change this service).

Design was validated over several days against live data plus a metrics
backtest (see scripts/backtest_straddle_metrics.py). That backtest found
iv_percentile_expiry (per-expiry ATM-IV percentile) was the strongest
entry-time "deal quality" signal for long-straddle P&L — this service uses it
as the primary cross-expiry ranking key.

THE ONE DATA SOURCE RULE: this service fetches market data exclusively via
DeribitApiService.get_option_chain_snapshot(currency). That method's
docstring documents the index-vs-future pricing rule (index price for USD
conversion, future price for strike-space/moneyness math) — read it before
touching any pricing math in this file. Do not add any other Deribit call.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.chart_generator import (
    generate_straddle_payoff_chart,
    inject_hover_js,
    save_chart,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class StraddleScanService:
    """
    Scans an option chain for long-straddle candidates and ranks expiries by
    the backtest-validated IV-percentile signal.

    Per-expiry algorithm (5 <= dte <= 400):
      1. F = futures_by_expiry[expiry] (future price — strike-space math).
      2. ATM strike = closest strike to F with both legs quoted. ATM IV =
         average of both legs' mark_iv (Deribit native units, percent).
      3. Expected 1-sigma range: sigma_sqrt_t = atm_iv/100 * sqrt(dte/365);
         lo = F / exp(sigma_sqrt_t); hi = F * exp(sigma_sqrt_t).
      4. Candidates = strikes inside [lo, hi] with both legs passing the
         liquidity gate (live bid+ask, per-leg spread <= MAX_SPREAD_PCT,
         per-leg OI >= MIN_OPEN_INTEREST).
      5. Best candidate per expiry = lowest max(required_move_up,
         required_move_down) — i.e. the strike needing the smallest move in
         either direction to break even.

    Cross-expiry ranking: ascending by iv_percentile (NULL sorts last) — the
    backtest-validated signal, low percentile = IV cheap relative to its own
    history = better long-vol entry.
    """

    # -- Universe / DTE window ------------------------------------------------
    MIN_DTE = 5
    MAX_DTE = 400

    # -- Liquidity gate ---------------------------------------------------------
    MAX_SPREAD_PCT = 0.15        # per-leg (ask - bid) / mid
    MIN_OPEN_INTEREST = 25       # per-leg

    # -- Realized-vol lookback --------------------------------------------------
    RV_MIN_WINDOW_DAYS = 21      # floor: max(21, round(dte))
    RV_FETCH_BUFFER_DAYS = 15    # extra calendar days fetched to absorb gaps

    # -- Alert formatting ---------------------------------------------------------
    STALENESS_WARNING_MINUTES = 5

    def __init__(
        self,
        api_service: Optional[DeribitApiService] = None,
        repository: Optional[DatabaseRepository] = None,
    ):
        """
        Args:
            api_service: Injected Deribit API service (tests / callers that
                already manage a connection). If None, scan() opens and
                closes its own DeribitApiService for the duration of the call.
            repository: Injected DatabaseRepository (tests). If None, scan()
                creates a default one for the entry-time metric lookups.
        """
        self.api_service = api_service
        self.repository = repository

    # ── Public API ────────────────────────────────────────────────────────────

    def scan(self, currency: str) -> Dict[str, Any]:
        """
        Run one straddle scan for a currency.

        Every expiry present in the fetched option chain ends up in exactly
        one of `expiries` (included) or `excluded` — nothing is silently
        dropped; see module docstring for per-field meaning of an included
        entry.

        Returns:
            Dict: {as_of, currency, index_price, expiries: [...],
            excluded: [...]}.
              expiries entry: {expiry, dte, F, atm_iv, iv_percentile, rv,
                rv_iv_ratio, vrp, best, candidates}. `candidates` is ranked
                best-first (ascending by max required move); `best` ==
                `candidates[0]`.
              excluded entry: {expiry, dte, reason} — dte may be None if the
                expiry couldn't even be parsed to a DTE (shouldn't happen
                given the snapshot contract, but guarded). reason is a short
                human-readable string, e.g. "DTE 2.1 below minimum 5" or
                "no strikes passed liquidity gate (checked 4)".
        """
        snapshot = self._fetch_snapshot(currency)
        repo = self.repository or DatabaseRepository()

        as_of = snapshot["as_of"]
        index_price = snapshot["index_price"]
        futures_by_expiry = snapshot["futures_by_expiry"]

        contracts_by_expiry: Dict[str, List[Dict[str, Any]]] = {}
        for contract in snapshot["contracts"]:
            contracts_by_expiry.setdefault(contract["expiry"], []).append(contract)

        expiry_entries: List[Dict[str, Any]] = []
        excluded: List[Dict[str, Any]] = []

        for expiry, expiry_contracts in contracts_by_expiry.items():
            dte = expiry_contracts[0]["dte"]

            if dte < self.MIN_DTE:
                excluded.append({
                    "expiry": expiry, "dte": dte,
                    "reason": f"DTE {dte:.1f} below minimum {self.MIN_DTE}",
                })
                continue
            if dte > self.MAX_DTE:
                excluded.append({
                    "expiry": expiry, "dte": dte,
                    "reason": f"DTE {dte:.1f} above maximum {self.MAX_DTE}",
                })
                continue

            future_price = futures_by_expiry.get(expiry)
            if not future_price:
                logger.warning(f"{currency} {expiry}: no future price available, skipping")
                excluded.append({
                    "expiry": expiry, "dte": dte,
                    "reason": "no future price available",
                })
                continue

            entry, exclusion_reason = self._build_expiry_entry(
                currency=currency,
                expiry=expiry,
                dte=dte,
                future_price=future_price,
                contracts=expiry_contracts,
                repo=repo,
                as_of=as_of,
            )
            if entry is not None:
                expiry_entries.append(entry)
            else:
                excluded.append({"expiry": expiry, "dte": dte, "reason": exclusion_reason})

        expiry_entries.sort(key=self._expiry_sort_key)
        excluded.sort(key=lambda x: x["expiry"])

        return {
            "as_of": as_of,
            "currency": currency,
            "index_price": index_price,
            "expiries": expiry_entries,
            "excluded": excluded,
        }

    def format_alert(self, scan_result: Dict[str, Any], top_n: int = 1) -> str:
        """
        Render a Telegram-ready plain-text alert for the top-ranked expiry
        (and a runner-up one-liner if a second exists). No HTML — this is
        the exact text a future Telegram sender (increment 2) would send;
        for now the GUI just displays it so the user can review it.

        Args:
            scan_result: Output of scan().
            top_n: Reserved for future multi-pick alerts; only the single
                best result is currently detailed (runner-up gets one line).

        Returns:
            Plain-text alert string. If scan_result has no expiries, returns
            a short "no candidates found" message.
        """
        currency = scan_result["currency"]
        as_of: datetime = scan_result["as_of"]
        index_price = scan_result["index_price"]
        expiries = scan_result["expiries"]

        lines: List[str] = [
            f"STRADDLE SCANNER — {currency}",
            f"as of {as_of.strftime('%Y-%m-%d %H:%M UTC')} | index ${index_price:,.2f}",
            "",
        ]

        if not expiries:
            lines.append("No qualifying straddle candidates found.")
            return "\n".join(lines)

        top = expiries[0]
        best = top["best"]

        lines.append(
            f"BEST: {top['expiry']} ({top['dte']:.0f}d) — Straddle at {best['strike']:.0f}"
        )
        cost_pct = (best["cost_usd"] / top["F"] * 100) if top["F"] else 0.0
        lines.append(f"  Cost: ${best['cost_usd']:,.2f} ({cost_pct:.1f}% of F)")
        lines.append(
            f"  Breakevens: {best['breakeven_down']:,.0f} / {best['breakeven_up']:,.0f}"
        )
        lines.append("")
        lines.append("Why it ranks:")

        iv_pct = top["iv_percentile"]
        iv_pct_str = f"{iv_pct:.1f}%" if iv_pct is not None else "N/A"
        lines.append(f"  [TRIGGER] IV percentile (expiry): {iv_pct_str}")

        if top["rv_iv_ratio"] is not None and top["rv"] is not None:
            lines.append(
                f"  [CONFIRM] RV/IV: {top['rv_iv_ratio']:.2f} "
                f"(RV {top['rv']:.1f}% vs IV {top['atm_iv']:.1f}%)"
            )
        else:
            lines.append("  [CONFIRM] RV/IV: N/A (insufficient OHLCV history)")

        vrp_str = f"{top['vrp']:+.1f}" if top["vrp"] is not None else "N/A"
        score_str = f"${best['min_pnl_score']:,.2f}" if best.get("min_pnl_score") is not None else "N/A"
        lines.append(f"  [CONTEXT] VRP: {vrp_str}  |  min P&L score: {score_str}")

        if len(expiries) > 1:
            runner_up = expiries[1]
            ru_iv = runner_up["iv_percentile"]
            ru_iv_str = f"{ru_iv:.1f}%" if ru_iv is not None else "N/A"
            lines.append("")
            lines.append(
                f"Runner-up: {runner_up['expiry']} ({runner_up['dte']:.0f}d) — IV %ile {ru_iv_str}"
            )

        lines.append("")
        age_minutes = (datetime.now(timezone.utc) - as_of).total_seconds() / 60.0
        if age_minutes > self.STALENESS_WARNING_MINUTES:
            lines.append(f"WARNING: data is {age_minutes:.0f} minutes old — reverify before trading.")
        else:
            lines.append(f"Data is fresh ({age_minutes:.1f} min old).")

        lines.append("")
        lines.append(f"Chart: {best['deribit_url']}")

        return "\n".join(lines)

    def generate_payoff_chart(self, scan_result: Dict[str, Any], expiry: str, strike: float) -> str:
        """
        Build and save a long-straddle payoff-at-expiry chart for one
        candidate from an existing scan() result — no new API/DB fetch, this
        purely re-uses data already computed for `scan_result`.

        Args:
            scan_result: Output of scan() (must still contain `expiry` with
                a matching `strike` among its candidates).
            expiry: Expiry label to chart, e.g. "25SEP26".
            strike: Candidate strike to chart (must be one of that expiry's
                candidates — best or a runner-up).

        Returns:
            Path to the saved chart HTML file (output/charts/straddle/...).

        Raises:
            ValueError: If the expiry or strike isn't present in
                scan_result — the GUI should only ever pass values it just
                rendered from this same scan_result, so this indicates a
                caller bug, not a data problem.
        """
        entry = next((e for e in scan_result["expiries"] if e["expiry"] == expiry), None)
        if entry is None:
            raise ValueError(f"Expiry {expiry} not found in scan result")

        candidate = next((c for c in entry["candidates"] if c["strike"] == strike), None)
        if candidate is None:
            raise ValueError(f"Strike {strike} not found among {expiry} candidates")

        fig = generate_straddle_payoff_chart(
            currency=scan_result["currency"],
            expiry=expiry,
            dte=entry["dte"],
            future_price=entry["F"],
            atm_iv=entry["atm_iv"],
            strike=candidate["strike"],
            cost_usd=candidate["cost_usd"],
            breakeven_down=candidate["breakeven_down"],
            breakeven_up=candidate["breakeven_up"],
            rv=entry["rv"],
            iv_percentile=entry.get("iv_percentile"),
            vrp=entry.get("vrp"),
        )

        filename = f"straddle_{scan_result['currency']}_{expiry}_{int(strike)}"
        path = save_chart(fig, filename, subfolder="straddle", save_png=False)
        inject_hover_js(Path(path))
        return path

    # ── Internals: fetch ────────────────────────────────────────────────────

    def _fetch_snapshot(self, currency: str) -> Dict[str, Any]:
        """Fetch the one authoritative option-chain snapshot for this scan."""
        if self.api_service is not None:
            return self.api_service.get_option_chain_snapshot(currency)
        with DeribitApiService() as api:
            return api.get_option_chain_snapshot(currency)

    # ── Internals: per-expiry construction ──────────────────────────────────

    def _build_expiry_entry(
        self,
        currency: str,
        expiry: str,
        dte: float,
        future_price: float,
        contracts: List[Dict[str, Any]],
        repo: DatabaseRepository,
        as_of: datetime,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Build one expiry's scan entry.

        Returns:
            (entry, None) on success, or (None, reason) if this expiry
            yields no usable candidate — `reason` is a short human-readable
            string surfaced to the caller's `excluded` list.
        """
        both_legs = self._pair_legs_by_strike(contracts)
        if not both_legs:
            reason = "no strike has both legs quoted"
            logger.info(f"{currency} {expiry}: {reason}, skipping")
            return None, reason

        atm_strike = min(both_legs, key=lambda k: abs(k - future_price))
        atm_iv = self._average_mark_iv(both_legs[atm_strike])
        if atm_iv is None:
            reason = f"ATM strike {atm_strike:.0f} missing mark_iv"
            logger.info(f"{currency} {expiry}: {reason}, skipping")
            return None, reason

        time_to_expiry_years = dte / 365.0
        sigma_sqrt_t = (atm_iv / 100.0) * math.sqrt(time_to_expiry_years)
        range_lo = future_price / math.exp(sigma_sqrt_t)
        range_hi = future_price * math.exp(sigma_sqrt_t)

        strikes_checked = sum(1 for k in both_legs if range_lo <= k <= range_hi)

        candidates = self._build_candidates(
            both_legs, range_lo, range_hi, future_price, currency, expiry
        )
        if not candidates:
            reason = f"no strikes passed liquidity gate (checked {strikes_checked})"
            logger.info(
                f"{currency} {expiry}: {reason} "
                f"within the expected range [{range_lo:.2f}, {range_hi:.2f}]"
            )
            return None, reason

        rv = self._compute_realized_vol(repo, currency, dte, as_of)
        for candidate in candidates:
            candidate["min_pnl_score"] = (
                self._min_pnl_score(future_price, rv, time_to_expiry_years,
                                     candidate["strike"], candidate["cost_usd"])
                if rv is not None else None
            )

        # Ranked best-first: lowest max(required_move_up, required_move_down)
        # i.e. the strike needing the smallest move in either direction to
        # break even. candidates[0] == best; candidates[1]/[2] feed the
        # GUI's tree "rank 2 / rank 3" child rows.
        candidates.sort(key=lambda c: max(c["required_move_up_pct"], c["required_move_down_pct"]))
        best = candidates[0]

        iv_percentile = repo.get_latest_iv_percentile_expiry(currency, expiry)
        rv_iv_ratio = (rv / atm_iv) if (rv is not None and atm_iv) else None
        vrp = (atm_iv - rv) if rv is not None else None

        entry = {
            "expiry": expiry,
            "dte": dte,
            "F": future_price,
            "atm_iv": atm_iv,
            "iv_percentile": iv_percentile,
            "rv": rv,
            "rv_iv_ratio": rv_iv_ratio,
            "vrp": vrp,
            "best": best,
            "candidates": candidates,
        }
        return entry, None

    @staticmethod
    def _pair_legs_by_strike(
        contracts: List[Dict[str, Any]]
    ) -> Dict[float, Dict[str, Dict[str, Any]]]:
        """Group contracts by strike, keeping only strikes with both C and P legs."""
        by_strike: Dict[float, Dict[str, Dict[str, Any]]] = {}
        for contract in contracts:
            by_strike.setdefault(contract["strike"], {})[contract["option_type"]] = contract
        return {k: v for k, v in by_strike.items() if "C" in v and "P" in v}

    @staticmethod
    def _average_mark_iv(legs: Dict[str, Dict[str, Any]]) -> Optional[float]:
        """Average of both legs' mark_iv (Deribit native percent units)."""
        ivs = [legs[side].get("mark_iv") for side in ("C", "P")]
        ivs = [iv for iv in ivs if iv is not None]
        if not ivs:
            return None
        return sum(ivs) / len(ivs)

    def _build_candidates(
        self,
        both_legs: Dict[float, Dict[str, Dict[str, Any]]],
        range_lo: float,
        range_hi: float,
        future_price: float,
        currency: str,
        expiry: str,
    ) -> List[Dict[str, Any]]:
        """Liquidity-gated candidate strikes inside the expected range, priced."""
        candidates: List[Dict[str, Any]] = []
        for strike, legs in both_legs.items():
            if not (range_lo <= strike <= range_hi):
                continue
            call, put = legs["C"], legs["P"]
            if not self._passes_liquidity_gate(call) or not self._passes_liquidity_gate(put):
                continue

            call_ask_usd = call.get("ask_usd")
            put_ask_usd = put.get("ask_usd")
            if call_ask_usd is None or put_ask_usd is None:
                continue
            cost_usd = call_ask_usd + put_ask_usd
            if cost_usd <= 0:
                continue

            breakeven_down = strike - cost_usd
            breakeven_up = strike + cost_usd
            required_move_down_pct = (future_price - breakeven_down) / future_price * 100.0
            required_move_up_pct = (breakeven_up - future_price) / future_price * 100.0

            candidates.append({
                "strike": strike,
                "cost_usd": cost_usd,
                "breakeven_down": breakeven_down,
                "breakeven_up": breakeven_up,
                "required_move_down_pct": required_move_down_pct,
                "required_move_up_pct": required_move_up_pct,
                "call_ask_btc": call.get("ask_price"),
                "call_ask_usd": call_ask_usd,
                "put_ask_btc": put.get("ask_price"),
                "put_ask_usd": put_ask_usd,
                "deribit_url": self._deribit_url(currency, expiry, strike),
            })
        return candidates

    def _passes_liquidity_gate(self, leg: Dict[str, Any]) -> bool:
        """Both sides quoted, spread within MAX_SPREAD_PCT, OI >= MIN_OPEN_INTEREST."""
        bid = leg.get("bid_price")
        ask = leg.get("ask_price")
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return False
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return False
        spread_pct = (ask - bid) / mid
        if spread_pct > self.MAX_SPREAD_PCT:
            return False
        open_interest = leg.get("open_interest") or 0
        if open_interest < self.MIN_OPEN_INTEREST:
            return False
        return True

    # ── Internals: entry-time metrics ───────────────────────────────────────

    def _compute_realized_vol(
        self,
        repo: DatabaseRepository,
        currency: str,
        dte: float,
        as_of: datetime,
    ) -> Optional[float]:
        """
        Annualized realized vol (percent units, matching atm_iv) from daily
        OHLCV closes: stdev(log returns) * sqrt(365) * 100, over
        max(RV_MIN_WINDOW_DAYS, round(dte)) trailing closes.
        """
        window_days = max(self.RV_MIN_WINDOW_DAYS, round(dte))
        end = as_of.replace(tzinfo=None)
        start = end - timedelta(days=window_days + self.RV_FETCH_BUFFER_DAYS)

        rows = repo.get_ohlcv_by_date_range(currency, start, end)
        if len(rows) < window_days + 1:
            logger.warning(
                f"{currency}: insufficient OHLCV history for RV window {window_days}d "
                f"(have {len(rows)} rows, need {window_days + 1}) — RV unavailable"
            )
            return None

        closes = [row["close"] for row in rows[-(window_days + 1):]]
        log_returns = [
            math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0
        ]
        n = len(log_returns)
        if n < 2:
            return None

        mean = sum(log_returns) / n
        variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
        stdev = math.sqrt(variance)
        return stdev * math.sqrt(365.0) * 100.0

    @staticmethod
    def _min_pnl_score(
        future_price: float,
        rv_pct: float,
        time_to_expiry_years: float,
        strike: float,
        cost_usd: float,
    ) -> float:
        """
        Min P&L at the RV-implied range edges: rv_hi/rv_lo = F*exp(+-rv/100 *
        sqrt(T)); score = min(|rv_hi - K|, |rv_lo - K|) - cost. Positive means
        the straddle would still be profitable even at the *tighter* of the
        two RV-implied edges — a conservative worst-of-two-tails check.
        """
        sigma_sqrt_t = (rv_pct / 100.0) * math.sqrt(time_to_expiry_years)
        rv_hi = future_price * math.exp(sigma_sqrt_t)
        rv_lo = future_price * math.exp(-sigma_sqrt_t)
        edge_distance = min(abs(rv_hi - strike), abs(rv_lo - strike))
        return edge_distance - cost_usd

    @staticmethod
    def _expiry_sort_key(entry: Dict[str, Any]):
        """Ascending by iv_percentile; NULL sorts last."""
        iv_percentile = entry["iv_percentile"]
        return (iv_percentile is None, iv_percentile if iv_percentile is not None else 0.0)

    @staticmethod
    def _deribit_url(currency: str, expiry: str, strike: float) -> str:
        return f"https://www.deribit.com/options/{currency}/{currency}-{expiry}-{int(strike)}-C"
