"""
Automation tab.

Manual trigger point for scanner jobs. The job dropdown picks one of three
manual scans — "Straddle Scanner (manual)", "Iron Condor Scanner (manual)",
or "Long Butterfly Scanner (manual)" — each running its own scan service in
a background thread and rendering results into the same results tree and
alert-preview widgets, with the tree's columns and row-builder swapped per
job in `_run_job`. Straddle shows the ranked results with top-3 candidates
per expiry (excluded expiries shown dimmed); iron condor/butterfly show the
best candidate per expiry only. No scheduling, no DB recording, no Telegram
send yet (increment 2) — this tab only triggers a single scan and displays
its output.

All business logic lives in the scan services (StraddleScanService,
IronCondorScanService, ButterflyScanService); this tab only orchestrates
worker threads and renders the returned dicts. No scan math and no chart
figure-building here — payoff charts are built by each service's own
generate_payoff_chart(), which delegates to
coding.core.analytics.chart_generator.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFrame, QSizePolicy, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QTextEdit, QGroupBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QColor, QDesktopServices

from coding.gui.theme.colors import Colors
from coding.service.scanner.straddle_scan_service import StraddleScanService
from coding.service.scanner.butterfly_scan_service import ButterflyScanService
from coding.service.scanner.iron_condor_scan_service import IronCondorScanService
from coding.service.scanner.defined_risk_alert_rules import format_defined_risk_alert

logger = logging.getLogger(__name__)

# Job registry: dropdown label -> one of three jobs (straddle, iron condor,
# long butterfly). Each shares the single results-tree/alert-preview widget
# pair, swapping columns and row-builder per job in _run_job.
_JOBS = [
    "Straddle Scanner (manual)",
    "Iron Condor Scanner (manual)",
    "Long Butterfly Scanner (manual)",
]

# Tree column layout — parent (best candidate) and child (rank 2/3) rows
# share this exact layout.
_COLUMNS = [
    "Expiry", "DTE", "Strike", "Call $", "Put $", "Cost $", "Breakevens",
    "IV %ile", "RV/IV", "VRP", "Score $", "Payoff", "Deribit",
]
(
    _COL_EXPIRY, _COL_DTE, _COL_STRIKE, _COL_CALL, _COL_PUT, _COL_COST,
    _COL_BE, _COL_IV, _COL_RVIV, _COL_VRP, _COL_SCORE, _COL_PAYOFF,
    _COL_DERIBIT,
) = range(len(_COLUMNS))

# Iron condor column layout — same single-tree widget as the straddle job,
# swapped in by _run_job when "Iron Condor Scanner (manual)" is selected.
_IC_COLUMNS = [
    "Expiry", "DTE", "Short C", "Long C", "Short P", "Long P", "Credit $",
    "Max Loss $", "Breakevens", "Prob %", "EV $", "Gate", "Payoff", "Deribit",
]
(
    _IC_COL_EXPIRY, _IC_COL_DTE, _IC_COL_SHORTC, _IC_COL_LONGC, _IC_COL_SHORTP,
    _IC_COL_LONGP, _IC_COL_CREDIT, _IC_COL_MAXLOSS, _IC_COL_BE, _IC_COL_PROB,
    _IC_COL_EV, _IC_COL_GATE, _IC_COL_PAYOFF, _IC_COL_DERIBIT,
) = range(len(_IC_COLUMNS))

# Long butterfly column layout — same single-tree widget, swapped in by
# _run_job when "Long Butterfly Scanner (manual)" is selected.
_BF_COLUMNS = [
    "Expiry", "DTE", "K1", "K2 (mid)", "K3", "Cost $", "Max Profit $",
    "Breakevens", "Prob %", "EV $", "Gate", "Payoff", "Deribit",
]
(
    _BF_COL_EXPIRY, _BF_COL_DTE, _BF_COL_K1, _BF_COL_K2, _BF_COL_K3,
    _BF_COL_COST, _BF_COL_MAXPROFIT, _BF_COL_BE, _BF_COL_PROB, _BF_COL_EV,
    _BF_COL_GATE, _BF_COL_PAYOFF, _BF_COL_DERIBIT,
) = range(len(_BF_COLUMNS))

# Rich, multi-line header tooltips: (a) what it is, (b) exact formula,
# (c) a worked example, (d) how to interpret it. Kept as a module-level
# dict for maintainability — one place to update wording.
_COLUMN_TOOLTIPS: Dict[str, str] = {
    "Expiry": (
        "Expiry\n\n"
        "The option contract's settlement date (Deribit format, e.g. "
        "25SEP26). All contracts in a row settle at 08:00 UTC on this date.\n\n"
        "Used to group and rank straddle candidates — one row per expiry, "
        "ordered by IV percentile ascending (cheapest vol first)."
    ),
    "DTE": (
        "DTE — Days To Expiry\n\n"
        "Formula: (expiry_datetime - as_of).total_seconds() / 86400\n\n"
        "Example: scan run 2026-07-17, expiry 25SEP26 08:00 UTC -> "
        "DTE ~= 68.3 days (shown rounded, e.g. \"68\").\n\n"
        "Interpretation: shorter DTE = faster theta decay and a tighter "
        "expected range for the same IV; longer DTE = more time for the "
        "move to happen but a wider expected range needed to break even."
    ),
    "Strike": (
        "Strike\n\n"
        "The straddle's single strike price — the same strike is used for "
        "both the long call and long put leg.\n\n"
        "Selected from strikes inside the expiry's IV-implied 1-sigma "
        "expected range [F/exp(sigma*sqrt(T)), F*exp(sigma*sqrt(T))] that "
        "pass the liquidity gate (bid+ask quoted, spread <= 15%, OI >= 25 "
        "on both legs).\n\n"
        "The parent row shows the BEST strike for that expiry (lowest max "
        "required move); expand the row to see ranks 2 and 3."
    ),
    "Call $": (
        "Call $ — call leg ask price in USD\n\n"
        "Formula: call_ask_price (BTC/ETH) x index_price\n\n"
        "Example: ask 0.0685 BTC x index $63,978 = $4,383.\n\n"
        "This is what you pay in USD to buy the call leg at its current "
        "ask — you pay the ASK when buying, never the mid or last-trade "
        "price."
    ),
    "Put $": (
        "Put $ — put leg ask price in USD\n\n"
        "Formula: put_ask_price (BTC/ETH) x index_price\n\n"
        "Example: ask 0.0332 BTC x index $63,978 = $2,124.\n\n"
        "This is what you pay in USD to buy the put leg at its current "
        "ask — you pay the ASK when buying, never the mid or last-trade "
        "price."
    ),
    "Cost $": (
        "Cost $ (with % of F)\n\n"
        "Formula: cost_usd = call_ask_usd + put_ask_usd; "
        "% = cost_usd / F x 100\n\n"
        "Example: $4,383 (call) + $2,124 (put) = $6,507 total; if F = "
        "$67,700, that's 9.6% of F — shown as \"6,507 (9.6%)\".\n\n"
        "The % figure is comparable across expiries AND currencies (BTC "
        "vs ETH) since it's normalized by the future price — it's "
        "roughly the move needed to break even."
    ),
    "Breakevens": (
        "Breakevens\n\n"
        "Formula: breakeven_down = strike - cost_usd; "
        "breakeven_up = strike + cost_usd\n\n"
        "Example: strike $65,000, cost $6,507 -> breakevens "
        "$58,493 / $71,507.\n\n"
        "The long straddle only profits at expiry if the underlying "
        "settles OUTSIDE this range (below breakeven_down or above "
        "breakeven_up). Inside the range the position loses money, "
        "maxing out the loss exactly at the strike."
    ),
    "IV %ile": (
        "IV %ile — ATM IV percentile vs this expiry's own history\n\n"
        "Formula: percentile rank of the current ATM IV within this "
        "expiry's own valid (non-zero, non-NULL) ATM IV observation "
        "history — zero/missing-data rows are excluded, they are never "
        "treated as \"cheap\" IV.\n\n"
        "Example: current ATM IV = 58%; over its available history this "
        "expiry's ATM IV mostly ranged 55-95% -> today ranks at the 8th "
        "percentile -> \"8.2% (n=1,405, 112d)\".\n\n"
        "The \"(n=..., ...d)\" suffix shows exactly how much history backs "
        "the number — n = valid observation count, d = days spanned. A "
        "young expiry (e.g. a few months since listing) will show a small "
        "window; ALWAYS check this before treating the percentile as a "
        "full-year rank.\n\n"
        "Interpretation: LOW percentile = historically CHEAP volatility = "
        "the primary buy signal. This is THE ranking metric for this "
        "scanner (expiries are sorted ascending by this column) — "
        "empirically validated in a backtest where the cheapest "
        "IV-percentile quintile averaged +30% straddle return vs -16% "
        "for the richest quintile."
    ),
    "RV/IV": (
        "RV/IV — realized vol / implied vol\n\n"
        "Formula: RV = stdev(daily log returns) x sqrt(365) x 100, over a "
        "DTE-matched trailing window (max(21, DTE) days), divided by ATM "
        "IV (both in percent).\n\n"
        "Example: RV 68% / IV 58% = 1.17.\n\n"
        "Interpretation: RV/IV > 1 means the underlying has recently been "
        "moving MORE than the options are pricing in — favorable for "
        "straddle buyers. This is a CONFIRMING signal (used alongside IV "
        "%ile, not instead of it)."
    ),
    "VRP": (
        "VRP — Variance Risk Premium\n\n"
        "Formula: ATM IV - RV, in percentage points\n\n"
        "Example: IV 58% - RV 68% = -10.0; or IV 58% - RV 40% = +18.0.\n\n"
        "Interpretation: positive VRP = options are priced ABOVE recent "
        "realized movement (vol is 'expensive' relative to what actually "
        "happened). Shown as context only — it is not a buy/sell trigger "
        "by itself."
    ),
    "Score $": (
        "Score $ — worst-case P&L at the realized-pace range edges\n\n"
        "Formula: RV-implied range = F*exp(+-RV/100*sqrt(T)); "
        "score = min(|rv_hi - strike|, |rv_lo - strike|) - cost_usd\n\n"
        "Example: F=$67,700, RV=40%, T=68/365 -> rv_hi~=$77,800, "
        "rv_lo~=$58,900; strike $65,000, cost $6,507 -> "
        "min(12,800, 6,100) - 6,507 = -407.\n\n"
        "CAUTION: the backtest found this metric ANTI-PREDICTIVE of "
        "actual straddle P&L — shown as context/diagnostic only, NEVER "
        "rank or select candidates by this column."
    ),
    "Payoff": (
        "Payoff\n\n"
        "Opens a chart of this straddle's P&L at expiry (|S-K| - cost) "
        "across a range of underlying prices, with the strike, current F, "
        "both breakevens, and the IV-implied vs realized-pace expected "
        "ranges marked.\n\n"
        "Click \"View chart\" to generate and open it — runs in the "
        "background, opens automatically when ready."
    ),
    "Deribit": (
        "Deribit\n\n"
        "Opens this strike's option chain page on deribit.com in your "
        "browser (call side shown; the put is the paired contract at the "
        "same strike/expiry).\n\n"
        "Use this to check the live order book before placing a trade — "
        "scanner data can be a few minutes old."
    ),
    "Gate": (
        "Gate — regime filter, recorded as data (NOT a live filter)\n\n"
        "Formula: gate_pass = (net_gex > 0) AND (RV10/RV30 < 1) — positive "
        "market-wide gamma exposure (dealers likely dampening moves) AND "
        "contracting realized vol (market calming down).\n\n"
        "Example: net_gex=+$175M, RV10/RV30=1.12 (expanding) -> "
        "gate_pass=False, even though net_gex alone was positive.\n\n"
        "Every candidate is scanned and alerted on regardless of this "
        "value — an earlier backtest of this exact formula on 16 "
        "historical trades came out backwards (gate-passing trades did "
        "worse), so it's deliberately recorded unfiltered here. The idea "
        "is to let a much larger live forward-test sample judge whether "
        "it's actually predictive, rather than trust or discard it on a "
        "small backtest."
    ),
}

# Iron-condor-specific tooltip overrides — merged on top of _COLUMN_TOOLTIPS
# by _apply_header_tooltips. "Breakevens" must be overridden here: the base
# dict's entry states the straddle's formula AND profit direction, both of
# which are wrong for a credit structure (profit is INSIDE these breakevens,
# not outside).
_IC_COLUMN_TOOLTIPS: Dict[str, str] = {
    "Short C": (
        "Short C — the call strike you SELL (nearer the money)\n\n"
        "Formula: nearest listed strike to F x (1 + short_distance%), "
        "tried across a grid of short distances (6%, 8%, 10%, 12%, 15% "
        "OTM). Paired with Long C as a credit call spread.\n\n"
        "Example: F=$66,500, 8% OTM target -> $71,820 -> nearest listed "
        "strike $71,000 sold.\n\n"
        "This leg collects most of the credit; risk above it is capped "
        "by the Long C strike."
    ),
    "Long C": (
        "Long C — the call strike you BUY (further OTM, caps the risk)\n\n"
        "Formula: nearest listed strike above Short C at "
        "Short C + F x wing_width% (tried across 2%, 3%, 5% widths).\n\n"
        "Example: Short C $77,000, wing 3% of F=$66,500 (~$2,000) -> "
        "Long C $80,000.\n\n"
        "The gap between Short C and Long C, minus the credit collected, "
        "IS the max loss on the call side — see Max Loss $."
    ),
    "Short P": (
        "Short P — the put strike you SELL (nearer the money)\n\n"
        "Formula: nearest listed strike to F x (1 - short_distance%), "
        "same short-distance grid as Short C, mirrored on the put side.\n\n"
        "Example: F=$66,500, 8% OTM -> $61,180 -> nearest listed strike "
        "$57,000 sold (grid-dependent — actual strike depends on which "
        "distance/wing combination this candidate came from).\n\n"
        "Paired with Long P as a credit put spread; collects premium "
        "symmetric to the call side."
    ),
    "Long P": (
        "Long P — the put strike you BUY (further OTM, caps the risk)\n\n"
        "Formula: nearest listed strike below Short P at "
        "Short P - F x wing_width%.\n\n"
        "Example: Short P $57,000, wing ~$3,000 -> Long P $54,000.\n\n"
        "Same role as Long C on the other side: caps max loss on a down "
        "move."
    ),
    "Credit $": (
        "Credit $ — net premium received for the whole 4-leg structure\n\n"
        "Formula: (Short C bid - Long C ask) + (Short P bid - Long P ask), "
        "in USD.\n\n"
        "Example: call spread nets $180, put spread nets $151 -> "
        "Credit $331.\n\n"
        "This is what you receive today. It's also the MAX PROFIT — if "
        "price settles between the short strikes at expiry, you keep the "
        "whole credit."
    ),
    "Max Loss $": (
        "Max Loss $ — worst case if price blows through a wing\n\n"
        "Formula: max(Long C - Short C, Short P - Long P) - Credit — the "
        "wider of the two spread widths, minus the credit already "
        "collected.\n\n"
        "Example: both wings $3,000 wide, credit $331 -> "
        "Max Loss = $3,000 - $331 = $2,669.\n\n"
        "This is the correct divisor for return-on-capital, NOT the "
        "credit — an iron condor is a CREDIT structure, so "
        "%return = P&L / Max Loss."
    ),
    "Breakevens": (
        "Breakevens\n\n"
        "Formula: breakeven_hi = Short C + Credit; "
        "breakeven_lo = Short P - Credit\n\n"
        "Example: Short C $77,000 + credit $331 -> breakeven_hi $77,331; "
        "Short P $57,000 - credit $331 -> breakeven_lo $56,669.\n\n"
        "Unlike a straddle, this position PROFITS INSIDE this range "
        "(between breakeven_lo and breakeven_hi) and loses money OUTSIDE "
        "it — opposite direction from the straddle's Breakevens column. "
        "Max profit (the full credit) happens anywhere between the two "
        "short strikes."
    ),
    "Prob %": (
        "Prob % — probability of profit (RV-implied)\n\n"
        "Formula: P(price settles between breakeven_lo and breakeven_hi "
        "at expiry), from a lognormal distribution centered on F, using "
        "REALIZED vol (not implied) over a DTE-matched trailing window "
        "as the sigma input.\n\n"
        "Example: breakevens $56,669/$77,331, F=$66,500, DTE=37 -> ~91% "
        "probability of landing inside.\n\n"
        "A high probability is inherent to this structure (the trade-off "
        "for a small, capped credit) — always read alongside EV $, not "
        "by itself; a 90%+ win rate with a negative EV is still a bad "
        "trade."
    ),
    "EV $": (
        "EV $ — expected value, RV-implied\n\n"
        "Formula: Prob% x Credit - (1 - Prob%) x Max Loss\n\n"
        "Example: Prob 91%, Credit $331, Max Loss $2,669 -> "
        "0.91 x 331 - 0.09 x 2,669 ~= $61.\n\n"
        "This is THE ranking metric — candidates and expiries are sorted "
        "descending by this column. Positive EV is also required before "
        "an alert can fire."
    ),
}

# Butterfly-specific tooltip overrides — same merge pattern as
# _IC_COLUMN_TOOLTIPS. "Breakevens" is overridden for the same reason.
_BF_COLUMN_TOOLTIPS: Dict[str, str] = {
    "K1": (
        "K1 — the lower wing strike (long call, BUY)\n\n"
        "Formula: nearest listed strike below K2 at K2 - F x width% "
        "(tried across 2%, 3%, 5%, 8% widths).\n\n"
        "Example: K2 $65,000, width 3% of F=$66,500 (~$2,000) -> "
        "K1 $63,000.\n\n"
        "Bought as part of the butterfly's protective wing; loses its "
        "full cost if price settles at or below here."
    ),
    "K2 (mid)": (
        "K2 (mid) — the body strike, SOLD TWICE\n\n"
        "Formula: candidate strikes within ±3% of F are tried as the "
        "body; two contracts at this strike are sold.\n\n"
        "Example: F=$66,500 -> K2 candidates near $65,000-$68,000 are "
        "tried.\n\n"
        "Max profit happens exactly at this strike at expiry — the "
        "classic butterfly \"peak\"."
    ),
    "K3": (
        "K3 — the upper wing strike (long call, BUY)\n\n"
        "Formula: nearest listed strike above K2 at K2 + F x width%, same "
        "width grid as K1 (kept roughly symmetric — the K1/K2 and K2/K3 "
        "widths must be within 20% of each other or the candidate is "
        "rejected).\n\n"
        "Example: K2 $65,000, width ~$2,000 -> K3 $67,000.\n\n"
        "Bought as the upper protective wing; loses its full cost if "
        "price settles at or above here."
    ),
    "Cost $": (
        "Cost $ — net debit paid for the whole structure\n\n"
        "Formula: Call ask at K1 + Call ask at K3 - 2 x Call bid at K2\n\n"
        "Example: K1 ask $2,700, K3 ask $2,100, K2 bid (x2) $4,400 -> "
        "cost = 2,700 + 2,100 - 4,400 = $400.\n\n"
        "This is what you pay today. It's also the MAX LOSS — a "
        "butterfly is a DEBIT structure, so %return = P&L / Cost, not "
        "credit-based like the iron condor."
    ),
    "Max Profit $": (
        "Max Profit $ — best case, if price settles exactly at K2\n\n"
        "Formula: min(K2-K1, K3-K2) - Cost — the narrower of the two "
        "wing widths, minus the debit paid.\n\n"
        "Example: K2-K1=$2,000, K3-K2=$2,000, cost $400 -> "
        "max profit = $2,000 - $400 = $1,600.\n\n"
        "Only achieved exactly at K2 at expiry; P&L tapers off linearly "
        "toward either breakeven."
    ),
    "Breakevens": (
        "Breakevens\n\n"
        "Formula: breakeven_lo = K1 + Cost; breakeven_hi = K3 - Cost\n\n"
        "Example: K1 $63,000 + cost $400 -> breakeven_lo $63,400; "
        "K3 $67,000 - cost $400 -> breakeven_hi $66,600.\n\n"
        "Same direction as the iron condor: this position PROFITS INSIDE "
        "this range (between breakeven_lo and breakeven_hi) and loses "
        "the full debit OUTSIDE it — opposite of the straddle's "
        "Breakevens column."
    ),
    "Prob %": (
        "Prob % — probability of profit (RV-implied)\n\n"
        "Formula: P(price settles between breakeven_lo and breakeven_hi "
        "at expiry), lognormal centered on F, sigma from REALIZED vol "
        "over a DTE-matched window (not implied vol).\n\n"
        "Example: breakevens $63,400/$66,600 (a narrow band around F) -> "
        "a much lower probability than the iron condor's wide band, "
        "e.g. ~40%.\n\n"
        "A butterfly trades a lower win probability for a much bigger "
        "payoff ratio than an iron condor — always read alongside EV $, "
        "not alone."
    ),
    "EV $": (
        "EV $ — expected value, RV-implied\n\n"
        "Formula: Prob% x Max Profit - (1 - Prob%) x Cost\n\n"
        "Example: Prob 40%, Max Profit $1,600, Cost $400 -> "
        "0.40 x 1,600 - 0.60 x 400 = $400.\n\n"
        "This is THE ranking metric — candidates and expiries are sorted "
        "descending by this column. Positive EV is also required before "
        "an alert can fire."
    ),
}


class StraddleScanWorker(QThread):
    """Runs StraddleScanService.scan() off the GUI thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = StraddleScanService()
            result = service.scan(self.currency)
            self.finished.emit(result)
        except Exception as error:
            logger.exception(f"Straddle scan failed for {self.currency}")
            self.error.emit(str(error))


