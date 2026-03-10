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


def generate_max_pain_trend(
    data: List[Dict[str, Any]],
    currency: str,
    expiration: str
) -> str:
    """
    Generate max pain trend chart with underlying price overlay.

    Args:
        data: List of max pain records from database.
        currency: Currency symbol.
        expiration: Expiration date string.

    Returns:
        Path to saved chart.
    """
    if not data:
        logger.warning("No data for max pain trend chart")
        return ""

    theme = get_chart_theme()

    timestamps = [d["captured_at"] for d in data]
    max_pain_values = [float(d["max_pain_strike"]) for d in data]
    underlying_values = [float(d["underlying_price"]) for d in data]

    fig = go.Figure()

    # Max Pain line
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=max_pain_values,
        mode="lines+markers",
        name="Max Pain",
        line={"color": "#ff6b6b", "width": 2},
        marker={"size": 6},
    ))

    # Underlying Price line
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=underlying_values,
        mode="lines+markers",
        name="Underlying Price",
        line={"color": "#4ecdc4", "width": 2},
        marker={"size": 6},
    ))

    fig.update_layout(
        title=f"Max Pain Trend - {currency} {expiration}",
        xaxis_title="Time",
        yaxis_title="Price ($)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        hovermode="x unified",
        **theme
    )

    filename = f"max_pain_trend_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"max_pain/{expiration}")


def generate_open_interest_trend(
    data: List[Dict[str, Any]],
    currency: str,
    expiration: str
) -> str:
    """
    Generate open interest trend chart.

    Args:
        data: List of OI records from database.
        currency: Currency symbol.
        expiration: Expiration date string.

    Returns:
        Path to saved chart.
    """
    if not data:
        logger.warning("No data for OI trend chart")
        return ""

    theme = get_chart_theme()

    timestamps = [d["captured_at"] for d in data]
    call_oi = [float(d["total_call_oi"]) for d in data]
    put_oi = [float(d["total_put_oi"]) for d in data]
    total_oi = [float(d["total_oi"]) for d in data]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        subplot_titles=("Open Interest by Type", "Total Open Interest"),
        row_heights=[0.6, 0.4]
    )

    # Call OI
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=call_oi,
        mode="lines+markers",
        name="Call OI",
        line={"color": "#4ecdc4", "width": 2},
        fill="tozeroy",
        fillcolor="rgba(78, 205, 196, 0.2)",
    ), row=1, col=1)

    # Put OI
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=put_oi,
        mode="lines+markers",
        name="Put OI",
        line={"color": "#ff6b6b", "width": 2},
        fill="tozeroy",
        fillcolor="rgba(255, 107, 107, 0.2)",
    ), row=1, col=1)

    # Total OI
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=total_oi,
        mode="lines+markers",
        name="Total OI",
        line={"color": "#ffd93d", "width": 2},
    ), row=2, col=1)

    fig.update_layout(
        title=f"Open Interest Trend - {currency} {expiration}",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        **theme
    )

    fig.update_yaxes(title_text="Open Interest", row=1, col=1)
    fig.update_yaxes(title_text="Total OI", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)

    filename = f"oi_trend_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"open_interest/{expiration}")


def generate_pc_ratio_trend(
    data: List[Dict[str, Any]],
    currency: str,
    expiration: str
) -> str:
    """
    Generate Put/Call ratio trend chart.

    Args:
        data: List of OI records from database.
        currency: Currency symbol.
        expiration: Expiration date string.

    Returns:
        Path to saved chart.
    """
    if not data:
        logger.warning("No data for P/C ratio trend chart")
        return ""

    theme = get_chart_theme()

    timestamps = [d["captured_at"] for d in data]
    pc_ratios = [float(d["put_call_ratio"]) if d["put_call_ratio"] else 0 for d in data]

    fig = go.Figure()

    # P/C Ratio line
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=pc_ratios,
        mode="lines+markers",
        name="P/C Ratio",
        line={"color": "#9b59b6", "width": 2},
        marker={"size": 6},
        fill="tozeroy",
        fillcolor="rgba(155, 89, 182, 0.2)",
    ))

    # Add reference lines for sentiment zones
    fig.add_hline(y=1.0, line_dash="dash", line_color="#888888",
                  annotation_text="Neutral (1.0)")
    fig.add_hline(y=0.7, line_dash="dot", line_color="#4ecdc4",
                  annotation_text="Bullish (<0.7)")
    fig.add_hline(y=1.3, line_dash="dot", line_color="#ff6b6b",
                  annotation_text="Bearish (>1.3)")

    fig.update_layout(
        title=f"Put/Call Ratio Trend - {currency} {expiration}",
        xaxis_title="Time",
        yaxis_title="P/C Ratio",
        hovermode="x unified",
        **theme
    )

    filename = f"pc_ratio_trend_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"pc_ratio/{expiration}")


