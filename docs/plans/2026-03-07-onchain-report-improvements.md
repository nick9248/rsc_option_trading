# On-Chain Report Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add IV Rank + Expected Movements to Market Metrics, fix missing GEX/DEX units, and add 1-day trend comparison to Max Pain / PC Ratio / Volume sections.

**Architecture:** All DB access stays in service layer. Analyzer receives data via setters and renders it in `generate_report()`. GexDexCalculator gets a `currency` param for unit labeling. IV Rank is computed from the same DVOL API call already in `_fetch_market_metrics`.

**Tech Stack:** Python 3.13, psycopg2, pytest

---

## Task 1: IV Rank + Expected Movements

### Affected Files
- Modify: `coding/core/analytics/on_chain_analyzer.py` (lines 945–968 and 605–633)
- Modify: `coding/service/on_chain/on_chain_analysis_service.py` (lines 780–818)
- Test: `tests/unit/analytics/test_on_chain_analyzer.py`

---

**Step 1: Write failing tests**

In `tests/unit/analytics/test_on_chain_analyzer.py`, add:

```python
import math

def test_market_metrics_iv_rank_rendered(sample_analyzer):
    """IV Rank appears in report when set."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    report = sample_analyzer.generate_report()
    assert "IV Rank (52w): 78.4%" in report

def test_market_metrics_expected_movements_rendered(sample_analyzer):
    """Expected daily/weekly/monthly moves appear in report."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6)
    report = sample_analyzer.generate_report()
    assert "Expected Daily Move:" in report
    assert "Expected Weekly Move:" in report
    assert "Expected Monthly Move:" in report

def test_market_metrics_iv_rank_none_skipped(sample_analyzer):
    """No IV Rank line when iv_rank is None."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=None)
    report = sample_analyzer.generate_report()
    assert "IV Rank" not in report
```

**Step 2: Run tests to verify they fail**

```bash
cd "C:\Users\Nick\PycharmProjects\option_trading"
.venv/Scripts/python.exe -m pytest tests/unit/analytics/test_on_chain_analyzer.py -k "iv_rank or expected_movements" -v
```
Expected: FAIL (set_market_metrics has no iv_rank param)

**Step 3: Add `iv_rank` param to `set_market_metrics` in `on_chain_analyzer.py`**

Replace lines 945–968:

```python
def set_market_metrics(
    self,
    dvol: Optional[float] = None,
    iv_percentile: Optional[float] = None,
    iv_rank: Optional[float] = None,
    current_funding: Optional[float] = None,
    funding_8h: Optional[float] = None,
) -> None:
    """
    Store market-wide metrics (DVOL, funding rate).

    Args:
        dvol: Current DVOL (Deribit Volatility Index) value.
        iv_percentile: IV percentile based on past 365 days.
        iv_rank: IV Rank: (dvol - 52w_low) / (52w_high - 52w_low) * 100.
        current_funding: Current funding rate from perpetual.
        funding_8h: 8-hour funding rate from perpetual.
    """
    self.market_metrics = {
        "dvol": dvol,
        "iv_percentile": iv_percentile,
        "iv_rank": iv_rank,
        "current_funding": current_funding,
        "funding_8h": funding_8h,
    }
```

**Step 4: Update `generate_report()` to render IV Rank + Expected Movements**

In `on_chain_analyzer.py`, add `import math` at top if not present.

Replace the Market Metrics rendering block (lines 605–633) with:

```python
# Market Metrics (DVOL, Funding Rate) - if available
if self.market_metrics:
    lines.append("MARKET METRICS")
    lines.append(sub_separator)

    dvol = self.market_metrics.get("dvol")
    iv_percentile = self.market_metrics.get("iv_percentile")
    iv_rank = self.market_metrics.get("iv_rank")
    current_funding = self.market_metrics.get("current_funding")
    funding_8h = self.market_metrics.get("funding_8h")

    if dvol is not None:
        lines.append(f"DVOL (Volatility Index): {dvol:.2f}")
    if iv_percentile is not None:
        lines.append(f"IV Percentile (365d): {iv_percentile:.1f}%")
    if iv_rank is not None:
        lines.append(f"IV Rank (52w): {iv_rank:.1f}%")

    # Expected moves: DVOL/100 / sqrt(periods) * spot
    if dvol is not None and self.underlying_price:
        iv_decimal = dvol / 100
        daily_move = iv_decimal / math.sqrt(365) * self.underlying_price
        weekly_move = iv_decimal / math.sqrt(52) * self.underlying_price
        monthly_move = iv_decimal / math.sqrt(12) * self.underlying_price
        daily_pct = iv_decimal / math.sqrt(365) * 100
        weekly_pct = iv_decimal / math.sqrt(52) * 100
        monthly_pct = iv_decimal / math.sqrt(12) * 100
        lines.append(
            f"Expected Daily Move:    ${daily_move:>8,.2f}  ({daily_pct:.1f}%)"
        )
        lines.append(
            f"Expected Weekly Move:   ${weekly_move:>8,.2f}  ({weekly_pct:.1f}%)"
        )
        lines.append(
            f"Expected Monthly Move:  ${monthly_move:>8,.2f}  ({monthly_pct:.1f}%)"
        )

    if current_funding is not None:
        funding_pct = current_funding * 100
        funding_annualized = current_funding * 3 * 365 * 100
        lines.append(
            f"Current Funding Rate: {funding_pct:.4f}% "
            f"({funding_annualized:.2f}% annualized)"
        )
    if funding_8h is not None:
        funding_8h_pct = funding_8h * 100
        lines.append(f"8h Funding Rate: {funding_8h_pct:.4f}%")

    lines.append("")
    lines.append(separator)
    lines.append("")
```

**Step 5: Compute IV Rank in service `_fetch_market_metrics`**

In `on_chain_analysis_service.py`, after line 781 (`dvol = close_values[-1]`), add:

```python
# Calculate IV Rank: (current - min) / (max - min) * 100
dvol_min = min(close_values)
dvol_max = max(close_values)
if dvol_max > dvol_min:
    iv_rank = (dvol - dvol_min) / (dvol_max - dvol_min) * 100
else:
    iv_rank = 50.0  # Flat history edge case
```

Then update the `set_market_metrics` call (line 813) to include `iv_rank`:

```python
analyzer.set_market_metrics(
    dvol=dvol,
    iv_percentile=iv_percentile,
    iv_rank=iv_rank,
    current_funding=current_funding,
    funding_8h=funding_8h
)
```

Also initialize `iv_rank = None` alongside `dvol = None` at line 757.

**Step 6: Run tests**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/analytics/test_on_chain_analyzer.py -k "iv_rank or expected_movements" -v
```
Expected: PASS

**Step 7: Commit**

```bash
git add coding/core/analytics/on_chain_analyzer.py coding/service/on_chain/on_chain_analysis_service.py tests/unit/analytics/test_on_chain_analyzer.py
git commit -m "feat: add IV Rank and expected daily/weekly/monthly moves to market metrics"
```

---

## Task 2: GEX/DEX Units

### Affected Files
- Modify: `coding/core/analytics/gex_dex_calculator.py` (lines 31–45, 271–380)
- Modify: `coding/service/on_chain/on_chain_analysis_service.py` (lines 169–176)
- Test: `tests/unit/analytics/test_gex_dex_calculator.py`

---

**Step 1: Write failing tests**

```python
def test_gex_report_shows_usd_unit():
    """GEX totals section labels values as USD."""
    calc = GexDexCalculator(instruments=sample_instruments, spot_price=2000.0, currency="ETH")
    report = calc.generate_report_section()
    assert "(USD)" in report

def test_dex_report_shows_eth_unit():
    """DEX totals section labels values in ETH."""
    calc = GexDexCalculator(instruments=sample_instruments, spot_price=2000.0, currency="ETH")
    report = calc.generate_report_section()
    assert "(ETH)" in report