class PayoffChartWorker(QThread):
    """Runs StraddleScanService.generate_payoff_chart() off the GUI thread."""

    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        scan_service: StraddleScanService,
        scan_result: Dict[str, Any],
        expiry: str,
        strike: float,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.scan_service = scan_service
        self.scan_result = scan_result
        self.expiry = expiry
        self.strike = strike

    def run(self) -> None:
        try:
            path = self.scan_service.generate_payoff_chart(
                self.scan_result, self.expiry, self.strike
            )
            self.finished.emit(path)
        except Exception as error:
            logger.exception(
                f"Payoff chart generation failed for {self.expiry} {self.strike}"
            )
            self.error.emit(str(error))


class IronCondorScanWorker(QThread):
    """Runs IronCondorScanService.scan() off the GUI thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = IronCondorScanService()
            result = service.scan(self.currency)
            self.finished.emit(result)
        except Exception as error:
            logger.exception(f"Iron condor scan failed for {self.currency}")
            self.error.emit(str(error))


class ButterflyScanWorker(QThread):
    """Runs ButterflyScanService.scan() off the GUI thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = ButterflyScanService()
            result = service.scan(self.currency)
            self.finished.emit(result)
        except Exception as error:
            logger.exception(f"Butterfly scan failed for {self.currency}")
            self.error.emit(str(error))


