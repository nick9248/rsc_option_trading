"""
Chart generator for on-chain analysis data visualization.

Uses Plotly to create interactive charts saved as HTML and PNG files.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger(__name__)

# Chart output directory
CHARTS_DIR = Path(__file__).parent.parent.parent.parent / "output" / "charts"

# JS injected into saved HTML files for legend hover highlighting
_HOVER_JS = """
<script>
(function() {
    var isHovering = false;

    function setupLegendHover(plotDiv) {
        var legendItems = plotDiv.querySelectorAll('.legend .traces');
        if (legendItems.length === 0) return;

        legendItems.forEach(function(item, legendIdx) {
            item.style.cursor = 'pointer';

            item.onmouseenter = function() {
                if (isHovering || !plotDiv.data) return;
                isHovering = true;
                var opacity = [];
                var visIdx = 0;
                for (var i = 0; i < plotDiv.data.length; i++) {
                    if (plotDiv.data[i].visible === false) {
                        opacity.push(1.0);
                    } else {
                        opacity.push(visIdx === legendIdx ? 1.0 : 0.15);
                        visIdx++;
                    }
                }
                Plotly.restyle(plotDiv, {'opacity': opacity});
                isHovering = false;
            };

            item.onmouseleave = function() {
                if (isHovering || !plotDiv.data) return;
                isHovering = true;
                var opacity = plotDiv.data.map(function() { return 1.0; });
                Plotly.restyle(plotDiv, {'opacity': opacity});
                isHovering = false;
            };
        });
    }

    function init(attempts) {
        attempts = attempts || 0;
        if (attempts > 40) return;
        var plotDiv = document.querySelector('.plotly-graph-div');
        if (!plotDiv || !plotDiv.data || !window.Plotly) {
            setTimeout(function() { init(attempts + 1); }, 150);
            return;
        }
        setupLegendHover(plotDiv);
        plotDiv.on('plotly_afterplot', function() {
            if (!isHovering) { setupLegendHover(plotDiv); }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(init, 200); });
    } else {
        setTimeout(init, 200);
    }
})();
</script>
"""


def inject_hover_js(html_path: Path) -> None:
    """Inject legend hover highlighting JS into a saved chart HTML file."""
    try:
        content = html_path.read_text(encoding="utf-8")
        if "</body>" in content:
            content = content.replace("</body>", _HOVER_JS + "\n</body>")
            html_path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to inject hover JS into {html_path}: {e}")


def ensure_charts_dir() -> Path:
    """Ensure the charts directory exists."""
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    return CHARTS_DIR


def get_chart_theme() -> Dict[str, Any]:
    """
    Get consistent dark theme for all charts.

    Returns:
        Dictionary with theme configuration.
    """
    return {
        "paper_bgcolor": "#1a1a1a",
        "plot_bgcolor": "#1a1a1a",
        "font": {"color": "#e0e0e0", "family": "Arial, sans-serif"},
        "title_font": {"size": 16, "color": "#ffffff"},
        "xaxis": {
            "gridcolor": "#333333",
            "linecolor": "#444444",
            "tickcolor": "#666666",
        },
        "yaxis": {
            "gridcolor": "#333333",
            "linecolor": "#444444",
            "tickcolor": "#666666",
        },
    }


def save_chart(fig: go.Figure, filename: str, subfolder: str = "", save_png: bool = True) -> str:
    """
    Save chart to HTML and optionally PNG.

    Args:
        fig: Plotly figure to save.
        filename: Base filename (without extension).
        subfolder: Subfolder within charts directory (e.g., 'max_pain', 'levels').
        save_png: Whether to also save as PNG.

    Returns:
        Path to saved HTML file.
    """
    charts_dir = ensure_charts_dir()

    if subfolder:
        target_dir = charts_dir / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = charts_dir

    html_path = target_dir / f"{filename}.html"

    fig.write_html(str(html_path))
    logger.info(f"Chart saved: {html_path}")

    if save_png:
        try:
            png_path = target_dir / f"{filename}.png"
            fig.write_image(str(png_path), width=1200, height=600, scale=2)
            logger.info(f"PNG saved: {png_path}")
        except Exception as e:
            logger.warning(f"Failed to save PNG: {e}")

    return str(html_path)


def generate_flow_distribution_chart(
    flow_data: Dict[str, Any],
    spot_price: float,
    currency: str,
    expiration: str
) -> go.Figure:
    """
    Generate buy/sell flow distribution chart per strike.

    Population-pyramid style with 4 bars per strike:
    - Calls on right (positive): Call Buy, Call Sell
    - Puts on left (negative): Put Buy, Put Sell
    - Toggle buttons for notional/volume/count (top-right)
    - Spot price annotation

    Args:
        flow_data: Result from BuySellFlowAnalyzer.calculate().
        spot_price: Current underlying spot price.
        currency: Currency symbol (BTC or ETH).
        expiration: Expiration date string.

    Returns:
        Plotly figure object.
    """
    per_strike_data = flow_data.get("flow_data", {})

    if not per_strike_data:
        logger.warning("No flow data for distribution chart")
        fig = go.Figure()
        fig.update_layout(
            title=f"No flow data available - {currency} {expiration}",
            **get_chart_theme()
        )
        return fig

    theme = get_chart_theme()
    strikes = sorted(per_strike_data.keys())

    def _f(d: dict, key: str) -> float:
        v = d.get(key)
        return float(v) if v is not None else 0.0

    call_buy_notional, call_sell_notional = [], []
    put_buy_notional,  put_sell_notional  = [], []
    call_buy_volume,   call_sell_volume   = [], []
    put_buy_volume,    put_sell_volume    = [], []
    call_buy_count,    call_sell_count    = [], []
    put_buy_count,     put_sell_count     = [], []

    for strike in strikes:
        strike_data = per_strike_data[strike]
        c = strike_data.get("C", {})
        p = strike_data.get("P", {})

        call_buy_notional.append(_f(c, "buy_notional"))
        call_sell_notional.append(_f(c, "sell_notional"))
        put_buy_notional.append(-_f(p, "buy_notional"))   # negative → left side
        put_sell_notional.append(-_f(p, "sell_notional"))

        call_buy_volume.append(_f(c, "buy_volume"))
        call_sell_volume.append(_f(c, "sell_volume"))
        put_buy_volume.append(-_f(p, "buy_volume"))
        put_sell_volume.append(-_f(p, "sell_volume"))

        call_buy_count.append(_f(c, "buy_count"))
        call_sell_count.append(_f(c, "sell_count"))
        put_buy_count.append(-_f(p, "buy_count"))
        put_sell_count.append(-_f(p, "sell_count"))

    strike_labels = [f"${s:,.0f}" for s in strikes]

    # Luxury jewel-tone palette
    C_BUY  = "#10b981"   # Emerald — call buying (bullish)
    C_SELL = "#f43f5e"   # Rose    — call selling
    P_BUY  = "#818cf8"   # Indigo  — put buying (bearish hedge)
    P_SELL = "#f59e0b"   # Amber   — put selling (bullish)

    fig = go.Figure()

    def _bar(y, x, name, color, visible, tmpl):
        return go.Bar(
            y=strike_labels, x=x, name=name, orientation="h",
            marker_color=color, marker_line=dict(color=color, width=0.3),
            opacity=0.88, visible=visible,
            hovertemplate=tmpl
        )

    # Notional traces (visible by default, indices 0-3)
    fig.add_trace(_bar(strike_labels, call_buy_notional,  "Call Buy",  C_BUY,  True,
                       "<b>%{y}</b><br>Call Buy: $%{x:,.0f}<extra></extra>"))
    fig.add_trace(_bar(strike_labels, call_sell_notional, "Call Sell", C_SELL, True,
                       "<b>%{y}</b><br>Call Sell: $%{x:,.0f}<extra></extra>"))
    fig.add_trace(_bar(strike_labels, put_buy_notional,   "Put Buy",   P_BUY,  True,
                       "<b>%{y}</b><br>Put Buy: $%{x:,.0f}<extra></extra>"))
    fig.add_trace(_bar(strike_labels, put_sell_notional,  "Put Sell",  P_SELL, True,
                       "<b>%{y}</b><br>Put Sell: $%{x:,.0f}<extra></extra>"))

    # Volume traces (hidden, indices 4-7)
    fig.add_trace(_bar(strike_labels, call_buy_volume,  "Call Buy",  C_BUY,  False,
                       f"<b>%{{y}}</b><br>Call Buy: %{{x:.4f}} {currency}<extra></extra>"))
    fig.add_trace(_bar(strike_labels, call_sell_volume, "Call Sell", C_SELL, False,
                       f"<b>%{{y}}</b><br>Call Sell: %{{x:.4f}} {currency}<extra></extra>"))
    fig.add_trace(_bar(strike_labels, put_buy_volume,   "Put Buy",   P_BUY,  False,
                       f"<b>%{{y}}</b><br>Put Buy: %{{x:.4f}} {currency}<extra></extra>"))
    fig.add_trace(_bar(strike_labels, put_sell_volume,  "Put Sell",  P_SELL, False,
                       f"<b>%{{y}}</b><br>Put Sell: %{{x:.4f}} {currency}<extra></extra>"))

    # Count traces (hidden, indices 8-11)
    fig.add_trace(_bar(strike_labels, call_buy_count,  "Call Buy",  C_BUY,  False,
                       "<b>%{y}</b><br>Call Buy: %{x:.0f} trades<extra></extra>"))
    fig.add_trace(_bar(strike_labels, call_sell_count, "Call Sell", C_SELL, False,
                       "<b>%{y}</b><br>Call Sell: %{x:.0f} trades<extra></extra>"))
    fig.add_trace(_bar(strike_labels, put_buy_count,   "Put Buy",   P_BUY,  False,
                       "<b>%{y}</b><br>Put Buy: %{x:.0f} trades<extra></extra>"))
    fig.add_trace(_bar(strike_labels, put_sell_count,  "Put Sell",  P_SELL, False,
                       "<b>%{y}</b><br>Put Sell: %{x:.0f} trades<extra></extra>"))

    V = [True,  True,  True,  True,  False, False, False, False, False, False, False, False]
    W = [False, False, False, False, True,  True,  True,  True,  False, False, False, False]
    X = [False, False, False, False, False, False, False, False, True,  True,  True,  True ]

    fig.update_layout(
        updatemenus=[dict(
            type="buttons", direction="left",
            buttons=[
                dict(args=[{"visible": V}], label="Notional ($)", method="restyle"),
                dict(args=[{"visible": W}], label="Volume",        method="restyle"),
                dict(args=[{"visible": X}], label="Trade Count",   method="restyle"),
            ],
            pad={"r": 10, "t": 10}, showactive=True,
            x=1.0, xanchor="right", y=1.08, yanchor="top",
            bgcolor="#2a2a2a", bordercolor="#555555",
            font=dict(color="#e0e0e0", size=12),
        )]
    )

    # Spot price annotation — safe for categorical axes
    closest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot_price))
    fig.add_annotation(
        x=0, y=strike_labels[closest_idx],
        text=f"Spot ${spot_price:,.0f}",
        showarrow=True, arrowhead=2,
        arrowcolor="#fbbf24", arrowwidth=1.5,
        font=dict(color="#fbbf24", size=11),
        ax=-50, ay=0,
        bgcolor="rgba(26,26,26,0.75)",
        bordercolor="#fbbf24", borderwidth=1,
    )

    fig.update_layout(
        title=f"Buy/Sell Flow Distribution - {currency} {expiration}",
        xaxis_title="Flow (Put ← → Call)",
        yaxis_title="Strike Price",
        barmode="group",
        hovermode="y unified",
        autosize=True,
        margin=dict(t=80, r=20, b=40, l=80),
        legend=dict(
            itemclick="toggleothers", itemdoubleclick="toggle",
            x=1.02, y=1, xanchor="left", yanchor="top",
            bgcolor="rgba(26,26,26,0.85)",
            bordercolor="#444444", borderwidth=1,
        ),
        **theme
    )

    return fig


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

    # Spot price — find nearest label (annotation added after update_layout)
    spot_label = None
    if strikes:
        nearest_strike = min(strikes, key=lambda s: abs(s - spot_price))
        spot_label = f"${nearest_strike:,.0f}"

    fig.update_layout(
        title=f"Net Flow by Strike (Buy Vol − Sell Vol) — {currency} {expiration}",
        xaxis_title=f"Net Flow ({currency})  ·  ← selling  |  buying →",
        yaxis_title="Strike Price",
        barmode="group",  # group: parallel bars per strike — one for calls, one for puts
        hovermode="y unified",
        autosize=True,
        legend=dict(
            orientation="v",
            xanchor="right",
            yanchor="top",
            x=1.0,
            y=1.0,
            itemclick="toggleothers",
            itemdoubleclick="toggle",
        ),
        **theme
    )

    # Subtitle annotation — after update_layout so it appends cleanly
    fig.add_annotation(
        text="Net = Buy Volume − Sell Volume",
        xref="paper", yref="paper",
        x=0.0, y=1.04,
        showarrow=False,
        font=dict(color="#888888", size=11),
        xanchor="left",
    )

    # Spot price annotation — after update_layout
    if spot_label and spot_label in strike_labels:
        fig.add_annotation(
            x=0,
            y=spot_label,
            xref="x",
            yref="y",
            text=f"← Spot ~${spot_price:,.0f}",
            showarrow=False,
            font=dict(color="#ffd93d", size=11),
            xanchor="left",
            yanchor="middle",
        )

    return fig


def generate_flow_trend_chart(
    repository: Any,
    currency: str,
    expiration: Optional[str] = None,
    lookback_days: int = 7,
    trade_filter: str = "all",
) -> go.Figure:
    """
    Generate hourly flow trend chart over time.

    Shows:
    - Call buy/sell volume (green/red solid lines)
    - Put buy/sell volume (green/red dashed lines)
    - Net total flow (blue thick line)

    Args:
        repository: DatabaseRepository instance for querying trades.
        currency: Currency symbol (BTC or ETH).
        expiration: Expiration date string. When None, aggregates across all expirations.
        lookback_days: Number of days to look back (default: 7).
        trade_filter: Filter trades by size. "block" = notional >= $100k,
            "non_block" = notional < $100k, "all" = no filter (default).

    Returns:
        Plotly figure object.
    """
    display_label = expiration if expiration else "All Expirations"

    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=lookback_days)

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)

    results = repository.get_hourly_flow_volumes(
        currency=currency,
        start_ts=start_ts,
        end_ts=end_ts,
        expiration=expiration,
        trade_filter=trade_filter,
    )

    if not results:
        logger.warning(f"No hourly flow data for {currency} {display_label}")
        fig = go.Figure()
        fig.update_layout(
            title=f"No flow trend data available - {currency} {display_label}",
            **get_chart_theme()
        )
        return fig

    # Organize data by hour
    hourly_data = defaultdict(lambda: {
        "call_buy": 0.0,
        "call_sell": 0.0,
        "put_buy": 0.0,
        "put_sell": 0.0
    })

    for hour, option_type, direction, volume in results:
        if option_type == "C":
            if direction == "buy":
                hourly_data[hour]["call_buy"] += float(volume)
            else:
                hourly_data[hour]["call_sell"] += float(volume)
        else:  # option_type == "P"
            if direction == "buy":
                hourly_data[hour]["put_buy"] += float(volume)
            else:
                hourly_data[hour]["put_sell"] += float(volume)

    # Sort by time
    hours = sorted(hourly_data.keys())

    call_buy = [hourly_data[h]["call_buy"] for h in hours]
    call_sell = [hourly_data[h]["call_sell"] for h in hours]
    put_buy = [hourly_data[h]["put_buy"] for h in hours]
    put_sell = [hourly_data[h]["put_sell"] for h in hours]

    # Calculate net flow
    net_flow = [
        (hourly_data[h]["call_buy"] + hourly_data[h]["put_buy"]) -
        (hourly_data[h]["call_sell"] + hourly_data[h]["put_sell"])
        for h in hours
    ]

    theme = get_chart_theme()
    fig = go.Figure()

    # Call buy volume
    fig.add_trace(go.Scatter(
        x=hours,
        y=call_buy,
        name="Call Buy",
        mode="lines",
        line=dict(color="#10b981", width=2),  # Emerald-500
        hovertemplate="<b>%{x}</b><br>Call Buy: %{y:.4f} " + currency + "<extra></extra>"
    ))

    # Call sell volume
    fig.add_trace(go.Scatter(
        x=hours,
        y=call_sell,
        name="Call Sell",
        mode="lines",
        line=dict(color="#f43f5e", width=2),  # Rose-500
        hovertemplate="<b>%{x}</b><br>Call Sell: %{y:.4f} " + currency + "<extra></extra>"
    ))

    # Put buy volume
    fig.add_trace(go.Scatter(
        x=hours,
        y=put_buy,
        name="Put Buy",
        mode="lines",
        line=dict(color="#a78bfa", width=2, dash="dash"),  # Violet-400
        hovertemplate="<b>%{x}</b><br>Put Buy: %{y:.4f} " + currency + "<extra></extra>"
    ))

    # Put sell volume
    fig.add_trace(go.Scatter(
        x=hours,
        y=put_sell,
        name="Put Sell",
        mode="lines",
        line=dict(color="#fb923c", width=2, dash="dash"),  # Orange-400
        hovertemplate="<b>%{x}</b><br>Put Sell: %{y:.4f} " + currency + "<extra></extra>"
    ))

    # Net total flow
    fig.add_trace(go.Scatter(
        x=hours,
        y=net_flow,
        name="Net Flow",
        mode="lines",
        line=dict(color="#60a5fa", width=3),  # Blue-400
        hovertemplate="<b>%{x}</b><br>Net Flow: %{y:.4f} " + currency + "<extra></extra>"
    ))

    # Add zero line
    fig.add_hline(
        y=0,
        line_dash="dot",
        line_color="#666666",
        annotation_text="Balance",
        annotation_position="right"
    )

    fig.update_layout(
        title=f"Flow Trend Over Time - {currency} {display_label}",
        xaxis_title="Time",
        yaxis_title=f"Volume ({currency})",
        hovermode="x unified",
        autosize=True,  # Let chart resize to container
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            itemclick="toggleothers",  # Click to isolate trace
            itemdoubleclick="toggle",  # Double-click to toggle trace
        ),
        **theme
    )

    return fig
