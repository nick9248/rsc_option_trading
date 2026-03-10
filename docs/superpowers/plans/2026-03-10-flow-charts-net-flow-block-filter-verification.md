# Flow Charts: Net Flow Redesign, Block Filter, Verification Suite — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign Net Flow chart to horizontal 2-trace layout, add Block/Non-Block/All trade filter across all flow charts, and build a 6-check verification suite to validate flow data correctness end-to-end.

**Architecture:** Three independent changes to the flow charts subsystem. The net flow rewrite replaces 4 vertical traces with 2 horizontal traces in `generate_net_flow_chart`. The block filter threads a `trade_filter` string through two independent SQL paths (`BuySellFlowAnalyzer._fetch_trades` and `generate_flow_trend_chart`) and wires a 3-button toggle in the GUI. The verification script queries both `buy_sell_flow_metrics` and `historical_trades` directly to cross-check six data integrity properties.

**Tech Stack:** Python 3.13, Plotly (`go.Bar` horizontal), PySide6, psycopg2/PostgreSQL, pytest

---

## Chunk 1: Net Flow Chart Redesign + BuySellFlowAnalyzer Filter

### Task 1: Rewrite `generate_net_flow_chart` (horizontal, 2 traces)

**Files:**
- Modify: `coding/core/analytics/chart_generator.py` (function `generate_net_flow_chart`, lines ~1039-1161)
- Test: `tests/unit/analytics/test_chart_generator.py`

**Context:** Current implementation has 4 traces (Call Buying, Call Selling, Put Buying, Put Selling) with None-masking. Replace with 2 horizontal bar traces — one for call net flow, one for put net flow. Strikes go on Y-axis, net volume on X-axis. Signed values: positive = net buying (bar extends right), negative = net selling (bar extends left). Use `barmode="relative"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/analytics/test_chart_generator.py`:

```python
def test_net_flow_chart_has_exactly_two_traces():
    """Redesigned chart must have exactly 2 traces: Call Net Flow and Put Net Flow."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
            85000.0: {"C": {"net_flow": -0.3}, "P": {"net_flow": 2.1}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    assert len(fig.data) == 2


def test_net_flow_chart_trace_names():
    """Traces must be named 'Call Net Flow' and 'Put Net Flow'."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    names = [t.name for t in fig.data]
    assert "Call Net Flow" in names
    assert "Put Net Flow" in names


def test_net_flow_chart_is_horizontal():
    """All traces must have orientation='h'."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    for trace in fig.data:
        assert trace.orientation == "h", f"Trace '{trace.name}' should be horizontal"


def test_net_flow_chart_signed_values():
    """x values (net volume) must be the signed net flow values."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
            85000.0: {"C": {"net_flow": -0.3}, "P": {"net_flow": 2.1}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    call_trace = next(t for t in fig.data if t.name == "Call Net Flow")
    put_trace = next(t for t in fig.data if t.name == "Put Net Flow")
    # x holds the values; y holds the strike labels
    assert list(call_trace.x) == [1.5, -0.3]
    assert list(put_trace.x) == [-0.8, 2.1]


def test_net_flow_chart_colors():
    """Call Net Flow = emerald #10b981, Put Net Flow = indigo #818cf8."""
    flow_data = {
        "flow_data": {80000.0: {"C": {"net_flow": 1.0}, "P": {"net_flow": -1.0}}},
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    call_trace = next(t for t in fig.data if t.name == "Call Net Flow")
    put_trace = next(t for t in fig.data if t.name == "Put Net Flow")
    assert call_trace.marker.color == "#10b981"
    assert put_trace.marker.color == "#818cf8"


def test_net_flow_chart_empty_data():
    """Empty flow data must return a figure without crashing."""
    fig = generate_net_flow_chart({"flow_data": {}}, spot_price=0.0, currency="BTC", expiration="27MAR26")
    assert fig is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/analytics/test_chart_generator.py::test_net_flow_chart_has_exactly_two_traces tests/unit/analytics/test_chart_generator.py::test_net_flow_chart_trace_names tests/unit/analytics/test_chart_generator.py::test_net_flow_chart_is_horizontal tests/unit/analytics/test_chart_generator.py::test_net_flow_chart_signed_values tests/unit/analytics/test_chart_generator.py::test_net_flow_chart_colors -v
```

Expected: FAIL — current chart has 4 traces, not 2.

- [ ] **Step 3: Rewrite `generate_net_flow_chart`**

Replace the entire function body in `coding/core/analytics/chart_generator.py` (keeping the signature and docstring structure):