def test_dex_report_shows_btc_unit():
    """DEX totals section labels values in BTC."""
    calc = GexDexCalculator(instruments=sample_instruments, spot_price=50000.0, currency="BTC")
    report = calc.generate_report_section()
    assert "(BTC)" in report
```

Run: `.venv/Scripts/python.exe -m pytest tests/unit/analytics/test_gex_dex_calculator.py -k "unit" -v`
Expected: FAIL (GexDexCalculator has no currency param)

**Step 2: Add `currency` param to `GexDexCalculator.__init__`**

```python
def __init__(
    self,
    instruments: List[Dict[str, Any]],
    spot_price: float,
    currency: str = "BTC",
):
    self.instruments = instruments
    self.spot_price = spot_price
    self.currency = currency
    self.strike_data: Dict[float, Dict[str, Any]] = {}
```

**Step 3: Update `generate_report_section()` to add units**

In the TOTALS block (lines 316–320), replace:

```python
lines.append("TOTALS:")
lines.append(f"  Total Net GEX: {result['total_net_gex']:+,.2f}")
lines.append(f"  Total Net DEX: {result['total_net_dex']:+,.2f}")
```

With:

```python
lines.append("TOTALS:")
lines.append(f"  Total Net GEX: {result['total_net_gex']:+,.2f} USD")
lines.append(f"  Total Net DEX: {result['total_net_dex']:+,.4f} {self.currency}")
```

In the KEY LEVELS block (lines 291–307), replace:

```python
lines.append(
    f"  Call Resistance: ${cr['strike']:,.0f} "
    f"(Net GEX: {cr['net_gex']:+,.2f})"
)
```
with:
```python
lines.append(
    f"  Call Resistance: ${cr['strike']:,.0f} "
    f"(Net GEX: {cr['net_gex']:+,.2f} USD)"
)
```

And:
```python
lines.append(
    f"  Put Support: ${ps['strike']:,.0f} "
    f"(Net GEX: {ps['net_gex']:+,.2f})"
)
```
with:
```python
lines.append(
    f"  Put Support: ${ps['strike']:,.0f} "
    f"(Net GEX: {ps['net_gex']:+,.2f} USD)"
)
```

In the column header (line 346):
```python
lines.append(
    f"{'Strike':>10}  {'Net GEX (USD)':>14}  {'Net DEX (' + self.currency + ')':>14}  "
    f"{'Cum GEX (USD)':>14}  {'Cum DEX (' + self.currency + ')':>14}  Notes"
)
```

**Step 4: Pass `currency` when instantiating GexDexCalculator in service**

In `on_chain_analysis_service.py` line 169:

```python
calculator = GexDexCalculator(
    instruments_with_greeks,
    analyzer.underlying_price,
    currency=analyzer.currency
)
```

**Step 5: Run tests**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/analytics/test_gex_dex_calculator.py -v
```
Expected: PASS

**Step 6: Commit**

```bash
git add coding/core/analytics/gex_dex_calculator.py coding/service/on_chain/on_chain_analysis_service.py tests/unit/analytics/test_gex_dex_calculator.py
git commit -m "feat: add USD/currency units to GEX/DEX report section"
```

---

## Task 3: 1-Day Trend for Max Pain, PC Ratio, Volume

### Affected Files
- Modify: `coding/core/analytics/on_chain_analyzer.py` (add `trend_data` dict + setter, update `generate_report`)
- Modify: `coding/service/on_chain/on_chain_analysis_service.py` (add `_fetch_trend_data` method, call it before `generate_report`)
- Test: `tests/unit/analytics/test_on_chain_analyzer.py`

---

**Step 1: Write failing tests**