def generate_volume_trend(
    data: List[Dict[str, Any]],
    currency: str,
    expiration: str
) -> str:
    """
    Generate volume trend chart.

    Args:
        data: List of volume records from database.
        currency: Currency symbol.
        expiration: Expiration date string.

    Returns:
        Path to saved chart.
    """
    if not data:
        logger.warning("No data for volume trend chart")
        return ""

    theme = get_chart_theme()

    timestamps = [d["captured_at"] for d in data]
    call_vol = [float(d["total_call_volume"]) for d in data]
    put_vol = [float(d["total_put_volume"]) for d in data]

    fig = go.Figure()

    # Call Volume bars
    fig.add_trace(go.Bar(
        x=timestamps,
        y=call_vol,
        name="Call Volume",
        marker_color="#4ecdc4",
        opacity=0.8,
    ))

    # Put Volume bars
    fig.add_trace(go.Bar(
        x=timestamps,
        y=put_vol,
        name="Put Volume",
        marker_color="#ff6b6b",
        opacity=0.8,
    ))

    fig.update_layout(
        title=f"Volume Trend - {currency} {expiration}",
        xaxis_title="Time",
        yaxis_title="Volume",
        barmode="group",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        **theme
    )

    filename = f"volume_trend_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"volume/{expiration}")


def generate_levels_trend(
    data: List[Dict[str, Any]],
    currency: str,
    expiration: str
) -> str:
    """
    Generate support/resistance levels trend chart.

    Args:
        data: List of level records from database.
        currency: Currency symbol.
        expiration: Expiration date string.

    Returns:
        Path to saved chart.
    """
    if not data:
        logger.warning("No data for levels trend chart")
        return ""

    theme = get_chart_theme()

    # Group data by level_type
    level_types = {}
    for d in data:
        lt = d["level_type"]
        if lt not in level_types:
            level_types[lt] = {"timestamps": [], "strikes": []}
        level_types[lt]["timestamps"].append(d["captured_at"])
        level_types[lt]["strikes"].append(float(d["strike"]))

    fig = go.Figure()

    # Color mapping for different level types
    colors = {
        "resistance_1": "#ff6b6b",
        "resistance_2": "#ff8e8e",
        "resistance_3": "#ffb3b3",
        "support_1": "#4ecdc4",
        "support_2": "#7ed9d3",
        "support_3": "#a8e6e2",
        "short_term_resistance": "#e74c3c",
        "short_term_support": "#1abc9c",
        "call_resistance": "#e67e22",
        "put_support": "#3498db",
        "hvl_zero_gamma": "#f39c12",
    }

    for level_type, values in level_types.items():
        color = colors.get(level_type, "#888888")
        fig.add_trace(go.Scatter(
            x=values["timestamps"],
            y=values["strikes"],
            mode="lines+markers",
            name=level_type.replace("_", " ").title(),
            line={"color": color, "width": 2},
            marker={"size": 6},
        ))

    # Add underlying price if available
    underlying_prices = [float(d["underlying_price"]) for d in data if d.get("underlying_price")]
    if underlying_prices:
        timestamps = [d["captured_at"] for d in data if d.get("underlying_price")]
        # Get unique timestamps and corresponding prices
        unique_data = {}
        for i, ts in enumerate(timestamps):
            if ts not in unique_data:
                unique_data[ts] = underlying_prices[i]

        fig.add_trace(go.Scatter(
            x=list(unique_data.keys()),
            y=list(unique_data.values()),
            mode="lines",
            name="Underlying Price",
            line={"color": "#ffd93d", "width": 2, "dash": "dash"},
        ))

    fig.update_layout(
        title=f"Support/Resistance Levels Trend - {currency} {expiration}",
        xaxis_title="Time",
        yaxis_title="Strike Price ($)",
        hovermode="x unified",
        legend={"orientation": "v", "yanchor": "top", "y": 1, "xanchor": "left", "x": 1.02},
        **theme
    )

    filename = f"levels_trend_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"levels/{expiration}")