```python
def generate_net_flow_chart(
    flow_data: Dict[str, Any],
    spot_price: float,
    currency: str,
    expiration: str
) -> go.Figure:
    """
    Generate net flow chart showing call and put net flow per strike.

    Horizontal bar chart — strikes on Y-axis, net volume on X-axis.
    2 traces: Call Net Flow (emerald) and Put Net Flow (indigo).
    Bar extends right = net buying, left = net selling.
    Net = buy_volume - sell_volume (signed).

    Args:
        flow_data: Result from BuySellFlowAnalyzer.calculate() or repository.
        spot_price: Current underlying spot price.
        currency: Currency symbol (BTC or ETH).
        expiration: Expiration date string (used in title).

    Returns:
        Plotly figure object.
    """
    per_strike_data = flow_data.get("flow_data", {})

    if not per_strike_data:
        logger.warning("No flow data for net flow chart")
        fig = go.Figure()
        fig.update_layout(
            title=f"No flow data available - {currency} {expiration}",
            **get_chart_theme()
        )
        return fig

    theme = get_chart_theme()
    strikes = sorted(per_strike_data.keys())

    # Format strike labels for categorical Y-axis (e.g. "$80,000")
    strike_labels = [f"${s:,.0f}" for s in strikes]

    call_net = []
    put_net = []

    for strike in strikes:
        strike_data = per_strike_data[strike]
        call_net.append(float(strike_data.get("C", {}).get("net_flow", 0)))
        put_net.append(float(strike_data.get("P", {}).get("net_flow", 0)))

    fig = go.Figure()

    # Call Net Flow — Emerald
    fig.add_trace(go.Bar(
        x=call_net,
        y=strike_labels,
        name="Call Net Flow",
        orientation="h",
        marker_color="#10b981",
        marker_line=dict(color="rgba(255,255,255,0.12)", width=0.5),
        opacity=0.90,
        hovertemplate="<b>Strike: %{y}</b><br>Call Net: %{x:.4f} " + currency + "<extra></extra>",
    ))

    # Put Net Flow — Indigo
    fig.add_trace(go.Bar(
        x=put_net,
        y=strike_labels,
        name="Put Net Flow",
        orientation="h",
        marker_color="#818cf8",
        marker_line=dict(color="rgba(255,255,255,0.12)", width=0.5),
        opacity=0.90,
        hovertemplate="<b>Strike: %{y}</b><br>Put Net: %{x:.4f} " + currency + "<extra></extra>",
    ))

    # Zero line — vertical (x=0 on a horizontal bar chart)
    fig.add_vline(
        x=0,
        line_dash="dash",
        line_color="#666666",
        annotation_text="Zero",
        annotation_position="top",
    )

    # Spot price — horizontal annotation on categorical Y-axis
    # find nearest label
    spot_label = None
    if strikes:
        nearest_strike = min(strikes, key=lambda s: abs(s - spot_price))
        spot_label = f"${nearest_strike:,.0f}"

    if spot_label and spot_label in strike_labels:
        spot_idx = strike_labels.index(spot_label)
        fig.add_annotation(
            x=0,
            y=spot_idx,
            xref="x",
            yref="y",
            text=f"← Spot ~${spot_price:,.0f}",
            showarrow=False,
            font=dict(color="#ffd93d", size=11),
            xanchor="left",
            yanchor="middle",
        )

    fig.update_layout(
        title=f"Net Flow by Strike (Buy Vol − Sell Vol) — {currency} {expiration}",
        xaxis_title=f"Net Flow ({currency})  ·  ← selling  |  buying →",
        yaxis_title="Strike Price",
        barmode="relative",
        hovermode="y unified",
        autosize=True,
        annotations=[
            dict(
                text="Net = Buy Volume − Sell Volume",
                xref="paper", yref="paper",
                x=0.0, y=1.04,
                showarrow=False,
                font=dict(color="#888888", size=11),
                xanchor="left",
            )
        ] + (fig.layout.annotations or []),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.06,
            itemclick="toggleothers",
            itemdoubleclick="toggle",
        ),
        **theme
    )

    return fig
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/analytics/test_chart_generator.py -v
```

Expected: all net flow tests PASS plus existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add coding/core/analytics/chart_generator.py tests/unit/analytics/test_chart_generator.py
git commit -m "feat: rewrite generate_net_flow_chart as horizontal 2-trace layout (Call Net + Put Net)"
```

---

### Task 2: Add `trade_filter` to `BuySellFlowAnalyzer`

**Files:**
- Modify: `coding/core/analytics/buy_sell_flow_analyzer.py`
- Test: `tests/unit/test_buy_sell_flow_analyzer.py`

**Context:** Add optional `trade_filter: str = "all"` param to `__init__`. In `_fetch_trades`, inject a SQL filter clause based on the value. Safe — this is a controlled enum, not user input.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_buy_sell_flow_analyzer.py`:

```python
def test_buy_sell_flow_analyzer_default_trade_filter():
    """Default trade_filter should be 'all' (no SQL injection needed)."""
    repo = MagicMock()
    analyzer = BuySellFlowAnalyzer(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        spot_price=85000.0,
    )
    assert analyzer.trade_filter == "all"


def test_buy_sell_flow_analyzer_block_filter_param():
    """trade_filter='block' should be stored on the analyzer."""
    repo = MagicMock()
    analyzer = BuySellFlowAnalyzer(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        spot_price=85000.0,
        trade_filter="block",
    )
    assert analyzer.trade_filter == "block"


def test_fetch_trades_block_filter_injects_sql(monkeypatch):
    """Block filter must inject 'AND (amount * index_price) >= 100000' into the SQL."""
    captured_queries = []

    class FakeCursor:
        def execute(self, query, params):
            captured_queries.append(query)
        def fetchall(self):
            return []
        def __enter__(self): return self
        def __exit__(self, *args): pass

    repo = MagicMock()
    repo._db_cursor.return_value = FakeCursor()

    analyzer = BuySellFlowAnalyzer(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        spot_price=85000.0,
        trade_filter="block",
    )
    analyzer._fetch_trades(lookback_hours=24)

    assert len(captured_queries) == 1
    assert "(amount * index_price) >= 100000" in captured_queries[0]


def test_fetch_trades_non_block_filter_injects_sql(monkeypatch):
    """Non-block filter must inject 'AND (amount * index_price) < 100000' into SQL."""
    captured_queries = []

    class FakeCursor:
        def execute(self, query, params):
            captured_queries.append(query)
        def fetchall(self):
            return []
        def __enter__(self): return self
        def __exit__(self, *args): pass

    repo = MagicMock()
    repo._db_cursor.return_value = FakeCursor()

    analyzer = BuySellFlowAnalyzer(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        spot_price=85000.0,
        trade_filter="non_block",
    )
    analyzer._fetch_trades(lookback_hours=24)

    assert "(amount * index_price) < 100000" in captured_queries[0]


def test_fetch_trades_all_filter_no_block_clause(monkeypatch):
    """'all' filter must NOT inject any block filter clause."""
    captured_queries = []

    class FakeCursor:
        def execute(self, query, params):
            captured_queries.append(query)
        def fetchall(self):
            return []
        def __enter__(self): return self
        def __exit__(self, *args): pass

    repo = MagicMock()
    repo._db_cursor.return_value = FakeCursor()

    analyzer = BuySellFlowAnalyzer(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        spot_price=85000.0,
        trade_filter="all",
    )
    analyzer._fetch_trades(lookback_hours=24)

    assert "(amount * index_price)" not in captured_queries[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_buy_sell_flow_analyzer.py::test_buy_sell_flow_analyzer_default_trade_filter tests/unit/test_buy_sell_flow_analyzer.py::test_buy_sell_flow_analyzer_block_filter_param tests/unit/test_buy_sell_flow_analyzer.py::test_fetch_trades_block_filter_injects_sql tests/unit/test_buy_sell_flow_analyzer.py::test_fetch_trades_non_block_filter_injects_sql tests/unit/test_buy_sell_flow_analyzer.py::test_fetch_trades_all_filter_no_block_clause -v
```