class IronCondorPayoffChartWorker(QThread):
    """Runs IronCondorScanService.generate_payoff_chart() off the GUI thread."""

    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self, scan_service: IronCondorScanService, scan_result: Dict[str, Any],
        expiry: str, short_call: float, long_call: float, short_put: float, long_put: float,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.scan_service = scan_service
        self.scan_result = scan_result
        self.expiry = expiry
        self.short_call, self.long_call, self.short_put, self.long_put = short_call, long_call, short_put, long_put

    def run(self) -> None:
        try:
            path = self.scan_service.generate_payoff_chart(
                self.scan_result, self.expiry, self.short_call, self.long_call, self.short_put, self.long_put,
            )
            self.finished.emit(path)
        except Exception as error:
            logger.exception(f"Iron condor payoff chart generation failed for {self.expiry}")
            self.error.emit(str(error))


class ButterflyPayoffChartWorker(QThread):
    """Runs ButterflyScanService.generate_payoff_chart() off the GUI thread."""

    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self, scan_service: ButterflyScanService, scan_result: Dict[str, Any],
        expiry: str, k1: float, k2: float, k3: float, parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.scan_service = scan_service
        self.scan_result = scan_result
        self.expiry = expiry
        self.k1, self.k2, self.k3 = k1, k2, k3

    def run(self) -> None:
        try:
            path = self.scan_service.generate_payoff_chart(self.scan_result, self.expiry, self.k1, self.k2, self.k3)
            self.finished.emit(path)
        except Exception as error:
            logger.exception(f"Butterfly payoff chart generation failed for {self.expiry}")
            self.error.emit(str(error))