def generate_oi_distribution(
    strike_data: Dict[float, Dict[str, float]],
    currency: str,
    expiration: str,
    underlying_price: float
) -> str:
    """
    Generate OI distribution by strike chart (bar chart).

    Args:
        strike_data: Dict mapping strike -> {call_oi, put_oi}.
        currency: Currency symbol.
        expiration: Expiration date string.
        underlying_price: Current underlying price.

    Returns:
        Path to saved chart.
    """
    if not strike_data:
        logger.warning("No data for OI distribution chart")
        return ""

    theme = get_chart_theme()

    strikes = sorted(strike_data.keys())
    call_oi = [strike_data[s]["call_oi"] for s in strikes]
    put_oi = [-strike_data[s]["put_oi"] for s in strikes]  # Negative for left side

    fig = go.Figure()

    # Call OI (right side - positive)
    fig.add_trace(go.Bar(
        y=[f"${s:,.0f}" for s in strikes],
        x=call_oi,
        name="Call OI",
        orientation="h",
        marker_color="#4ecdc4",
    ))

    # Put OI (left side - negative)
    fig.add_trace(go.Bar(
        y=[f"${s:,.0f}" for s in strikes],
        x=put_oi,
        name="Put OI",
        orientation="h",
        marker_color="#ff6b6b",
    ))

    # Add vertical line for current price
    # Find closest strike to underlying price for annotation
    closest_strike_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - underlying_price))

    fig.add_annotation(
        x=0,
        y=closest_strike_idx,
        text=f"Current: ${underlying_price:,.0f}",
        showarrow=True,
        arrowhead=2,
        arrowcolor="#ffd93d",
        font={"color": "#ffd93d"},
    )

    fig.update_layout(
        title=f"OI Distribution by Strike - {currency} {expiration}",
        xaxis_title="Open Interest (Put ← → Call)",
        yaxis_title="Strike Price",
        barmode="overlay",
        hovermode="y unified",
        **theme
    )

    filename = f"oi_distribution_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"open_interest/{expiration}")


def generate_gex_dex_trend(
    data: List[Dict[str, Any]],
    currency: str,
    expiration: str
) -> str:
    """
    Generate GEX/DEX trend chart with key levels.

    Args:
        data: List of GEX/DEX records from database.
        currency: Currency symbol.
        expiration: Expiration date string.

    Returns:
        Path to saved chart.
    """
    if not data:
        logger.warning("No data for GEX/DEX trend chart")
        return ""

    theme = get_chart_theme()

    timestamps = [d["captured_at"] for d in data]
    total_gex = [float(d["total_net_gex"]) if d["total_net_gex"] else 0 for d in data]
    total_dex = [float(d["total_net_dex"]) if d["total_net_dex"] else 0 for d in data]
    hvl_strikes = [float(d["hvl_strike"]) if d["hvl_strike"] else None for d in data]
    underlying = [float(d["underlying_price"]) if d["underlying_price"] else 0 for d in data]
    call_res = [float(d["call_resistance_strike"]) if d["call_resistance_strike"] else None for d in data]
    put_sup = [float(d["put_support_strike"]) if d["put_support_strike"] else None for d in data]

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Net GEX (Gamma Exposure)", "Net DEX (Delta Exposure)", "Key Levels vs Price"),
        row_heights=[0.33, 0.33, 0.34]
    )

    # Row 1: Net GEX
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=total_gex,
        mode="lines+markers",
        name="Net GEX",
        line={"color": "#f39c12", "width": 2},
        fill="tozeroy",
        fillcolor="rgba(243, 156, 18, 0.2)",
    ), row=1, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="#666666", row=1, col=1)

    # Row 2: Net DEX
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=total_dex,
        mode="lines+markers",
        name="Net DEX",
        line={"color": "#9b59b6", "width": 2},
        fill="tozeroy",
        fillcolor="rgba(155, 89, 182, 0.2)",
    ), row=2, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="#666666", row=2, col=1)

    # Row 3: Key Levels
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=underlying,
        mode="lines",
        name="Price",
        line={"color": "#ffd93d", "width": 2},
    ), row=3, col=1)

    fig.add_trace(go.Scatter(
        x=timestamps,
        y=hvl_strikes,
        mode="lines+markers",
        name="HVL (Zero Gamma)",
        line={"color": "#e74c3c", "width": 2, "dash": "dot"},
        marker={"size": 6},
    ), row=3, col=1)

    fig.add_trace(go.Scatter(
        x=timestamps,
        y=call_res,
        mode="lines+markers",
        name="Call Resistance",
        line={"color": "#4ecdc4", "width": 1, "dash": "dash"},
        marker={"size": 4},
    ), row=3, col=1)

    fig.add_trace(go.Scatter(
        x=timestamps,
        y=put_sup,
        mode="lines+markers",
        name="Put Support",
        line={"color": "#ff6b6b", "width": 1, "dash": "dash"},
        marker={"size": 4},
    ), row=3, col=1)

    fig.update_layout(
        title=f"GEX/DEX Trend - {currency} {expiration}",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        height=550,
        **theme
    )

    fig.update_yaxes(title_text="Net GEX", row=1, col=1)
    fig.update_yaxes(title_text="Net DEX", row=2, col=1)
    fig.update_yaxes(title_text="Strike ($)", row=3, col=1)
    fig.update_xaxes(title_text="Time", row=3, col=1)

    filename = f"gex_dex_trend_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return save_chart(fig, filename, subfolder=f"gex_dex/{expiration}")