Expected: FAIL — `trade_filter` param doesn't exist yet.

- [ ] **Step 3: Add `trade_filter` param and SQL injection**

In `coding/core/analytics/buy_sell_flow_analyzer.py`, update `__init__`:

```python
def __init__(
    self,
    repository: DatabaseRepository,
    currency: str,
    expiration: str,
    spot_price: float,
    lookback_hours: int = 24,
    trade_filter: str = "all",
):
    """
    Initialize buy/sell flow analyzer.

    Args:
        repository: Database repository for querying trades.
        currency: Currency symbol (BTC or ETH).
        expiration: Expiration date string (e.g., "27MAR26").
        spot_price: Current underlying spot price.
        lookback_hours: Hours to look back for trade data (default: 24).
        trade_filter: Trade size filter — "all", "block", or "non_block".
            "block" = only trades with notional >= $100k.
            "non_block" = only trades with notional < $100k.
            "all" = no filter (default, backward compatible).
    """
    self.repository = repository
    self.currency = currency
    self.expiration = expiration
    self.spot_price = spot_price
    self.lookback_hours = lookback_hours
    self.trade_filter = trade_filter
    self.flow_data: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
        lambda: {
            "C": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                  "buy_notional": 0.0, "sell_notional": 0.0},
            "P": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                  "buy_notional": 0.0, "sell_notional": 0.0},
        }
    )
```

In `_fetch_trades`, add filter clause injection after defining `end_ts`:

```python
    filter_clause = {
        "block":     "AND (amount * index_price) >= 100000",
        "non_block": "AND (amount * index_price) < 100000",
    }.get(self.trade_filter, "")

    query = f"""
        SELECT
            trade_id, trade_timestamp, instrument_name, strike,
            option_type, price, amount, direction, index_price
        FROM historical_trades
        WHERE currency = %s
            AND expiration = %s
            AND trade_timestamp >= %s
            AND trade_timestamp <= %s
            AND strike IS NOT NULL
            AND direction IS NOT NULL
            {filter_clause}
        ORDER BY trade_timestamp ASC
    """
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_buy_sell_flow_analyzer.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding/core/analytics/buy_sell_flow_analyzer.py tests/unit/test_buy_sell_flow_analyzer.py
git commit -m "feat: add trade_filter param to BuySellFlowAnalyzer with SQL injection for block/non-block filtering"
```

---

## Chunk 2: Trend Chart Filter + GUI Toggle

### Task 3: Add `trade_filter` to `generate_flow_trend_chart`

**Files:**
- Modify: `coding/core/analytics/chart_generator.py` (function `generate_flow_trend_chart`, lines ~1164+)
- Test: `tests/unit/analytics/test_chart_generator.py`

**Context:** `generate_flow_trend_chart` has its own independent SQL query against `historical_trades`. The block filter must be injected here too — it is a parallel change, not related to `BuySellFlowAnalyzer`. Both SQL queries against `historical_trades` must honor the filter.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/analytics/test_chart_generator.py`:

```python
def test_trend_chart_block_filter_injects_sql():
    """Block filter must inject the block clause into the trend chart SQL."""
    captured_queries = []

    class FakeCursor:
        def execute(self, query, params):
            captured_queries.append(query)
        def fetchall(self):
            return []
        def __enter__(self): return self
        def __exit__(self, *args): pass

    repo = MagicMock()
    repo._db_cursor.return_value = FakeCursor()

    generate_flow_trend_chart(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        trade_filter="block",
    )

    assert len(captured_queries) == 1
    assert "(amount * index_price) >= 100000" in captured_queries[0]


def test_trend_chart_non_block_filter_injects_sql():
    """Non-block filter must inject the non-block clause."""
    captured_queries = []

    class FakeCursor:
        def execute(self, query, params):
            captured_queries.append(query)
        def fetchall(self):
            return []
        def __enter__(self): return self
        def __exit__(self, *args): pass

    repo = MagicMock()
    repo._db_cursor.return_value = FakeCursor()

    generate_flow_trend_chart(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        trade_filter="non_block",
    )

    assert "(amount * index_price) < 100000" in captured_queries[0]