class AutomationTab(QWidget):
    """Automation tab: pick a job + currency, run it, review the results."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.worker: Optional[QThread] = None
        self._last_scan_result: Optional[Dict[str, Any]] = None
        self._scan_service = StraddleScanService()  # format_alert + generate_payoff_chart (no I/O of its own)
        self._iron_condor_scan_service = IronCondorScanService()  # generate_payoff_chart (no I/O of its own)
        self._butterfly_scan_service = ButterflyScanService()  # generate_payoff_chart (no I/O of its own)
        self._chart_workers: List[QThread] = []

        # Quote-age tracking: scan results are snapshots, and Deribit quotes
        # drift within minutes. self._scan_as_of / self._scan_index_price
        # hold the last scan's timestamp so _update_age_banner can recompute
        # "how old is this data" every second, independent of the GUI thread
        # doing anything else.
        self._scan_as_of: Optional[datetime] = None
        self._scan_index_price: Optional[float] = None
        self._age_timer = QTimer(self)
        self._age_timer.setInterval(1000)
        self._age_timer.timeout.connect(self._update_age_banner)

        self._setup_ui()
        self._connect_signals()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel("Automation")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(header)

        desc = QLabel(
            "Manually trigger a scanner job and review its output. "
            "Nothing here is scheduled, recorded, or sent yet — this is a "
            "local preview of what an automated run would find."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(desc)

        layout.addWidget(self._build_controls())
        layout.addWidget(self._build_age_banner())
        layout.addWidget(self._build_results_tree(), stretch=1)
        layout.addWidget(self._build_alert_preview())

        self.setLayout(layout)

    def _build_controls(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(12)

        job_label = QLabel("Job:")
        job_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        row.addWidget(job_label)

        self.job_combo = QComboBox()
        self.job_combo.addItems(_JOBS)
        self.job_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.job_combo, stretch=2)

        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        row.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["BTC", "ETH"])
        row.addWidget(self.currency_combo)

        self.run_button = QPushButton("Run")
        self.run_button.setMinimumHeight(32)
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.run_button)

        row.addStretch()

        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        row.addWidget(self.status_label)

        return frame

    def _build_age_banner(self) -> QFrame:
        """
        Prominent "how stale is this data" banner shown above the results
        tree. Hidden until the first scan completes; then a QTimer ticks
        every second so the displayed age (and its color) stay live without
        requiring another scan.
        """
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 10, 16, 10)

        self.age_banner_label = QLabel("")
        self.age_banner_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: 600;")
        self.age_banner_label.setToolTip(
            "Prices in this table are the executable ASK prices captured at "
            "the moment the scan ran (\"as of\"). Deribit's live order book "
            "moves continuously between scans — verified: a snapshot taken "
            "at the same instant matches the website to the cent, but a "
            "scan just a few minutes old can already differ by roughly 1%. "
            "Only compare website prices against a FRESH scan — re-run the "
            "scan first if the age below is amber or red."
        )
        row.addWidget(self.age_banner_label)
        row.addStretch()

        frame.setLayout(row)
        frame.hide()  # nothing scanned yet
        self.age_banner_frame = frame
        return frame

    def _build_results_tree(self) -> QGroupBox:
        group = QGroupBox("Ranked Expiries")
        layout = QVBoxLayout()

        self.results_tree = QTreeWidget()
        self.results_tree.setColumnCount(len(_COLUMNS))
        self.results_tree.setHeaderLabels(_COLUMNS)
        self.results_tree.setRootIsDecorated(True)
        self.results_tree.setAlternatingRowColors(False)
        self.results_tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.results_tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.results_tree.setMinimumHeight(280)

        header = self.results_tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._apply_header_tooltips(_COLUMNS)

        layout.addWidget(self.results_tree)
        group.setLayout(layout)
        return group

    def _apply_header_tooltips(self, columns: list, overrides: Optional[Dict[str, str]] = None) -> None:
        """
        Apply header tooltips for the given column set. results_tree is
        shared across the straddle/iron-condor/butterfly jobs (see
        _COLUMNS/_IC_COLUMNS/_BF_COLUMNS above); setHeaderLabels only
        changes header TEXT, not tooltips, so this must be re-called
        after every setHeaderLabels swap in _run_job -- otherwise a
        tooltip set for one job's column index stays attached to that
        index after the header text there changes to a different job's
        column name.

        `overrides` (_IC_COLUMN_TOOLTIPS / _BF_COLUMN_TOOLTIPS) layers on
        top of the base _COLUMN_TOOLTIPS for column names that collide
        across job types but mean something different -- "Breakevens"
        in particular: the base entry states the straddle's formula AND
        profit direction, both wrong for a credit/debit structure that
        profits INSIDE its breakevens instead of outside.
        """
        tooltips = {**_COLUMN_TOOLTIPS, **(overrides or {})}
        for col, name in enumerate(columns):
            self.results_tree.headerItem().setToolTip(col, tooltips.get(name, name))

    def _build_alert_preview(self) -> QGroupBox:
        group = QGroupBox("Alert Preview (top result — what a Telegram send would say)")
        layout = QVBoxLayout()

        self.alert_text = QTextEdit()
        self.alert_text.setReadOnly(True)
        self.alert_text.setFontFamily("Consolas")
        self.alert_text.setFontPointSize(9)
        self.alert_text.setMaximumHeight(220)
        self.alert_text.setPlaceholderText("Run a scan to preview the alert text.")
        layout.addWidget(self.alert_text)

        group.setLayout(layout)
        return group

    def _connect_signals(self) -> None:
        self.run_button.clicked.connect(self._run_job)

    # ── Job execution ─────────────────────────────────────────────────────────

    def _run_job(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            logger.warning("A scan is already running")
            return

        job = self.job_combo.currentText()
        currency = self.currency_combo.currentText()

        self.results_tree.clear()
        self.alert_text.clear()
        self.run_button.setEnabled(False)
        self.status_label.setText(f"Scanning {currency}...")
        self.status_label.setStyleSheet(f"color: {Colors.WARNING};")

        if job == "Straddle Scanner (manual)":
            self.results_tree.setColumnCount(len(_COLUMNS))
            self.results_tree.setHeaderLabels(_COLUMNS)
            self._apply_header_tooltips(_COLUMNS)
            self.worker = StraddleScanWorker(currency)
            self.worker.finished.connect(self._on_scan_finished)
        elif job == "Iron Condor Scanner (manual)":
            self.results_tree.setColumnCount(len(_IC_COLUMNS))
            self.results_tree.setHeaderLabels(_IC_COLUMNS)
            self._apply_header_tooltips(_IC_COLUMNS, _IC_COLUMN_TOOLTIPS)
            self.worker = IronCondorScanWorker(currency)
            self.worker.finished.connect(self._on_ic_scan_finished)
        elif job == "Long Butterfly Scanner (manual)":
            self.results_tree.setColumnCount(len(_BF_COLUMNS))
            self.results_tree.setHeaderLabels(_BF_COLUMNS)
            self._apply_header_tooltips(_BF_COLUMNS, _BF_COLUMN_TOOLTIPS)
            self.worker = ButterflyScanWorker(currency)
            self.worker.finished.connect(self._on_bf_scan_finished)
        else:
            # Guards against a future dropdown addition silently falling
            # through without a handler.
            self.status_label.setText(f"No handler for job: {job}")
            self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
            self.run_button.setEnabled(True)
            return

        self.worker.error.connect(self._on_scan_error)
        self.worker.start()

    def _on_scan_finished(self, result: Dict[str, Any]) -> None:
        self.run_button.setEnabled(True)
        self._last_scan_result = result

        self._scan_as_of = result.get("as_of")
        self._scan_index_price = result.get("index_price")
        if self._scan_as_of is not None:
            self.age_banner_frame.show()
            self._update_age_banner()
            if not self._age_timer.isActive():
                self._age_timer.start()
        else:
            # Should not happen per StraddleScanService.scan()'s contract,
            # but never show a banner we can't compute an age for.
            self._age_timer.stop()
            self.age_banner_frame.hide()

        expiries = result.get("expiries", [])
        excluded = result.get("excluded", [])

        if not expiries and not excluded:
            self.status_label.setText("No expiries found in chain")
            self.status_label.setStyleSheet(f"color: {Colors.WARNING};")
        elif not expiries:
            self.status_label.setText(
                f"0 ranked, {len(excluded)} excluded"
            )
            self.status_label.setStyleSheet(f"color: {Colors.WARNING};")
        else:
            suffix = f", {len(excluded)} excluded" if excluded else ""
            self.status_label.setText(
                f"{len(expiries)} expir{'y' if len(expiries) == 1 else 'ies'} ranked{suffix}"
            )
            self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")

        self._populate_tree(expiries, excluded)
        self.alert_text.setPlainText(self._scan_service.format_alert(result))

    def _on_scan_error(self, error_message: str) -> None:
        self.run_button.setEnabled(True)
        self.status_label.setText("Scan failed")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
        self.alert_text.setPlainText(f"ERROR: {error_message}")

    def _on_ic_scan_finished(self, result: Dict[str, Any]) -> None:
        self.run_button.setEnabled(True)
        self._last_scan_result = result
        self._scan_as_of = result.get("as_of")
        self._scan_index_price = result.get("index_price")
        if self._scan_as_of is not None:
            self.age_banner_frame.show()
            self._update_age_banner()
            if not self._age_timer.isActive():
                self._age_timer.start()
        else:
            self._age_timer.stop()
            self.age_banner_frame.hide()

        expiries = result.get("expiries", [])
        excluded = result.get("excluded", [])
        self.results_tree.clear()
        for entry in expiries:
            candidates = entry.get("candidates") or []
            if not candidates:
                continue
            best = candidates[0]
            regime = entry.get("regime", {}) or {}
            values = [""] * len(_IC_COLUMNS)
            values[_IC_COL_EXPIRY] = entry["expiry"]
            values[_IC_COL_DTE] = f"{entry['dte']:.0f}"
            values[_IC_COL_SHORTC] = f"{best['short_call']:,.0f}"
            values[_IC_COL_LONGC] = f"{best['long_call']:,.0f}"
            values[_IC_COL_SHORTP] = f"{best['short_put']:,.0f}"
            values[_IC_COL_LONGP] = f"{best['long_put']:,.0f}"
            values[_IC_COL_CREDIT] = f"${best['cost_or_credit']:,.2f}"
            values[_IC_COL_MAXLOSS] = f"${best['max_loss']:,.2f}"
            values[_IC_COL_BE] = f"{best['breakeven_lo']:,.0f} / {best['breakeven_hi']:,.0f}"
            values[_IC_COL_PROB] = f"{best['prob_profit']:.1f}%" if best.get("prob_profit") is not None else "N/A"
            values[_IC_COL_EV] = f"${best['ev']:,.2f}" if best.get("ev") is not None else "N/A"
            values[_IC_COL_GATE] = "PASS" if regime.get("gate_pass") else "FAIL"
            item = QTreeWidgetItem(values)
            self.results_tree.addTopLevelItem(item)
            self._attach_ic_payoff_widget(item, entry["expiry"], best)
            self._attach_deribit_widget(item, best["deribit_url"], column=_IC_COL_DERIBIT)
        if excluded:
            self._add_excluded_items(excluded)

        if not expiries:
            self.status_label.setText(f"No iron condor candidates ({len(excluded)} expiries excluded)")
            self.status_label.setStyleSheet(f"color: {Colors.WARNING};")
        else:
            self.status_label.setText(f"Found {len(expiries)} iron condor candidate(s)")
            self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")
        self.alert_text.setPlainText(format_defined_risk_alert(result, "iron_condor"))

    def _on_bf_scan_finished(self, result: Dict[str, Any]) -> None:
        self.run_button.setEnabled(True)
        self._last_scan_result = result
        self._scan_as_of = result.get("as_of")
        self._scan_index_price = result.get("index_price")
        if self._scan_as_of is not None:
            self.age_banner_frame.show()
            self._update_age_banner()
            if not self._age_timer.isActive():
                self._age_timer.start()
        else:
            self._age_timer.stop()
            self.age_banner_frame.hide()

        expiries = result.get("expiries", [])
        excluded = result.get("excluded", [])
        self.results_tree.clear()
        for entry in expiries:
            candidates = entry.get("candidates") or []
            if not candidates:
                continue
            best = candidates[0]
            regime = entry.get("regime", {}) or {}
            values = [""] * len(_BF_COLUMNS)
            values[_BF_COL_EXPIRY] = entry["expiry"]
            values[_BF_COL_DTE] = f"{entry['dte']:.0f}"
            values[_BF_COL_K1] = f"{best['k1']:,.0f}"
            values[_BF_COL_K2] = f"{best['k2']:,.0f}"
            values[_BF_COL_K3] = f"{best['k3']:,.0f}"
            values[_BF_COL_COST] = f"${best['cost_or_credit']:,.2f}"
            values[_BF_COL_MAXPROFIT] = f"${best['max_profit']:,.2f}"
            values[_BF_COL_BE] = f"{best['breakeven_lo']:,.0f} / {best['breakeven_hi']:,.0f}"
            values[_BF_COL_PROB] = f"{best['prob_profit']:.1f}%" if best.get("prob_profit") is not None else "N/A"
            values[_BF_COL_EV] = f"${best['ev']:,.2f}" if best.get("ev") is not None else "N/A"
            values[_BF_COL_GATE] = "PASS" if regime.get("gate_pass") else "FAIL"
            item = QTreeWidgetItem(values)
            self.results_tree.addTopLevelItem(item)
            self._attach_bf_payoff_widget(item, entry["expiry"], best)
            self._attach_deribit_widget(item, best["deribit_url"], column=_BF_COL_DERIBIT)
        if excluded:
            self._add_excluded_items(excluded)

        if not expiries:
            self.status_label.setText(f"No butterfly candidates ({len(excluded)} expiries excluded)")
            self.status_label.setStyleSheet(f"color: {Colors.WARNING};")
        else:
            self.status_label.setText(f"Found {len(expiries)} butterfly candidate(s)")
            self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")
        self.alert_text.setPlainText(format_defined_risk_alert(result, "butterfly"))

    # ── Quote-age banner ──────────────────────────────────────────────────────

    def _update_age_banner(self) -> None:
        """
        Recompute and redraw the age banner. Called once right after a scan
        finishes and then every second via self._age_timer so the age (and
        its color) advance live even if the user never re-scans.
        """
        if self._scan_as_of is None:
            return

        as_of = self._scan_as_of
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - as_of).total_seconds()

        if age_seconds < 60:
            color = Colors.TEXT_PRIMARY
            suffix = ""
        elif age_seconds < 180:
            color = Colors.WARNING
            suffix = ""
        else:
            color = Colors.ERROR
            suffix = " — STALE, re-run scan"

        index_str = (
            f"${self._scan_index_price:,.2f}" if self._scan_index_price is not None else "N/A"
        )
        text = (
            f"As of {as_of.strftime('%H:%M:%S')} UTC · Index {index_str} · "
            f"age: {self._format_age(age_seconds)}{suffix}"
        )
        self.age_banner_label.setText(text)
        self.age_banner_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    @staticmethod
    def _format_age(age_seconds: float) -> str:
        """Format elapsed seconds as 'M:SS' (or 'H:MM:SS' past an hour)."""
        total_seconds = max(0, int(age_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    # ── Tree rendering ────────────────────────────────────────────────────────

    def _populate_tree(self, expiries: List[Dict[str, Any]], excluded: List[Dict[str, Any]]) -> None:
        self.results_tree.clear()

        for entry in expiries:
            self._add_expiry_item(entry)

        if excluded:
            self._add_excluded_items(excluded)

    def _add_expiry_item(self, entry: Dict[str, Any]) -> None:
        candidates = entry.get("candidates") or []
        if not candidates:
            return

        best = candidates[0]
        parent = QTreeWidgetItem(self._row_values(entry, best))
        self.results_tree.addTopLevelItem(parent)
        self._attach_payoff_widget(parent, entry["expiry"], best["strike"])
        self._attach_deribit_widget(parent, best["deribit_url"])

        # Rank 2 / rank 3 child rows — skipped if fewer candidates exist.
        for candidate in candidates[1:3]:
            child = QTreeWidgetItem(self._row_values(entry, candidate))
            parent.addChild(child)
            self._attach_payoff_widget(child, entry["expiry"], candidate["strike"])
            self._attach_deribit_widget(child, candidate["deribit_url"])

    def _row_values(self, entry: Dict[str, Any], candidate: Dict[str, Any]) -> List[str]:
        future_price = entry["F"] or 0.0
        cost_pct = (candidate["cost_usd"] / future_price * 100.0) if future_price else 0.0

        iv_percentile = entry["iv_percentile"]
        n_obs = entry.get("iv_percentile_n_obs")
        window_days = entry.get("iv_percentile_window_days")
        if iv_percentile is not None and n_obs is not None and window_days is not None:
            iv_percentile_str = f"{iv_percentile:.1f}% (n={n_obs:,}, {window_days:.0f}d)"
        elif iv_percentile is not None:
            iv_percentile_str = f"{iv_percentile:.1f}%"
        else:
            iv_percentile_str = "N/A"

        rv_iv_ratio = entry["rv_iv_ratio"]
        rv_iv_str = f"{rv_iv_ratio:.2f}" if rv_iv_ratio is not None else "N/A"

        vrp = entry["vrp"]
        vrp_str = f"{vrp:+.1f}" if vrp is not None else "N/A"

        score = candidate.get("min_pnl_score")
        score_str = f"${score:,.0f}" if score is not None else "N/A"

        call_ask_usd = candidate.get("call_ask_usd")
        put_ask_usd = candidate.get("put_ask_usd")
        call_str = f"${call_ask_usd:,.0f}" if call_ask_usd is not None else "N/A"
        put_str = f"${put_ask_usd:,.0f}" if put_ask_usd is not None else "N/A"

        values = [""] * len(_COLUMNS)
        values[_COL_EXPIRY] = entry["expiry"]
        values[_COL_DTE] = f"{entry['dte']:.0f}"
        values[_COL_STRIKE] = f"{candidate['strike']:,.0f}"
        values[_COL_CALL] = call_str
        values[_COL_PUT] = put_str
        values[_COL_COST] = f"${candidate['cost_usd']:,.0f} ({cost_pct:.1f}%)"
        values[_COL_BE] = f"{candidate['breakeven_down']:,.0f} / {candidate['breakeven_up']:,.0f}"
        values[_COL_IV] = iv_percentile_str
        values[_COL_RVIV] = rv_iv_str
        values[_COL_VRP] = vrp_str
        values[_COL_SCORE] = score_str
        # _COL_PAYOFF and _COL_DERIBIT are filled via setItemWidget, not text.
        return values

    def _attach_payoff_widget(self, item: QTreeWidgetItem, expiry: str, strike: float) -> None:
        button = QPushButton("View chart")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {Colors.ACCENT};
                border: none;
                text-decoration: underline;
                padding: 2px 4px;
                text-align: left;
            }}
            QPushButton:hover {{ color: {Colors.ACCENT_HOVER}; }}
            QPushButton:disabled {{ color: {Colors.TEXT_MUTED}; }}
        """)
        button.clicked.connect(lambda checked=False, e=expiry, s=strike, b=button: self._on_view_chart_clicked(e, s, b))
        self.results_tree.setItemWidget(item, _COL_PAYOFF, button)

    def _attach_deribit_widget(self, item: QTreeWidgetItem, url: str, column: int = _COL_DERIBIT) -> None:
        label = QLabel(f'<a href="{url}">Open Deribit ↗</a>')
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        label.setStyleSheet(f"color: {Colors.ACCENT}; padding: 2px 4px;")
        self.results_tree.setItemWidget(item, column, label)

    def _add_excluded_items(self, excluded: List[Dict[str, Any]]) -> None:
        separator = QTreeWidgetItem(["Excluded expiries"])
        separator.setFirstColumnSpanned(True)
        separator.setForeground(0, QColor(Colors.TEXT_MUTED))
        separator.setFlags(separator.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.results_tree.addTopLevelItem(separator)

        for excl in excluded:
            dte = excl.get("dte")
            dte_str = f"{dte:.1f}d" if dte is not None else "N/A"
            text = f"{excl['expiry']} ({dte_str}) — excluded: {excl['reason']}"
            item = QTreeWidgetItem([text])
            item.setFirstColumnSpanned(True)
            item.setForeground(0, QColor(Colors.TEXT_MUTED))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.results_tree.addTopLevelItem(item)

    # ── Payoff chart ──────────────────────────────────────────────────────────

    def _on_view_chart_clicked(self, expiry: str, strike: float, button: QPushButton) -> None:
        if self._last_scan_result is None:
            return

        button.setEnabled(False)
        button.setText("Generating...")

        worker = PayoffChartWorker(self._scan_service, self._last_scan_result, expiry, strike)
        worker.finished.connect(lambda path, w=worker, b=button: self._on_chart_ready(path, w, b))
        worker.error.connect(lambda msg, w=worker, b=button: self._on_chart_error(msg, w, b))
        self._chart_workers.append(worker)
        worker.start()

    def _attach_ic_payoff_widget(self, item: QTreeWidgetItem, expiry: str, candidate: Dict[str, Any]) -> None:
        button = QPushButton("View chart")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {Colors.ACCENT};
                border: none;
                text-decoration: underline;
                padding: 2px 4px;
                text-align: left;
            }}
            QPushButton:hover {{ color: {Colors.ACCENT_HOVER}; }}
            QPushButton:disabled {{ color: {Colors.TEXT_MUTED}; }}
        """)
        button.clicked.connect(lambda checked=False, e=expiry, c=candidate, b=button: self._on_view_ic_chart_clicked(e, c, b))
        self.results_tree.setItemWidget(item, _IC_COL_PAYOFF, button)

    def _on_view_ic_chart_clicked(self, expiry: str, candidate: Dict[str, Any], button: QPushButton) -> None:
        if self._last_scan_result is None:
            return
        button.setEnabled(False)
        button.setText("Generating...")
        worker = IronCondorPayoffChartWorker(
            self._iron_condor_scan_service, self._last_scan_result, expiry,
            candidate["short_call"], candidate["long_call"], candidate["short_put"], candidate["long_put"],
        )
        worker.finished.connect(lambda path, w=worker, b=button: self._on_chart_ready(path, w, b))
        worker.error.connect(lambda msg, w=worker, b=button: self._on_chart_error(msg, w, b))
        self._chart_workers.append(worker)
        worker.start()

    def _attach_bf_payoff_widget(self, item: QTreeWidgetItem, expiry: str, candidate: Dict[str, Any]) -> None:
        button = QPushButton("View chart")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {Colors.ACCENT};
                border: none;
                text-decoration: underline;
                padding: 2px 4px;
                text-align: left;
            }}
            QPushButton:hover {{ color: {Colors.ACCENT_HOVER}; }}
            QPushButton:disabled {{ color: {Colors.TEXT_MUTED}; }}
        """)
        button.clicked.connect(lambda checked=False, e=expiry, c=candidate, b=button: self._on_view_bf_chart_clicked(e, c, b))
        self.results_tree.setItemWidget(item, _BF_COL_PAYOFF, button)

    def _on_view_bf_chart_clicked(self, expiry: str, candidate: Dict[str, Any], button: QPushButton) -> None:
        if self._last_scan_result is None:
            return
        button.setEnabled(False)
        button.setText("Generating...")
        worker = ButterflyPayoffChartWorker(
            self._butterfly_scan_service, self._last_scan_result, expiry,
            candidate["k1"], candidate["k2"], candidate["k3"],
        )
        worker.finished.connect(lambda path, w=worker, b=button: self._on_chart_ready(path, w, b))
        worker.error.connect(lambda msg, w=worker, b=button: self._on_chart_error(msg, w, b))
        self._chart_workers.append(worker)
        worker.start()

    def _on_chart_ready(self, path: str, worker: QThread, button: QPushButton) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        button.setEnabled(True)
        button.setText("View chart")
        if worker in self._chart_workers:
            self._chart_workers.remove(worker)

    def _on_chart_error(self, message: str, worker: QThread, button: QPushButton) -> None:
        logger.error(f"Payoff chart generation failed: {message}")
        button.setEnabled(True)
        button.setText("View chart")
        self.status_label.setText("Chart generation failed")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
        if worker in self._chart_workers:
            self._chart_workers.remove(worker)