def generate_snapshot_oi_distribution(
    strike_data: Dict[float, Dict[str, float]],
    currency: str,
    expiration: str,
    underlying_price: float,
    max_pain_strike: float
) -> str:
    """
    Generate snapshot OI distribution chart (bar chart by strike).

    Args:
        strike_data: Dict mapping strike -> {call_oi, put_oi}.
        currency: Currency symbol.
        expiration: Expiration date string.
        underlying_price: Current underlying price.
        max_pain_strike: Max pain strike price.

    Returns:
        Path to saved chart.
    """
    if not strike_data:
        logger.warning("No data for snapshot OI distribution chart")
        return ""

    theme = get_chart_theme()

    strikes = sorted(strike_data.keys())
    call_oi = [strike_data[s].get("call_oi", 0) for s in strikes]
    put_oi = [strike_data[s].get("put_oi", 0) for s in strikes]

    fig = go.Figure()

    # Call OI bars (green)
    fig.add_trace(go.Bar(
        x=strikes,
        y=call_oi,
        name="Call OI",
        marker_color="#4ecdc4",
        opacity=0.8,
    ))

    # Put OI bars (red)
    fig.add_trace(go.Bar(
        x=strikes,
        y=put_oi,
        name="Put OI",
        marker_color="#ff6b6b",
        opacity=0.8,
    ))

    # Add vertical line for underlying price
    fig.add_vline(
        x=underlying_price,
        line_dash="dash",
        line_color="#ffd93d",
        annotation_text=f"Price: ${underlying_price:,.0f}",
        annotation_position="top",
    )

    # Add vertical line for max pain
    fig.add_vline(
        x=max_pain_strike,
        line_dash="dot",
        line_color="#e74c3c",
        annotation_text=f"Max Pain: ${max_pain_strike:,.0f}",
        annotation_position="bottom",
    )

    fig.update_layout(
        title=f"OI Distribution by Strike - {currency} {expiration}",
        xaxis_title="Strike Price ($)",
        yaxis_title="Open Interest",
        barmode="group",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        **theme
    )

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"oi_snapshot_{currency}_{timestamp}"
    return save_chart(fig, filename, subfolder=f"snapshot/{expiration}")


def generate_snapshot_volume_distribution(
    strike_data: Dict[float, Dict[str, float]],
    currency: str,
    expiration: str,
    underlying_price: float
) -> str:
    """
    Generate snapshot volume distribution chart (bar chart by strike).

    Args:
        strike_data: Dict mapping strike -> {call_volume, put_volume}.
        currency: Currency symbol.
        expiration: Expiration date string.
        underlying_price: Current underlying price.

    Returns:
        Path to saved chart.
    """
    if not strike_data:
        logger.warning("No data for snapshot volume distribution chart")
        return ""

    theme = get_chart_theme()

    strikes = sorted(strike_data.keys())
    call_vol = [strike_data[s].get("call_volume", 0) for s in strikes]
    put_vol = [strike_data[s].get("put_volume", 0) for s in strikes]

    # Filter out strikes with zero volume on both sides
    non_zero_indices = [i for i in range(len(strikes)) if call_vol[i] > 0 or put_vol[i] > 0]
    if not non_zero_indices:
        logger.warning("No volume data to chart")
        return ""

    strikes = [strikes[i] for i in non_zero_indices]
    call_vol = [call_vol[i] for i in non_zero_indices]
    put_vol = [put_vol[i] for i in non_zero_indices]

    fig = go.Figure()

    # Call Volume bars
    fig.add_trace(go.Bar(
        x=strikes,
        y=call_vol,
        name="Call Volume",
        marker_color="#4ecdc4",
        opacity=0.8,
    ))

    # Put Volume bars
    fig.add_trace(go.Bar(
        x=strikes,
        y=put_vol,
        name="Put Volume",
        marker_color="#ff6b6b",
        opacity=0.8,
    ))

    # Add vertical line for underlying price
    fig.add_vline(
        x=underlying_price,
        line_dash="dash",
        line_color="#ffd93d",
        annotation_text=f"Price: ${underlying_price:,.0f}",
        annotation_position="top",
    )

    fig.update_layout(
        title=f"Volume Distribution by Strike - {currency} {expiration}",
        xaxis_title="Strike Price ($)",
        yaxis_title="Volume",
        barmode="group",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        **theme
    )

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"volume_snapshot_{currency}_{timestamp}"
    return save_chart(fig, filename, subfolder=f"snapshot/{expiration}")


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
        barmode="relative",  # relative: signed bars extend in opposite directions from x=0 (diverging chart)
        hovermode="y unified",
        autosize=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.06,
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
    lookback_days: int = 7
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

    Returns:
        Plotly figure object.
    """
    display_label = expiration if expiration else "All Expirations"

    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=lookback_days)

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)

    # Query hourly aggregated data
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