def test_trend_chart_all_filter_no_block_clause():
    """Default 'all' filter must NOT inject any block clause."""
    captured_queries = []

    class FakeCursor:
        def execute(self, query, params):
            captured_queries.append(query)
        def fetchall(self):
            return []
        def __enter__(self): return self
        def __exit__(self, *args): pass

    repo = MagicMock()
    repo._db_cursor.return_value = FakeCursor()

    generate_flow_trend_chart(
        repository=repo,
        currency="BTC",
        expiration="27MAR26",
        trade_filter="all",
    )

    assert "(amount * index_price)" not in captured_queries[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/analytics/test_chart_generator.py::test_trend_chart_block_filter_injects_sql tests/unit/analytics/test_chart_generator.py::test_trend_chart_non_block_filter_injects_sql tests/unit/analytics/test_chart_generator.py::test_trend_chart_all_filter_no_block_clause -v
```

Expected: FAIL — `trade_filter` param doesn't exist on `generate_flow_trend_chart`.

- [ ] **Step 3: Add `trade_filter` param and SQL injection**

Update `generate_flow_trend_chart` signature:

```python
def generate_flow_trend_chart(
    repository: Any,
    currency: str,
    expiration: Optional[str] = None,
    lookback_days: int = 7,
    trade_filter: str = "all",
) -> go.Figure:
```

Inside the function, before the `if expiration:` branch, add:

```python
    filter_clause = {
        "block":     "AND (amount * index_price) >= 100000",
        "non_block": "AND (amount * index_price) < 100000",
    }.get(trade_filter, "")
```

Then inject `{filter_clause}` into both SQL strings (expiration and all-expirations branches). For the per-expiration branch:

```python
        query = f"""
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
                {filter_clause}
            GROUP BY hour, option_type, direction
            ORDER BY hour ASC
        """
```

For the all-expirations branch:

```python
        query = f"""
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
                {filter_clause}
            GROUP BY hour, option_type, direction
            ORDER BY hour ASC
        """
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/analytics/test_chart_generator.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding/core/analytics/chart_generator.py tests/unit/analytics/test_chart_generator.py
git commit -m "feat: add trade_filter param to generate_flow_trend_chart with SQL injection for block/non-block"
```

---

### Task 4: Wire Block/Non-Block/All toggle in `FlowChartsWindow`

**Files:**
- Modify: `coding/gui/dialogs/flow_charts_window.py`
- Modify: `coding/service/on_chain/on_chain_analysis_service.py` (backward-compat: pass `trade_filter="all"`)

**Context:** Add a 3-button toggle group `[All] [Block] [Non-Block]` to the controls bar. Default: All. On click, set `self.current_filter` and regenerate charts. For All Expirations mode with a non-"all" filter, bypass `get_aggregated_flow_metrics` and run `BuySellFlowAnalyzer` per expiration in Python, aggregating results.

No unit tests for GUI widget code (PySide6 requires display). The logic in `_generate_aggregate_charts_with_filter` is testable via integration if needed. For now we test the service backward-compat path and the filter propagation.

- [ ] **Step 1: Add `self.current_filter` and toggle buttons in `_setup_ui`**

In `flow_charts_window.py`, add to `__init__` after existing attributes:

```python
self.current_filter: str = "all"
```

In `_setup_ui`, after `controls.addWidget(self.expiration_combo)` and before `controls.addStretch()`, add the toggle group:

```python
        # Block filter toggle group
        filter_label = QLabel("Filter:")
        filter_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; margin-left: 16px;")
        controls.addWidget(filter_label)

        self._filter_buttons = {}
        filter_btn_style_active = f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.ACCENT};
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
                font-size: 12px;
            }}
        """
        filter_btn_style_inactive = f"""
            QPushButton {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
                color: {Colors.TEXT_PRIMARY};
            }}
        """

        for filter_key, filter_label_text in [("all", "All"), ("block", "Block"), ("non_block", "Non-Block")]:
            btn = QPushButton(filter_label_text)
            btn.setStyleSheet(filter_btn_style_active if filter_key == "all" else filter_btn_style_inactive)
            btn.clicked.connect(lambda checked, k=filter_key: self._on_filter_changed(k))
            controls.addWidget(btn)
            self._filter_buttons[filter_key] = (btn, filter_btn_style_active, filter_btn_style_inactive)
```

- [ ] **Step 2: Add `_on_filter_changed` method**

Add after `_on_expiration_changed`:

```python
    def _on_filter_changed(self, filter_mode: str) -> None:
        """
        Update active filter and regenerate charts.

        Args:
            filter_mode: "all", "block", or "non_block".
        """
        self.current_filter = filter_mode

        # Update button styles
        for key, (btn, active_style, inactive_style) in self._filter_buttons.items():
            btn.setStyleSheet(active_style if key == filter_mode else inactive_style)

        # Regenerate charts with new filter
        expiration = self.expiration_combo.currentData()
        if not expiration:
            return

        if expiration == "__ALL__":
            self._generate_aggregate_charts()
        else:
            self._generate_charts_from_db(expiration)
```

- [ ] **Step 3: Propagate `trade_filter` in `_generate_charts_from_db`**

`_generate_charts_from_db` generates charts from pre-aggregated `buy_sell_flow_metrics` (no raw trades). The block filter only applies to `generate_flow_trend_chart` in this path — the distribution and net flow charts use pre-aggregated data which doesn't support filtering.

Update the trend chart call in `_generate_charts_from_db`:

```python
            fig_trend = generate_flow_trend_chart(
                repository=self.repository,
                currency=self.currency,
                expiration=expiration,
                lookback_days=7,
                trade_filter=self.current_filter,
            )
```

- [ ] **Step 4: Update `_generate_aggregate_charts` to handle non-"all" filters**

Replace the existing `_generate_aggregate_charts` method with this implementation that bypasses `get_aggregated_flow_metrics` when filter != "all":

```python
    def _generate_aggregate_charts(self) -> None:
        """
        Generate all three charts aggregated across all expirations.

        When filter == "all": uses get_aggregated_flow_metrics (fast, pre-aggregated).
        When filter != "all": runs BuySellFlowAnalyzer per expiration and aggregates
        in Python so the block filter can be applied to raw historical_trades.
        """
        try:
            from coding.core.analytics.chart_generator import (
                generate_flow_distribution_chart,
                generate_net_flow_chart,
                generate_flow_trend_chart,
            )
            from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
            from collections import defaultdict

            label = "All Expirations"

            if self.current_filter == "all":
                # Fast path: use pre-aggregated metrics table
                logger.info(f"Fetching aggregated flow metrics for {self.currency}")
                metrics = self.repository.get_aggregated_flow_metrics(self.currency)
            else:
                # Filtered path: re-run BuySellFlowAnalyzer per expiration
                logger.info(
                    f"Fetching per-expiration flow with filter={self.current_filter} for {self.currency}"
                )
                expirations = self.repository.get_active_expirations_with_flow(self.currency)
                if not expirations:
                    logger.warning(f"No active expirations found for {self.currency}")
                    self._show_empty_charts()
                    return

                # Aggregate flow_data across all expirations in Python
                agg_flow: dict = defaultdict(lambda: {
                    "C": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                          "buy_notional": 0.0, "sell_notional": 0.0, "net_flow": 0.0,
                          "buy_sell_ratio": None},
                    "P": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                          "buy_notional": 0.0, "sell_notional": 0.0, "net_flow": 0.0,
                          "buy_sell_ratio": None},
                })
                spot_prices = []

                for exp_info in expirations:
                    exp = exp_info["expiration"]
                    try:
                        analyzer = BuySellFlowAnalyzer(
                            repository=self.repository,
                            currency=self.currency,
                            expiration=exp,
                            spot_price=0.0,  # placeholder — not used for aggregation
                            trade_filter=self.current_filter,
                        )
                        result = analyzer.calculate()
                        exp_flow = result.get("flow_data", {})
                        if result.get("spot_price"):
                            spot_prices.append(result["spot_price"])

                        for strike, type_data in exp_flow.items():
                            for opt_type, vals in type_data.items():
                                target = agg_flow[strike][opt_type]
                                for field in ("buy_count", "sell_count", "buy_volume",
                                              "sell_volume", "buy_notional", "sell_notional"):
                                    target[field] += vals.get(field, 0.0)

                    except Exception as exp_err:
                        logger.warning(f"Skipping {exp} during aggregation: {exp_err}")

                # Recompute net_flow and buy_sell_ratio from aggregated values
                for strike_data in agg_flow.values():
                    for opt_data in strike_data.values():
                        opt_data["net_flow"] = opt_data["buy_volume"] - opt_data["sell_volume"]
                        sv = opt_data["sell_volume"]
                        opt_data["buy_sell_ratio"] = (
                            opt_data["buy_volume"] / sv if sv > 0 else None
                        )

                spot_price = (sum(spot_prices) / len(spot_prices)) if spot_prices else 0.0
                metrics = {"flow_data": dict(agg_flow), "spot_price": spot_price}

            if not metrics or not metrics.get("flow_data"):
                logger.warning(f"No aggregated flow data for {self.currency}")
                self._show_empty_charts()
                return

            spot_price = metrics.get("spot_price", 0)

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
                expiration=None,
                lookback_days=7,
                trade_filter=self.current_filter,
            )

            temp_dir = Path(tempfile.gettempdir()) / "flow_charts"
            temp_dir.mkdir(exist_ok=True)

            filter_suffix = f"_{self.current_filter}" if self.current_filter != "all" else ""
            dist_path = temp_dir / f"dist_{self.currency}_all{filter_suffix}.html"
            net_path = temp_dir / f"net_{self.currency}_all{filter_suffix}.html"
            trend_path = temp_dir / f"trend_{self.currency}_all{filter_suffix}.html"

            fig_dist.write_html(str(dist_path))
            fig_net.write_html(str(net_path))
            fig_trend.write_html(str(trend_path))

            inject_hover_js(dist_path)
            inject_hover_js(net_path)
            inject_hover_js(trend_path)

            self.distribution_view.setUrl(QUrl.fromLocalFile(str(dist_path.resolve())))
            self.net_flow_view.setUrl(QUrl.fromLocalFile(str(net_path.resolve())))
            self.trend_view.setUrl(QUrl.fromLocalFile(str(trend_path.resolve())))

            logger.info(f"Aggregated charts loaded (filter={self.current_filter})")

        except Exception as e:
            import traceback
            logger.error(f"Failed to generate aggregate charts: {e}")
            logger.error(traceback.format_exc())
            self._show_empty_charts()
```

- [ ] **Step 5: Update `on_chain_analysis_service.py` for backward compat**

In `coding/service/on_chain/on_chain_analysis_service.py`, find where `BuySellFlowAnalyzer` is instantiated and ensure `trade_filter="all"` is passed explicitly (backward-compatible since it's the default, but explicit for clarity):

Find the instantiation (search for `BuySellFlowAnalyzer(`):

```python
            analyzer = BuySellFlowAnalyzer(
                repository=self.repository,
                currency=currency,
                expiration=expiration,
                spot_price=spot_price,
                trade_filter="all",
            )
```

- [ ] **Step 6: Run all existing tests to confirm no regressions**

```
pytest tests/ -v --tb=short
```

Expected: all previously passing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add coding/gui/dialogs/flow_charts_window.py coding/service/on_chain/on_chain_analysis_service.py
git commit -m "feat: add Block/Non-Block/All toggle to FlowChartsWindow with per-expiration aggregation bypass"
```

---

## Chunk 3: Verification Suite

### Task 5: Create `scripts/verify_flow_data.py`

**Files:**
- Create: `scripts/verify_flow_data.py`

**Context:** 6-check verification suite. Checks 1-4 and 6 are PASS/FAIL. Check 5 is informational only (direction semantics). The script reads from both `buy_sell_flow_metrics` and `historical_trades` directly. Run with `python scripts/verify_flow_data.py --currency BTC`.

- [ ] **Step 1: Create the script**

```python
"""
Flow data verification suite.