```python
def test_max_pain_trend_shown_when_data_available(sample_analyzer):
    """Max Pain section shows trend vs prior when trend_data is set."""
    sample_analyzer.set_trend_data("10MAR26", {
        "max_pain_strike": 2000.0,
        "call_oi": 2500.0,
        "put_oi": 4000.0,
        "pc_ratio": 1.60,
        "total_volume": 7000.0,
        "volume_ratio": 1.40,
    })
    report = sample_analyzer.generate_report()
    assert "vs prior" in report or "unchanged" in report.lower()

def test_max_pain_trend_skipped_when_no_data(sample_analyzer):
    """Max Pain section renders normally when no trend_data for expiration."""
    # No set_trend_data call → no crash
    report = sample_analyzer.generate_report()
    assert "Max Pain Strike:" in report

def test_trend_data_graceful_on_new_expiry(sample_analyzer):
    """No crash when trend_data is set but prev value is None."""
    sample_analyzer.set_trend_data("10MAR26", None)
    report = sample_analyzer.generate_report()
    assert "Max Pain Strike:" in report
```

Run: `.venv/Scripts/python.exe -m pytest tests/unit/analytics/test_on_chain_analyzer.py -k "trend" -v`
Expected: FAIL (set_trend_data not defined)

**Step 2: Add `trend_data` storage and setter to `OnChainAnalyzer`**

In `on_chain_analyzer.py`, in `__init__`, after the other storage dicts:

```python
self.trend_data: Dict[str, Optional[Dict]] = {}  # prev DB snapshot per expiry
```

Add setter method (after `set_market_metrics`):

```python
def set_trend_data(self, expiration: str, data: Optional[Dict]) -> None:
    """
    Store previous DB snapshot for trend comparison.

    Args:
        expiration: Expiration string (e.g. '10MAR26').
        data: Dict with prev values, or None if no prior record exists.
              Keys: max_pain_strike, call_oi, put_oi, pc_ratio,
                    total_volume, volume_ratio.
    """
    self.trend_data[expiration] = data
```

**Step 3: Update `generate_report()` to show trends**

Add a helper inside `generate_report` (or as a private method) for formatting trend:

```python
def _format_trend(self, current: float, previous: Optional[float], unit: str = "", pct: bool = False) -> str:
    """Format a trend indicator vs previous value."""
    if previous is None:
        return ""
    delta = current - previous
    if delta == 0:
        return "  [→ unchanged]"
    arrow = "↑" if delta > 0 else "↓"
    if pct:
        return f"  [{arrow} from {previous:.2f}, {delta:+.2f}]"
    return f"  [{arrow} from {previous:,.2f}{unit}, {delta:+,.2f}{unit}]"
```

In the Max Pain block (after `lines.append(f"Distance from Current: ...")`):

```python
trend = self.trend_data.get(expiration)
prev = trend if trend else None
if prev:
    prev_mp = prev.get("max_pain_strike")
    lines.append(
        f"Trend vs Prior:  Max Pain {self._format_trend(max_pain_strike, prev_mp, '$').strip()}"
        if prev_mp is not None else "Trend vs Prior:  (no prior data)"
    )
```

In the PC Ratio block (after `lines.append(f"P/C Ratio: ...")`):

```python
if prev:
    prev_call_oi = prev.get("call_oi")
    prev_put_oi = prev.get("put_oi")
    prev_pc = prev.get("pc_ratio")
    if prev_call_oi is not None:
        lines.append(f"  Call OI change: {self._format_trend(pcr['total_call_oi'], prev_call_oi).strip()}")
        lines.append(f"  Put OI change:  {self._format_trend(pcr['total_put_oi'], prev_put_oi).strip()}")
    if prev_pc is not None:
        lines.append(f"  P/C Ratio change: {self._format_trend(pcr['ratio'], prev_pc, pct=True).strip()}")
```

In the Volume block (after the volume ratio line):

```python
if prev:
    prev_vol = prev.get("total_volume")
    prev_vr = prev.get("volume_ratio")
    if prev_vol is not None:
        lines.append(f"  Total Volume change: {self._format_trend(vol['total_volume'], prev_vol).strip()}")
    if prev_vr is not None and vol['volume_ratio'] != float('inf'):
        lines.append(f"  Vol P/C Ratio change: {self._format_trend(vol['volume_ratio'], prev_vr, pct=True).strip()}")
```

**Step 4: Add `_fetch_trend_data` to service**

