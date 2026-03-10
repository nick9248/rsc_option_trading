# Flow Charts: Aggregated View, Duplicate Fix, Chart Info Update — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add "All Expirations" aggregated view to the flow charts GUI, confirm duplicate-file behavior, and rewrite chart info panels with accurate descriptions.

**Architecture:** Three independent changes — (1) new DB query method, (2) optional expiration in trend chart generator, (3) GUI wiring and chart info text. No new files except test file. All chart generators accept the same dict structure so aggregated data flows through unchanged.

**Tech Stack:** Python 3.13, PySide6, Plotly, psycopg2/PostgreSQL, pytest

---

## Task 1: Confirm duplicate file behavior (no code needed)

**Files:**
- Check: `output/charts/flow_analysis/` root

**Step 1: Verify no flat HTML files exist at root**

```bash
find output/charts/flow_analysis -maxdepth 1 -name "*.html"
```

Expected: no output (all files are inside expiry subfolders like `25DEC26/`).

If any flat files exist, note them and ask user for deletion approval — do NOT delete without approval.

**Step 2: Confirm save_chart always overwrites**

`fig.write_html(str(html_path))` always overwrites if the file exists (Python standard behavior). No change needed.

**Step 3: Commit**

No files changed — skip commit.

---

## Task 2: Add `get_aggregated_flow_metrics` to repository

**Files:**
- Modify: `coding/core/database/repository.py` (after `get_flow_metrics`, around line 1185)
- Test: `tests/unit/test_repository_aggregated_flow.py`

**Step 1: Write the failing test**

Create `tests/unit/test_repository_aggregated_flow.py`:

```python
"""Tests for get_aggregated_flow_metrics."""
from unittest.mock import MagicMock, patch
import pytest
from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    return repo


def test_aggregated_flow_returns_correct_structure():
    """Result has flow_data dict and spot_price float."""
    repo = _make_repo()

    # Simulate two expirations with same strike 90000, both have C and P
    fake_rows = [
        # strike, opt_type, buy_count, buy_vol, buy_not, sell_count, sell_vol, sell_not, net_flow, bs_ratio, price
        (90000, "C", 5, 1.0, 90000.0, 3, 0.5, 45000.0, 0.5, 2.0, 85000.0),
        (90000, "P", 2, 0.3, 27000.0, 4, 0.8, 72000.0, -0.5, 0.375, 85000.0),
        # second expiration same strike — should be summed
        (90000, "C", 3, 0.5, 45000.0, 1, 0.2, 18000.0, 0.3, 2.5, 85000.0),
        (90000, "P", 1, 0.1, 9000.0,  2, 0.4, 36000.0, -0.3, 0.25, 85000.0),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fake_rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert "flow_data" in result
    assert "spot_price" in result

    fd = result["flow_data"]
    assert 90000.0 in fd
    # C: buy_volume = 1.0 + 0.5 = 1.5
    assert abs(fd[90000.0]["C"]["buy_volume"] - 1.5) < 0.001
    # P: sell_volume = 0.8 + 0.4 = 1.2
    assert abs(fd[90000.0]["P"]["sell_volume"] - 1.2) < 0.001


def test_aggregated_flow_empty_returns_defaults():
    """Empty DB returns empty flow_data and 0.0 spot_price."""
    repo = _make_repo()

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert result == {"flow_data": {}, "spot_price": 0.0}
```

**Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_repository_aggregated_flow.py -v
```

Expected: `AttributeError: get_aggregated_flow_metrics`

**Step 3: Implement `get_aggregated_flow_metrics`**

In `coding/core/database/repository.py`, add after `get_flow_metrics` (around line 1185):

```python
def get_aggregated_flow_metrics(self, currency: str) -> Dict[str, Any]:
    """
    Get flow metrics aggregated across all expirations for a currency.

    Uses the latest snapshot per (expiration, strike, option_type) then
    sums all flow columns by (strike, option_type) across expirations.

    Args:
        currency: Currency symbol (BTC, ETH).

    Returns:
        Dict with flow_data structure and median spot_price.
    """
    query = """
        WITH latest_per_expiry AS (
            SELECT
                strike,
                option_type,
                buy_count,
                buy_volume,
                buy_notional,
                sell_count,
                sell_volume,
                sell_notional,
                net_flow,
                buy_sell_ratio,
                underlying_price
            FROM buy_sell_flow_metrics b
            WHERE currency = %s
              AND captured_at = (
                  SELECT MAX(captured_at)
                  FROM buy_sell_flow_metrics
                  WHERE currency = b.currency
                    AND expiration = b.expiration
              )
        )
        SELECT
            strike,
            option_type,
            SUM(buy_count)     AS buy_count,
            SUM(buy_volume)    AS buy_volume,
            SUM(buy_notional)  AS buy_notional,
            SUM(sell_count)    AS sell_count,
            SUM(sell_volume)   AS sell_volume,
            SUM(sell_notional) AS sell_notional,
            SUM(net_flow)      AS net_flow,
            AVG(underlying_price) AS underlying_price
        FROM latest_per_expiry
        GROUP BY strike, option_type
        ORDER BY strike, option_type
    """

    with self._db_cursor() as cursor:
        cursor.execute(query, (currency,))
        rows = cursor.fetchall()

    if not rows:
        return {"flow_data": {}, "spot_price": 0.0}

    flow_data: Dict[float, Dict[str, Any]] = {}
    prices = []

    for row in rows:
        strike, opt_type, buy_count, buy_vol, buy_not, sell_count, sell_vol, sell_not, net_flow, price = row

        strike_f = float(strike)
        if strike_f not in flow_data:
            flow_data[strike_f] = {}

        flow_data[strike_f][opt_type] = {
            "buy_count":    int(buy_count),
            "buy_volume":   float(buy_vol),
            "buy_notional": float(buy_not),
            "sell_count":   int(sell_count),
            "sell_volume":  float(sell_vol),
            "sell_notional": float(sell_not),
            "net_flow":     float(net_flow),
            "buy_sell_ratio": float(buy_vol) / float(sell_vol) if float(sell_vol) > 0 else None,
        }
        prices.append(float(price))

    spot_price = sorted(prices)[len(prices) // 2] if prices else 0.0  # median

    return {"flow_data": flow_data, "spot_price": spot_price}
```

**Step 4: Run tests**

```bash
pytest tests/unit/test_repository_aggregated_flow.py -v
```

Expected: both tests PASS.

**Step 5: Run all unit tests to confirm no regressions**

```bash
pytest tests/unit/ -v --tb=short
```

Expected: all pass.

**Step 6: Commit**

```bash
git add coding/core/database/repository.py tests/unit/test_repository_aggregated_flow.py
git commit -m "feat: add get_aggregated_flow_metrics to repository"
```

---

## Task 3: Make expiration optional in `generate_flow_trend_chart`

**Files:**
- Modify: `coding/core/analytics/chart_generator.py` — `generate_flow_trend_chart` function (line ~1164)

The current function signature is:
```python
def generate_flow_trend_chart(repository, currency, expiration, lookback_days=7)
```

Change to accept `expiration: Optional[str] = None`. When `None`, remove the expiration filter from the SQL query and set title to "All Expirations".

**Step 1: Write the failing test**

Add to `tests/unit/analytics/test_synthesis.py` OR create `tests/unit/analytics/test_chart_generator.py`:

```python
"""Tests for generate_flow_trend_chart all-expiration mode."""
from unittest.mock import MagicMock, patch
import pytest
import plotly.graph_objects as go
from coding.core.analytics.chart_generator import generate_flow_trend_chart


def _mock_repo(rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    repo = MagicMock()
    repo._db_cursor.return_value = mock_ctx
    return repo


def test_trend_chart_all_expirations_title():
    """When expiration=None, title contains 'All Expirations'."""
    repo = _mock_repo([])  # no data is fine, just checks title
    fig = generate_flow_trend_chart(repo, "BTC", expiration=None)
    assert isinstance(fig, go.Figure)
    assert "All Expirations" in fig.layout.title.text


def test_trend_chart_specific_expiration_title():
    """When expiration is given, title contains that expiration."""
    repo = _mock_repo([])
    fig = generate_flow_trend_chart(repo, "BTC", expiration="27MAR26")
    assert "27MAR26" in fig.layout.title.text
```

**Step 2: Run to verify it fails**

```bash
pytest tests/unit/analytics/test_chart_generator.py -v
```

Expected: `test_trend_chart_all_expirations_title` fails — `expiration` is required positional arg.

**Step 3: Implement the change**

In `coding/core/analytics/chart_generator.py`, modify `generate_flow_trend_chart`:

```python
def generate_flow_trend_chart(
    repository: Any,
    currency: str,
    expiration: Optional[str] = None,
    lookback_days: int = 7
) -> go.Figure:
    """
    Generate hourly flow trend chart over time.

    Args:
        repository: DatabaseRepository instance for querying trades.
        currency: Currency symbol (BTC or ETH).
        expiration: Expiration date string, or None for all expirations.
        lookback_days: Number of days to look back (default: 7).

    Returns:
        Plotly figure object.
    """
    end_time = datetime.now()
    start_time = end_time - timedelta(days=lookback_days)
    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)

    display_label = expiration if expiration else "All Expirations"

    if expiration:
        query = """
            SELECT
                DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000)) AS hour,
                option_type,
                direction,
                SUM(amount) AS total_volume
            FROM historical_trades
            WHERE currency = %s
                AND expiration = %s
                AND trade_timestamp >= %s
                AND trade_timestamp <= %s
                AND direction IS NOT NULL
            GROUP BY hour, option_type, direction
            ORDER BY hour ASC
        """
        params = (currency, expiration, start_ts, end_ts)
    else:
        query = """
            SELECT
                DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000)) AS hour,
                option_type,
                direction,
                SUM(amount) AS total_volume
            FROM historical_trades
            WHERE currency = %s
                AND trade_timestamp >= %s
                AND trade_timestamp <= %s
                AND direction IS NOT NULL
            GROUP BY hour, option_type, direction
            ORDER BY hour ASC
        """
        params = (currency, start_ts, end_ts)

    with repository._db_cursor() as cursor:
        cursor.execute(query, params)
        results = cursor.fetchall()

    if not results:
        logger.warning(f"No hourly flow data for {currency} {display_label}")
        fig = go.Figure()
        fig.update_layout(
            title=f"No flow trend data available - {currency} {display_label}",
            **get_chart_theme()
        )
        return fig

    # ... rest of the function unchanged, but replace `expiration` references
    # with `display_label` in the title:
    # fig.update_layout(title=f"Flow Trend Over Time - {currency} {display_label}", ...)
```

Key: replace `expiration` with `display_label` in `fig.update_layout(title=...)` at line ~1322.

**Step 4: Run tests**

```bash
pytest tests/unit/analytics/test_chart_generator.py -v
```

Expected: both pass.

**Step 5: Commit**

```bash
git add coding/core/analytics/chart_generator.py tests/unit/analytics/test_chart_generator.py
git commit -m "feat: make expiration optional in generate_flow_trend_chart for all-expiration mode"
```

---

## Task 4: Wire aggregated view in `FlowChartsWindow`

**Files:**
- Modify: `coding/gui/dialogs/flow_charts_window.py`
  - `_load_expirations` — prepend "All Expirations" item
  - `_on_expiration_changed` — route to `_generate_aggregate_charts`
  - Add `_generate_aggregate_charts` method

**Step 1: Update `_load_expirations` to prepend "All Expirations"**

In `_load_expirations`, after `self.expiration_combo.clear()` and before the `if not expirations` guard, add:

```python
# Always add aggregated view as first option
self.expiration_combo.addItem("🌐 All Expirations", "__ALL__")
```

Then change the final auto-load from index 0 to still load index 0 (which is now "All Expirations"):

```python
# Auto-load first item (All Expirations)
self._on_expiration_changed(0)
```

**Step 2: Update `_on_expiration_changed` to route `__ALL__`**

```python
def _on_expiration_changed(self, index: int) -> None:
    expiration = self.expiration_combo.itemData(index)
    if not expiration:
        return

    self.current_expiration = expiration

    if expiration == "__ALL__":
        logger.info("Loading aggregated charts for all expirations")
        self._generate_aggregate_charts()
    else:
        logger.info(f"Loading charts for {expiration}")
        self._generate_charts_from_db(expiration)
```

**Step 3: Add `_generate_aggregate_charts` method**

Add after `_generate_charts_from_db`:

```python
def _generate_aggregate_charts(self) -> None:
    """
    Generate all three charts aggregated across all expirations.

    Uses get_aggregated_flow_metrics for distribution and net flow,
    and expiration=None mode for the trend chart.
    """
    try:
        from coding.core.analytics.chart_generator import (
            generate_flow_distribution_chart,
            generate_net_flow_chart,
            generate_flow_trend_chart,
        )

        logger.info(f"Fetching aggregated flow metrics for {self.currency}")
        metrics = self.repository.get_aggregated_flow_metrics(self.currency)

        if not metrics or not metrics.get("flow_data"):
            logger.warning(f"No aggregated flow data for {self.currency}")
            self._show_empty_charts()
            return

        spot_price = metrics.get("spot_price", 0)
        label = "All Expirations"

        fig_dist = generate_flow_distribution_chart(
            flow_data=metrics,
            spot_price=spot_price,
            currency=self.currency,
            expiration=label,
        )
        fig_net = generate_net_flow_chart(
            flow_data=metrics,
            spot_price=spot_price,
            currency=self.currency,
            expiration=label,
        )
        fig_trend = generate_flow_trend_chart(
            repository=self.repository,
            currency=self.currency,
            expiration=None,  # all-expiration mode
            lookback_days=7,
        )

        temp_dir = Path(tempfile.gettempdir()) / "flow_charts"
        temp_dir.mkdir(exist_ok=True)

        dist_path = temp_dir / f"dist_{self.currency}_all.html"
        net_path = temp_dir / f"net_{self.currency}_all.html"
        trend_path = temp_dir / f"trend_{self.currency}_all.html"

        fig_dist.write_html(str(dist_path))
        fig_net.write_html(str(net_path))
        fig_trend.write_html(str(trend_path))

        inject_hover_js(dist_path)
        inject_hover_js(net_path)
        inject_hover_js(trend_path)

        self.distribution_view.setUrl(QUrl.fromLocalFile(str(dist_path.resolve())))
        self.net_flow_view.setUrl(QUrl.fromLocalFile(str(net_path.resolve())))
        self.trend_view.setUrl(QUrl.fromLocalFile(str(trend_path.resolve())))

        logger.info("Aggregated charts loaded")

    except Exception as e:
        import traceback
        logger.error(f"Failed to generate aggregate charts: {e}")
        logger.error(traceback.format_exc())
        self._show_empty_charts()
```

**Step 4: Manual smoke test**

Open the GUI → "View Flow Charts". Verify:
- Dropdown first item is "🌐 All Expirations"
- All three charts load without error
- Switching to a specific expiry still works
- Switching back to "All Expirations" works

**Step 5: Commit**

```bash
git add coding/gui/dialogs/flow_charts_window.py
git commit -m "feat: add All Expirations aggregated view to flow charts window"
```

---

## Task 5: Rewrite `_show_chart_info` panels

**Files:**
- Modify: `coding/gui/dialogs/flow_charts_window.py` — `_show_chart_info` method (line ~357)

Replace all three info strings with accurate descriptions matching the current jewel-tone palette and chart structure.

**Distribution by Strike** (tab 0):

```python
info = """
<b>What This Chart Shows:</b><br>
Population pyramid showing call and put flow activity per strike — calls extend right (positive), puts extend left (negative). Each strike has up to 4 bars split by direction and option type.<br><br>

<b>4-Bar Structure per Strike:</b><br>
• <span style='color:#10b981'>■ Call Buying</span> (emerald, right) — aggressive call buyers, bullish conviction<br>
• <span style='color:#f43f5e'>■ Call Selling</span> (rose, right) — writing calls, capping upside or closing longs<br>
• <span style='color:#818cf8'>■ Put Buying</span> (indigo, left) — bearish hedging or directional shorts<br>
• <span style='color:#f59e0b'>■ Put Selling</span> (amber, left) — selling downside protection, bullish premium collection<br><br>

<b>Metric Toggle (Top Right):</b><br>
• <b>Notional ($):</b> Dollar value of trades (contracts × price × multiplier). Best for sizing.<br>
• <b>Volume:</b> Number of contracts. Best for frequency.<br>
• <b>Trade Count:</b> Number of individual trades. Shows fragmentation vs. block activity.<br><br>

<b>Spot Price Marker:</b><br>
Gold annotation marks the current underlying price. Strikes near spot are most relevant for directional reads.<br><br>

<b>How to Interpret:</b><br>
• <b>Dominant call buying near/above spot</b> = Bullish speculation or delta hedging<br>
• <b>Dominant put buying near/below spot</b> = Fear, downside hedging, or short positioning<br>
• <b>Large put selling</b> = Institutions selling downside protection (bullish carry trade)<br>
• <b>Symmetric buying + selling</b> = Market makers/liquidity providers, not directional<br>
• <b>Strike clusters</b> = Key gamma levels; large dealers hedge here → price magnetic effect<br><br>

<b>Legend Hover:</b> Hover over a legend item to isolate that trace (dims others to 15% opacity).
"""
```

**Net Flow by Strike** (tab 1):

```python
info = """
<b>What This Chart Shows:</b><br>
Net buying/selling pressure per strike — each bar is Buy Volume minus Sell Volume. Positive bars = net buyers dominated. Negative bars = net sellers dominated. Calls and puts are separate traces.<br><br>

<b>4-Trace Color System:</b><br>
• <span style='color:#10b981'>■ Call Buying</span> (emerald) — strikes with net call buying pressure<br>
• <span style='color:#f43f5e'>■ Call Selling</span> (rose) — strikes with net call selling pressure<br>
• <span style='color:#818cf8'>■ Put Buying</span> (indigo) — strikes with net put buying pressure<br>
• <span style='color:#f59e0b'>■ Put Selling</span> (amber) — strikes with net put selling pressure<br>
Each trace only shows values at relevant strikes — bars are absent where that type had no net dominance.<br><br>

<b>How to Interpret:</b><br>
• <b>Tall emerald call bars above spot</b> = Bullish speculation; market expects upside<br>
• <b>Tall rose call bars</b> = Call writing dominant; expected ceiling on price<br>
• <b>Tall indigo put bars below spot</b> = Active hedging or bearish positioning<br>
• <b>Tall amber put bars</b> = Put selling (bullish carry); traders selling downside protection<br><br>

<b>Key Patterns:</b><br>
• Net call buying + Net put selling = Strong bullish signal<br>
• Net put buying + Net call selling = Strong bearish signal<br>
• Balanced net flow across all strikes = Neutral/range-bound<br>
• Concentrated net flow at one strike = Gamma magnet level<br><br>

<b>Zero Line:</b> Dashed line at 0 is the balance point. Bars above = buyers won. Below = sellers won.<br>
<b>Spot Price:</b> Yellow vertical line marks current underlying price.<br><br>

<b>Legend Hover:</b> Hover over a legend item to isolate that trace.
"""
```

**Flow Trend Over Time** (tab 2):

```python
info = """
<b>What This Chart Shows:</b><br>
Hourly aggregated option flow over the past 7 days. Shows how buying and selling pressure evolved over time — useful for detecting regime shifts, conviction buildup, and sentiment divergences from price.<br><br>

<b>5 Lines:</b><br>
• <span style='color:#10b981'>── Call Buy</span> (emerald, solid) — call buying volume per hour<br>
• <span style='color:#f43f5e'>── Call Sell</span> (rose, solid) — call selling volume per hour<br>
• <span style='color:#a78bfa'>- - Put Buy</span> (violet, dashed) — put buying volume per hour<br>
• <span style='color:#fb923c'>- - Put Sell</span> (orange, dashed) — put selling volume per hour<br>
• <span style='color:#60a5fa'>━━ Net Flow</span> (blue, thick) — total net flow = (call buy + put buy) − (call sell + put sell)<br><br>

<b>How to Interpret:</b><br>
• <b>Spikes in Call Buy</b> = Sudden bullish interest, often before moves or on news<br>
• <b>Spikes in Put Buy</b> = Fear events, hedging demand (tail risk buying)<br>
• <b>Sustained high Call Buy</b> = Persistent bullish positioning building<br>
• <b>Sustained high Put Sell</b> = Carry trade / bullish premium collection strategy<br>
• <b>Net Flow crossing above zero</b> = Market tilting net bullish in that window<br><br>

<b>Regime Detection:</b><br>
• <b>Accelerating flows</b> = Growing conviction in a direction<br>
• <b>Decelerating flows</b> = Weakening sentiment, potential reversal<br>
• <b>Flow reversal (buy → sell)</b> = Regime change; smart money repositioning<br>
• <b>Divergence</b> (price up, put buying increases) = Hedged rally; participants cautious<br><br>

<b>Timeframe:</b> Each data point = 1 hour. X-axis covers the last 7 days.<br><br>

<b>Legend Hover:</b> Hover over a legend item to isolate that line. Double-click to toggle.
"""
```

**Step 1: Replace all three info strings in `_show_chart_info`**

Locate the method at line ~357. Replace the `info = """..."""` string for each `current_tab` branch with the text above.

**Step 2: Also update color references** — the old info referenced `#22c55e`/`#f87171` (old green/red). The new text uses the actual jewel-tone palette. No functional change.

**Step 3: Manual verification**

Open charts window → click "ℹ️ Chart Info" on each of the three tabs. Verify the popup shows the new text with correct colors.

**Step 4: Commit**

```bash
git add coding/gui/dialogs/flow_charts_window.py
git commit -m "docs: rewrite flow chart info panels with accurate colors and trader-level descriptions"
```

---

## Final: Run full test suite

```bash
pytest tests/ -v --tb=short
```

Expected: all existing tests pass + 4 new tests pass.