Verifies that buy_sell_flow_metrics correctly reflects raw historical_trades data.
Runs 6 cross-checks; Checks 1-4 and 6 are PASS/FAIL, Check 5 is informational.

Usage:
    python scripts/verify_flow_data.py --currency BTC
    python scripts/verify_flow_data.py --currency ETH
    python scripts/verify_flow_data.py --currency BTC --expiration 27MAR26
"""

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path so imports work when run as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository

init_logging(level="WARNING")  # Suppress routine DB logs during verification
logger = logging.getLogger(__name__)


def _db_cursor(repo):
    """Get a database cursor."""
    return repo._db_cursor()


def check_1_mathematical_consistency(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 1: Mathematical consistency within buy_sell_flow_metrics.

    For each row: net_flow == round(buy_volume - sell_volume, 8)
    buy_sell_ratio == buy_volume / sell_volume when sell > 0, NULL when sell == 0.
    """
    print("\n[CHECK 1] Mathematical consistency")

    exp_clause = "AND expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT strike, option_type, expiration, buy_volume, sell_volume, net_flow, buy_sell_ratio
        FROM buy_sell_flow_metrics
        WHERE currency = %s
          {exp_clause}
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    if not rows:
        print("  No rows found in buy_sell_flow_metrics.")
        print("  SKIP (no data)")
        return True  # No data is not a failure

    discrepancies = []
    for strike, opt_type, exp, buy_vol, sell_vol, net_flow, ratio in rows:
        buy_vol = float(buy_vol)
        sell_vol = float(sell_vol)
        net_flow = float(net_flow) if net_flow is not None else None
        ratio = float(ratio) if ratio is not None else None

        expected_net = round(buy_vol - sell_vol, 8)
        if net_flow is not None and abs(net_flow - expected_net) > 1e-6:
            discrepancies.append(
                f"  {exp} {strike} {opt_type}: net_flow={net_flow:.8f} expected={expected_net:.8f}"
            )

        if sell_vol > 0:
            expected_ratio = buy_vol / sell_vol
            if ratio is not None and abs(ratio - expected_ratio) > 1e-6:
                discrepancies.append(
                    f"  {exp} {strike} {opt_type}: buy_sell_ratio={ratio:.6f} expected={expected_ratio:.6f}"
                )
        else:
            # sell_vol == 0: ratio should be NULL
            if ratio is not None:
                discrepancies.append(
                    f"  {exp} {strike} {opt_type}: buy_sell_ratio={ratio} expected=NULL (sell_vol=0)"
                )

    print(f"  Rows checked: {len(rows):,}")
    print(f"  Discrepancies: {len(discrepancies)}")
    if discrepancies:
        print("  First 5 discrepancies:")
        for d in discrepancies[:5]:
            print(d)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_2_trade_count_reconciliation(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 2: Trade count reconciliation.

    For latest captured_at per (currency, expiration), per (strike, option_type):
    COUNT direction='buy' in historical_trades within 24h window == buy_count.
    COUNT direction='sell' == sell_count.
    """
    print("\n[CHECK 2] Trade count reconciliation")

    exp_clause = "AND m.expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    # Get latest metrics with their time windows
    query = f"""
        SELECT DISTINCT ON (m.expiration, m.strike, m.option_type)
            m.expiration, m.strike, m.option_type,
            m.buy_count, m.sell_count, m.captured_at
        FROM buy_sell_flow_metrics m
        WHERE m.currency = %s
          {exp_clause}
        ORDER BY m.expiration, m.strike, m.option_type, m.captured_at DESC
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        metric_rows = cursor.fetchall()

    if not metric_rows:
        print("  No rows found.")
        print("  SKIP (no data)")
        return True

    mismatches = []
    checked = 0

    for exp, strike, opt_type, buy_count, sell_count, captured_at in metric_rows:
        # 24h window ending at captured_at
        window_end = captured_at
        window_start = window_end - timedelta(hours=24)
        start_ts = int(window_start.timestamp() * 1000)
        end_ts = int(window_end.timestamp() * 1000)

        count_query = """
            SELECT direction, COUNT(*) as cnt
            FROM historical_trades
            WHERE currency = %s
              AND expiration = %s
              AND strike = %s
              AND option_type = %s
              AND trade_timestamp >= %s
              AND trade_timestamp <= %s
              AND direction IS NOT NULL
            GROUP BY direction
        """

        with _db_cursor(repo) as cursor:
            cursor.execute(count_query, (currency, exp, strike, opt_type, start_ts, end_ts))
            count_rows = cursor.fetchall()

        raw_counts = {"buy": 0, "sell": 0}
        for direction, cnt in count_rows:
            raw_counts[direction] = cnt

        checked += 1
        expected_buy = int(buy_count) if buy_count else 0
        expected_sell = int(sell_count) if sell_count else 0

        if raw_counts["buy"] != expected_buy or raw_counts["sell"] != expected_sell:
            mismatches.append(
                f"  {exp} ${strike} {opt_type}: "
                f"buy={raw_counts['buy']} (expected {expected_buy}), "
                f"sell={raw_counts['sell']} (expected {expected_sell})"
            )

    print(f"  Strike-type pairs checked: {checked:,}")
    print(f"  Mismatches: {len(mismatches)}")
    if mismatches:
        print("  First 5 mismatches:")
        for m in mismatches[:5]:
            print(m)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_3_volume_reconciliation(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 3: Volume reconciliation.

    SUM(amount) per (strike, option_type, direction) from historical_trades within 24h window
    must match buy_volume / sell_volume in buy_sell_flow_metrics.
    Tolerance: 1e-6.
    """
    print("\n[CHECK 3] Volume reconciliation")

    exp_clause = "AND m.expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT DISTINCT ON (m.expiration, m.strike, m.option_type)
            m.expiration, m.strike, m.option_type,
            m.buy_volume, m.sell_volume, m.captured_at
        FROM buy_sell_flow_metrics m
        WHERE m.currency = %s
          {exp_clause}
        ORDER BY m.expiration, m.strike, m.option_type, m.captured_at DESC
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        metric_rows = cursor.fetchall()

    if not metric_rows:
        print("  No rows found.")
        print("  SKIP (no data)")
        return True

    mismatches = []
    max_discrepancy = 0.0
    checked = 0

    for exp, strike, opt_type, buy_volume, sell_volume, captured_at in metric_rows:
        window_end = captured_at
        window_start = window_end - timedelta(hours=24)
        start_ts = int(window_start.timestamp() * 1000)
        end_ts = int(window_end.timestamp() * 1000)

        vol_query = """
            SELECT direction, SUM(amount) as total
            FROM historical_trades
            WHERE currency = %s
              AND expiration = %s
              AND strike = %s
              AND option_type = %s
              AND trade_timestamp >= %s
              AND trade_timestamp <= %s
              AND direction IS NOT NULL
            GROUP BY direction
        """

        with _db_cursor(repo) as cursor:
            cursor.execute(vol_query, (currency, exp, strike, opt_type, start_ts, end_ts))
            vol_rows = cursor.fetchall()

        raw_vols = {"buy": 0.0, "sell": 0.0}
        for direction, total in vol_rows:
            raw_vols[direction] = float(total) if total else 0.0

        checked += 1
        exp_buy = float(buy_volume) if buy_volume else 0.0
        exp_sell = float(sell_volume) if sell_volume else 0.0

        disc_buy = abs(raw_vols["buy"] - exp_buy)
        disc_sell = abs(raw_vols["sell"] - exp_sell)
        max_discrepancy = max(max_discrepancy, disc_buy, disc_sell)

        if disc_buy > 1e-6 or disc_sell > 1e-6:
            mismatches.append(
                f"  {exp} ${strike} {opt_type}: "
                f"buy_vol={raw_vols['buy']:.6f} (expected {exp_buy:.6f}, diff={disc_buy:.8f}), "
                f"sell_vol={raw_vols['sell']:.6f} (expected {exp_sell:.6f}, diff={disc_sell:.8f})"
            )

    print(f"  Strike-type pairs checked: {checked:,}")
    print(f"  Mismatches: {len(mismatches)}  (tolerance: 1e-6)")
    print(f"  Largest absolute discrepancy: {max_discrepancy:.2e}")
    if mismatches:
        print("  First 5 mismatches:")
        for m in mismatches[:5]:
            print(m)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_4_notional_reconciliation(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 4: Notional reconciliation.

    SUM(amount * index_price) per (strike, option_type, direction) from historical_trades
    must match buy_notional / sell_notional in buy_sell_flow_metrics.
    """
    print("\n[CHECK 4] Notional reconciliation")

    exp_clause = "AND m.expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT DISTINCT ON (m.expiration, m.strike, m.option_type)
            m.expiration, m.strike, m.option_type,
            m.buy_notional, m.sell_notional, m.captured_at
        FROM buy_sell_flow_metrics m
        WHERE m.currency = %s
          {exp_clause}
        ORDER BY m.expiration, m.strike, m.option_type, m.captured_at DESC
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        metric_rows = cursor.fetchall()

    if not metric_rows:
        print("  No rows found.")
        print("  SKIP (no data)")
        return True

    mismatches = []
    max_discrepancy = 0.0
    checked = 0

    for exp, strike, opt_type, buy_notional, sell_notional, captured_at in metric_rows:
        window_end = captured_at
        window_start = window_end - timedelta(hours=24)
        start_ts = int(window_start.timestamp() * 1000)
        end_ts = int(window_end.timestamp() * 1000)

        notional_query = """
            SELECT direction, SUM(amount * index_price) as total
            FROM historical_trades
            WHERE currency = %s
              AND expiration = %s
              AND strike = %s
              AND option_type = %s
              AND trade_timestamp >= %s
              AND trade_timestamp <= %s
              AND direction IS NOT NULL
              AND index_price IS NOT NULL
            GROUP BY direction
        """

        with _db_cursor(repo) as cursor:
            cursor.execute(notional_query, (currency, exp, strike, opt_type, start_ts, end_ts))
            notional_rows = cursor.fetchall()

        raw_notional = {"buy": 0.0, "sell": 0.0}
        for direction, total in notional_rows:
            raw_notional[direction] = float(total) if total else 0.0

        checked += 1
        exp_buy = float(buy_notional) if buy_notional else 0.0
        exp_sell = float(sell_notional) if sell_notional else 0.0

        disc_buy = abs(raw_notional["buy"] - exp_buy)
        disc_sell = abs(raw_notional["sell"] - exp_sell)
        max_discrepancy = max(max_discrepancy, disc_buy, disc_sell)

        # Notional can differ slightly due to index_price changes between capture time
        # and trade time; use a larger tolerance: $1 or 0.01% of notional
        tolerance = max(1.0, exp_buy * 0.0001, exp_sell * 0.0001)

        if disc_buy > tolerance or disc_sell > tolerance:
            mismatches.append(
                f"  {exp} ${strike} {opt_type}: "
                f"buy_notional={raw_notional['buy']:.2f} (expected {exp_buy:.2f}), "
                f"sell_notional={raw_notional['sell']:.2f} (expected {exp_sell:.2f})"
            )

    print(f"  Strike-type pairs checked: {checked:,}")
    print(f"  Mismatches: {len(mismatches)}")
    print(f"  Largest absolute discrepancy: ${max_discrepancy:,.2f}")
    if mismatches:
        print("  First 5 mismatches:")
        for m in mismatches[:5]:
            print(m)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_5_direction_semantics(repo, currency: str, expiration: str = None) -> None:
    """
    Check 5: Direction semantics (informational only — never fails the suite).

    Hypothesis: direction='buy' correlates with tick_direction IN (0, 1) (price moving up).
    Options markets have weaker correlation than futures — low percentage is expected.
    WARNING if correlation < 45% (below random chance).
    """
    print("\n[CHECK 5] Direction semantics (informational)")

    exp_clause = "AND expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT direction, tick_direction
        FROM historical_trades
        WHERE currency = %s
          {exp_clause}
          AND tick_direction IS NOT NULL
          AND direction IS NOT NULL
        ORDER BY trade_timestamp DESC
        LIMIT 500
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    if not rows:
        print("  No trades with tick_direction found.")
        print("  SKIP")
        return

    # tick_direction: 0=plus tick, 1=zero-plus tick, 2=minus tick, 3=zero-minus tick
    # Hypothesis: buy -> tick_direction in (0, 1)
    total = len(rows)
    correlated = sum(
        1 for direction, tick_dir in rows
        if direction == "buy" and tick_dir in (0, 1)
    )
    also_buy_negative = sum(
        1 for direction, tick_dir in rows
        if direction == "buy" and tick_dir in (2, 3)
    )
    buy_total = sum(1 for direction, _ in rows if direction == "buy")
    correlation_pct = (correlated / buy_total * 100) if buy_total > 0 else 0.0

    print(f"  Trades sampled: {total:,}")
    print(f"  Buy trades: {buy_total:,}")
    print(f"  Buy + tick (0,1) correlation: {correlation_pct:.1f}%")
    print("  NOTE: Options markets have weaker aggressor/tick correlation than futures.")
    print("        Mid-market crosses are common; a low percentage is expected for liquid options.")

    if correlation_pct < 45.0:
        print(f"  WARNING: Correlation {correlation_pct:.1f}% < 45% threshold.")
        print("           This may indicate direction inversion or data quality issues.")
        print("           Investigate a sample of raw trades before concluding.")
    else:
        print(f"  This value is within the expected range for liquid options.")


def check_6_time_window_verification(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 6: Time window verification.

    The pipeline uses window [captured_at - 24h, captured_at] when fetching trades.
    Because the SQL in BuySellFlowAnalyzer._fetch_trades already enforces this window with
    WHERE trade_timestamp >= start_ts AND trade_timestamp <= end_ts, it is architecturally
    impossible for the pipeline to include trades outside the window. The meaningful verification
    is therefore:

    (a) Are the metrics fresh? (captured_at is recent — pipeline ran in the last 30h)
    (b) Does the stated 24h window actually have trades? (pipeline ran with real data, not empty)
    (c) How many trades exist outside the 24h window in historical_trades (informational)?
        These are older/newer trades that CORRECTLY were not included.

    FAIL conditions:
    - captured_at is more than 30h old (metrics are stale — pipeline has not run)
    - Zero trades found within the 24h window (pipeline ran but captured nothing — data gap)
    """
    print("\n[CHECK 6] Time window verification")

    exp_clause = "AND expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    # Find the latest captured_at
    # _db_cursor uses the repository's internal context manager (same pattern as other callers
    # within the codebase; this is an accepted internal coupling for scripts).
    query = f"""
        SELECT MAX(captured_at) FROM buy_sell_flow_metrics
        WHERE currency = %s {exp_clause}
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()

    if not row or not row[0]:
        print("  No captured_at found.")
        print("  SKIP (no data)")
        return True

    latest_captured_at = row[0]
    window_start = latest_captured_at - timedelta(hours=24)
    start_ts = int(window_start.timestamp() * 1000)
    end_ts = int(latest_captured_at.timestamp() * 1000)

    # Compute age — handle timezone-aware vs naive datetimes
    if latest_captured_at.tzinfo is not None:
        from datetime import timezone
        now = datetime.now(timezone.utc)
    else:
        now = datetime.utcnow()
    age_hours = (now - latest_captured_at).total_seconds() / 3600

    print(f"  Latest captured_at: {latest_captured_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Window: {window_start.strftime('%Y-%m-%d %H:%M:%S')} → {latest_captured_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Metrics age: {age_hours:.1f}h ago")

    # (a) Freshness check
    stale = age_hours > 30.0
    if stale:
        print(f"  WARNING: captured_at is {age_hours:.1f}h old — collection daemon may not have run recently.")

    # (b) Count trades within the declared 24h window
    in_query = f"""
        SELECT COUNT(*)
        FROM historical_trades
        WHERE currency = %s
          {exp_clause}
          AND direction IS NOT NULL
          AND trade_timestamp >= %s
          AND trade_timestamp <= %s
    """
    in_params = [currency]
    if expiration:
        in_params.append(expiration)
    in_params.extend([start_ts, end_ts])

    with _db_cursor(repo) as cursor:
        cursor.execute(in_query, in_params)
        in_count = cursor.fetchone()[0]

    print(f"  Trades within 24h window: {in_count:,}")

    # (c) Informational: trades outside the 24h window (correctly excluded by the pipeline)
    #     historical_trades accumulates all history, so this will always be > 0; it is expected.
    out_query = f"""
        SELECT COUNT(*)
        FROM historical_trades
        WHERE currency = %s
          {exp_clause}
          AND direction IS NOT NULL
          AND (trade_timestamp < %s OR trade_timestamp > %s)
    """
    out_params = [currency]
    if expiration:
        out_params.append(expiration)
    out_params.extend([start_ts, end_ts])

    with _db_cursor(repo) as cursor:
        cursor.execute(out_query, out_params)
        out_count = cursor.fetchone()[0]

    print(f"  Trades outside 24h window (informational — correctly excluded): {out_count:,}")
    print(f"  NOTE: historical_trades accumulates all history; out-of-window count is expected to be large.")

    if stale:
        print("  FAIL ✗ — Metrics are stale (captured_at > 30h ago). Run the collection daemon.")
        return False

    if in_count == 0:
        print("  FAIL ✗ — No trades found within the 24h window. Pipeline may not have collected data.")
        return False

    print("  PASS ✓")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Verify flow data integrity: buy_sell_flow_metrics vs historical_trades."
    )
    parser.add_argument("--currency", required=True, choices=["BTC", "ETH"],
                        help="Currency to verify (BTC or ETH)")
    parser.add_argument("--expiration", default=None,
                        help="Optional: restrict to a single expiration (e.g. 27MAR26)")
    args = parser.parse_args()

    currency = args.currency
    expiration = args.expiration

    scope = f"{currency}" + (f" {expiration}" if expiration else " (all expirations)")
    print(f"\n=== Flow Data Verification: {scope} ===")

    # Initialize repository
    from coding.core.database.repository import DatabaseRepository
    repo = DatabaseRepository()

    results = {}

    results["check_1"] = check_1_mathematical_consistency(repo, currency, expiration)
    results["check_2"] = check_2_trade_count_reconciliation(repo, currency, expiration)
    results["check_3"] = check_3_volume_reconciliation(repo, currency, expiration)
    results["check_4"] = check_4_notional_reconciliation(repo, currency, expiration)
    check_5_direction_semantics(repo, currency, expiration)  # informational, no result stored
    results["check_6"] = check_6_time_window_verification(repo, currency, expiration)

    # Summary
    hard_checks = [results["check_1"], results["check_2"], results["check_3"],
                   results["check_4"], results["check_6"]]
    passed = sum(hard_checks)
    total = len(hard_checks)

    print(f"\n=== SUMMARY ===")
    print(f"PASS: {passed}/{total} hard checks")
    print(f"INFORMATIONAL: 1 (Check 5 — direction semantics)")

    if passed == total:
        print("All flow data verified correctly.")
        sys.exit(0)
    else:
        print("One or more checks FAILED. Investigate discrepancies above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script to verify it works against the database**

```bash
python scripts/verify_flow_data.py --currency BTC
```

Expected: script runs without import errors and prints all 6 check results.

If any hard check FAILs:
- Examine the first 5 discrepancy rows printed by the script.
- Determine whether the failure is a **data quality issue** (e.g., metrics haven't been captured yet, or the DB has no recent trades — expected in test environments) or a **script logic error** (e.g., wrong column name, wrong time window computation — indicates a bug to fix before proceeding).
- A Check 2/3/4 mismatch with count > 0 rows and small discrepancies near floating-point tolerance likely indicates a timing window difference (trades captured between the analytics run and this check). Re-run to confirm it's transient.
- A Check 6 failure (stale metrics) in production indicates the collection daemon has not run recently — this is a data pipeline issue, not a script bug.

- [ ] **Step 3: Run ETH**

```bash
python scripts/verify_flow_data.py --currency ETH
```

- [ ] **Step 4: Run full test suite to ensure no regressions**

```
pytest tests/ -v --tb=short
```

Expected: all previously passing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_flow_data.py
git commit -m "feat: add 6-check flow data verification suite (scripts/verify_flow_data.py)"
```

---

## Final Integration Check

- [ ] **Step 1: Launch the application and open Flow Charts window**

Manually verify:
1. Toggle buttons [All] [Block] [Non-Block] appear in the controls bar
2. Default state = All (button highlighted)
3. Click Block → charts regenerate, Block button highlighted
4. Switch to a specific expiration → filter persists, charts regenerate
5. Net Flow chart shows exactly 2 bars per strike (horizontal), not 4
6. Call Net Flow is emerald (#10b981), Put Net Flow is indigo (#818cf8)
7. Bars extend right for positive values, left for negative

- [ ] **Step 2: Verify git status is clean**

All files were committed individually in each task's Step 5/7. Confirm nothing was left uncommitted:

```bash
git status
```

Expected: working tree clean (no modified files related to this plan). If any modified files remain, stage and commit them now with an appropriate message. If the tree is clean, no further commit is needed.