In `on_chain_analysis_service.py`, add this method:

```python
def _fetch_trend_data(
    self,
    analyzer: "OnChainAnalyzer",
    progress_callback: Callable[[str], None]
) -> None:
    """
    Fetch previous DB snapshots for trend comparison in report.

    Queries max_pain, open_interest, volume tables (limit=2).
    The most-recent record = the prior capture (not the current live run).
    Sets trend_data on analyzer for each expiration.

    Skipped silently when repository is not configured.
    """
    if self.repository is None:
        return

    progress_callback("Fetching trend data for report comparison...")

    for expiration in analyzer.get_expirations():
        try:
            mp_history = self.repository.get_max_pain_history(
                analyzer.currency, expiration, limit=2
            )
            oi_history = self.repository.get_open_interest_history(
                analyzer.currency, expiration, limit=2
            )
            vol_history = self.repository.get_volume_history(
                analyzer.currency, expiration, limit=2
            )

            # Use the oldest of the 2 returned records as "previous"
            # (most recent was captured before this live run)
            prev_mp = mp_history[0] if len(mp_history) >= 1 else None
            prev_oi = oi_history[0] if len(oi_history) >= 1 else None
            prev_vol = vol_history[0] if len(vol_history) >= 1 else None

            if not any([prev_mp, prev_oi, prev_vol]):
                analyzer.set_trend_data(expiration, None)
                continue

            trend = {}
            if prev_mp:
                trend["max_pain_strike"] = float(prev_mp["max_pain_strike"])
            if prev_oi:
                trend["call_oi"] = float(prev_oi["total_call_oi"])
                trend["put_oi"] = float(prev_oi["total_put_oi"])
                trend["pc_ratio"] = float(prev_oi["put_call_ratio"]) if prev_oi["put_call_ratio"] else None
            if prev_vol:
                trend["total_volume"] = float(prev_vol["total_call_volume"]) + float(prev_vol["total_put_volume"])
                trend["volume_ratio"] = float(prev_vol["volume_put_call_ratio"]) if prev_vol["volume_put_call_ratio"] else None

            analyzer.set_trend_data(expiration, trend)

        except Exception as e:
            logger.warning(f"Failed to fetch trend data for {expiration}: {e}")
            analyzer.set_trend_data(expiration, None)
```

**Step 5: Call `_fetch_trend_data` in `fetch_and_analyze`**

In `fetch_and_analyze`, before line 110 (`progress("Generating analysis report...")`), add:

```python
# Fetch previous DB snapshots for trend comparison
self._fetch_trend_data(analyzer, progress)
```

**Step 6: Run tests**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/analytics/test_on_chain_analyzer.py -v
```
Expected: PASS

**Step 7: Full test suite**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ -v
```
Expected: all pass

**Step 8: Commit**

```bash
git add coding/core/analytics/on_chain_analyzer.py coding/service/on_chain/on_chain_analysis_service.py tests/unit/analytics/test_on_chain_analyzer.py
git commit -m "feat: add 1-day trend comparison to Max Pain, PC Ratio, and Volume sections"
```

---

## Final Verification

Run a live analysis and check the report output:

```bash
.venv/Scripts/python.exe -c "
from coding.core.logging.logging_setup import init_logging
init_logging()
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.database.config import DatabaseConfig
from coding.core.database.repository import DatabaseRepository

config = DatabaseConfig()
repo = DatabaseRepository(config)
api = DeribitApiService()
svc = OnChainAnalysisService(api, repository=repo)
report = svc.fetch_and_analyze('ETH', progress_callback=print)
# Print just the first expiration's section
idx = report.find('EXPIRATION:')
print(report[:idx + 1500])
"
```

Verify:
1. MARKET METRICS section has IV Rank, Expected Daily/Weekly/Monthly moves
2. GEX/DEX section shows "USD" for GEX values and "ETH" for DEX values
3. Max Pain / PC Ratio / Volume sections show trend arrows for expirations with DB history
4. New expirations (like 10MAR26 with only 1 prior record) show gracefully
