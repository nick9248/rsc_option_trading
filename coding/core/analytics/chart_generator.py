"""
Chart generator for on-chain analysis data visualization.

Uses Plotly to create interactive charts saved as HTML and PNG files.
"""

import json
import logging
import math
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


# Dark → light color equivalents for every hardcoded color used by charts
# that support the theme toggle (currently the straddle payoff chart). Keys
# are the exact dark-mode literals as they appear in figure JSON (hex or
# rgba() strings); a color not present here is left unchanged in light mode
# (this is correct for hues like amber/rose/blue/green that read fine on
# both backgrounds — only backgrounds, grays, borders and the near-white
# F-price marker need remapping).
_DARK_TO_LIGHT_COLORS: Dict[str, str] = {
    "#1a1a1a": "#ffffff",   # paper / plot background
    "#e0e0e0": "#1f2937",   # primary font color
    "#ffffff": "#1f2937",   # title font (pure white -> dark slate)
    "#9ca3af": "#6b7280",   # secondary text (legend, stats box, range labels, subtitle)
    "#666666": "#9ca3af",   # zero line / tick color
    "#444444": "#d1d5db",   # borders / axis linecolor
    "#333333": "#e5e7eb",   # grid lines
    "#e5e7eb": "#1f2937",   # F-price marker: near-white is invisible on white bg
    "#60a5fa": "#2563eb",   # P&L line: weak blue-400 -> stronger blue-600 on white
    "rgba(26,26,26,0.85)": "rgba(255,255,255,0.92)",  # stats box background
    "rgba(26,26,26,0.6)": "rgba(255,255,255,0.85)",   # marker-label background
    "rgba(26,26,26,0.75)": "rgba(255,255,255,0.90)",  # (other charts' annotation bg)
}

# JS injected into saved HTML files: a fixed top-right button that toggles
# the page between the saved dark theme and a light theme. The color
# mapping (__THEME_PAYLOAD_JSON__) is generated in Python from the actual
# figure at save time — see _build_theme_toggle_payload — so every
# annotations[i]/shapes[i]/trace-index path below is exact for this figure,
# never guessed.
_THEME_TOGGLE_JS_TEMPLATE = """
<script>
(function() {
    var THEME = __THEME_PAYLOAD_JSON__;
    var isLight = false;

    function applyTheme(plotDiv, light) {
        var relayout = {};

        function pick(pair) { return pair ? (light ? pair.light : pair.dark) : undefined; }
        function set(key, pair) { var v = pick(pair); if (v !== undefined) relayout[key] = v; }

        set('paper_bgcolor', THEME.paper_bgcolor);
        set('plot_bgcolor', THEME.plot_bgcolor);
        set('font.color', THEME.font_color);
        set('title.font.color', THEME.title_font_color);
        set('title.text', THEME.title_text);
        if (THEME.xaxis) {
            set('xaxis.gridcolor', THEME.xaxis.gridcolor);
            set('xaxis.linecolor', THEME.xaxis.linecolor);
            set('xaxis.tickcolor', THEME.xaxis.tickcolor);
        }
        if (THEME.yaxis) {
            set('yaxis.gridcolor', THEME.yaxis.gridcolor);
            set('yaxis.linecolor', THEME.yaxis.linecolor);
            set('yaxis.tickcolor', THEME.yaxis.tickcolor);
        }
        set('legend.font.color', THEME.legend_font_color);
        set('legend.bgcolor', THEME.legend_bgcolor);

        (THEME.annotations || []).forEach(function(a) {
            set('annotations[' + a.index + '].font.color', a.font_color);
            set('annotations[' + a.index + '].bgcolor', a.bgcolor);
            set('annotations[' + a.index + '].bordercolor', a.bordercolor);
        });

        (THEME.shapes || []).forEach(function(s) {
            set('shapes[' + s.index + '].line.color', s.line_color);
        });

        if (Object.keys(relayout).length > 0) {
            Plotly.relayout(plotDiv, relayout);
        }

        var restyleIdx = [];
        var restyleColors = [];
        (THEME.traces || []).forEach(function(t) {
            var v = pick(t.line_color);
            if (v !== undefined) { restyleIdx.push(t.index); restyleColors.push(v); }
        });
        if (restyleIdx.length > 0) {
            Plotly.restyle(plotDiv, {'line.color': restyleColors}, restyleIdx);
        }

        var bodyBg = pick(THEME.paper_bgcolor);
        if (bodyBg !== undefined) { document.body.style.background = bodyBg; }
    }

    function makeButton(plotDiv) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Light mode';
        btn.style.position = 'fixed';
        btn.style.top = '12px';
        btn.style.right = '12px';
        btn.style.zIndex = '9999';
        btn.style.padding = '6px 12px';
        btn.style.fontSize = '12px';
        btn.style.fontFamily = 'Arial, sans-serif';
        btn.style.borderRadius = '6px';
        btn.style.border = '1px solid #666666';
        btn.style.background = 'rgba(60,60,60,0.55)';
        btn.style.color = '#e0e0e0';
        btn.style.cursor = 'pointer';
        btn.style.transition = 'opacity 0.15s ease';
        btn.onmouseenter = function() { btn.style.opacity = '0.8'; };
        btn.onmouseleave = function() { btn.style.opacity = '1'; };
        btn.onclick = function() {
            isLight = !isLight;
            applyTheme(plotDiv, isLight);
            btn.textContent = isLight ? 'Dark mode' : 'Light mode';
            btn.style.border = isLight ? '1px solid #d1d5db' : '1px solid #666666';
            btn.style.background = isLight ? 'rgba(255,255,255,0.85)' : 'rgba(60,60,60,0.55)';
            btn.style.color = isLight ? '#1f2937' : '#e0e0e0';
        };
        document.body.appendChild(btn);
    }

    function init(attempts) {
        attempts = attempts || 0;
        if (attempts > 40) return;
        var plotDiv = document.querySelector('.plotly-graph-div');
        if (!plotDiv || !plotDiv.data || !window.Plotly) {
            setTimeout(function() { init(attempts + 1); }, 150);
            return;
        }
        makeButton(plotDiv);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(init, 200); });
    } else {
        setTimeout(init, 200);
    }
})();
</script>
"""


def _map_color(color: Optional[str]) -> Optional[str]:
    """Map a dark-mode color literal to its light-mode equivalent (identity if unmapped)."""
    if color is None:
        return None
    return _DARK_TO_LIGHT_COLORS.get(color, color)


def _map_title_text(text: Optional[str]) -> Optional[str]:
    """Rewrite inline HTML color hexes (e.g. a subtitle <span style='color:#9ca3af'>) to light equivalents."""
    if text is None:
        return None
    mapped = text
    for dark, light in _DARK_TO_LIGHT_COLORS.items():
        if dark.startswith("rgba"):
            continue
        mapped = mapped.replace(dark, light)
    return mapped


def _color_pair(dark_color: Optional[str]) -> Optional[Dict[str, str]]:
    """Build a {"dark": ..., "light": ...} pair from a color actually found on the figure."""
    if dark_color is None:
        return None
    return {"dark": dark_color, "light": _map_color(dark_color)}


def _build_theme_toggle_payload(fig: go.Figure) -> Dict[str, Any]:
    """
    Introspect a saved figure's actual layout/annotations/shapes/traces and
    build the dark<->light color mapping the toggle JS needs, keyed by the
    figure's real annotation/shape/trace indices.

    This is deliberately introspective rather than a static/guessed index
    list: if `generate_straddle_payoff_chart` ever adds, removes, or
    reorders an annotation or shape (e.g. the rv-band annotations that only
    exist when `rv` is provided), the indices here are still exactly right
    because they're read off `fig` itself at save time.

    Args:
        fig: The exact figure that was (or will be) saved to the HTML file
            this payload's JS will be injected into.

    Returns:
        JSON-serializable dict consumed by _THEME_TOGGLE_JS_TEMPLATE.
    """
    layout = fig.layout

    xaxis = layout.xaxis
    yaxis = layout.yaxis
    title = layout.title
    legend = layout.legend
    base_font = layout.font

    payload: Dict[str, Any] = {
        "paper_bgcolor": _color_pair(layout.paper_bgcolor),
        "plot_bgcolor": _color_pair(layout.plot_bgcolor),
        "font_color": _color_pair(base_font.color if base_font is not None else None),
        "title_font_color": _color_pair(
            title.font.color if title is not None and title.font is not None else None
        ),
        "title_text": {
            "dark": title.text if title is not None else None,
            "light": _map_title_text(title.text if title is not None else None),
        },
        "xaxis": {
            "gridcolor": _color_pair(xaxis.gridcolor if xaxis is not None else None),
            "linecolor": _color_pair(xaxis.linecolor if xaxis is not None else None),
            "tickcolor": _color_pair(xaxis.tickcolor if xaxis is not None else None),
        },
        "yaxis": {
            "gridcolor": _color_pair(yaxis.gridcolor if yaxis is not None else None),
            "linecolor": _color_pair(yaxis.linecolor if yaxis is not None else None),
            "tickcolor": _color_pair(yaxis.tickcolor if yaxis is not None else None),
        },
        "legend_font_color": _color_pair(
            legend.font.color if legend is not None and legend.font is not None else None
        ),
        "legend_bgcolor": _color_pair(legend.bgcolor if legend is not None else None),
        "annotations": [],
        "shapes": [],
        "traces": [],
    }

    for i, ann in enumerate(layout.annotations or []):
        entry: Dict[str, Any] = {"index": i}
        if ann.font is not None and ann.font.color is not None:
            entry["font_color"] = _color_pair(ann.font.color)
        if ann.bgcolor is not None:
            entry["bgcolor"] = _color_pair(ann.bgcolor)
        if ann.bordercolor is not None:
            entry["bordercolor"] = _color_pair(ann.bordercolor)
        if len(entry) > 1:
            payload["annotations"].append(entry)

    for i, shp in enumerate(layout.shapes or []):
        entry = {"index": i}
        if shp.line is not None and shp.line.color is not None:
            entry["line_color"] = _color_pair(shp.line.color)
        if len(entry) > 1:
            payload["shapes"].append(entry)

    for i, tr in enumerate(fig.data):
        line = getattr(tr, "line", None)
        if line is not None and line.color is not None:
            payload["traces"].append({"index": i, "line_color": _color_pair(line.color)})

    return payload


def inject_theme_toggle_js(html_path: Path, fig: go.Figure) -> None:
    """
    Inject a light/dark theme toggle button into a saved chart HTML file.

    The button is fixed top-right, styled unobtrusively for both themes,
    and defaults to the dark state the figure was saved in (nothing changes
    until the user clicks). Every color it can flip — backgrounds, fonts,
    axis grid/line/tick colors, legend, each annotation's font/bgcolor/
    bordercolor, each shape's line color, and the P&L trace's line color —
    is derived from the real `fig` passed in (see
    _build_theme_toggle_payload), so the injected `annotations[i]` /
    `shapes[i]` / trace-index paths are always exact for this figure.

    Args:
        html_path: Path to the already-saved chart HTML file.
        fig: The exact Plotly figure that was saved to `html_path`.
    """
    try:
        payload = _build_theme_toggle_payload(fig)
        js = _THEME_TOGGLE_JS_TEMPLATE.replace(
            "__THEME_PAYLOAD_JSON__", json.dumps(payload)
        )
        content = html_path.read_text(encoding="utf-8")
        if "</body>" in content:
            content = content.replace("</body>", js + "\n</body>")
            html_path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to inject theme toggle JS into {html_path}: {e}")


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


def _fmt_k(value: float) -> str:
    """Format a dollar value in compact $k notation, e.g. 54800 -> '$54.8k'."""
    return f"${value / 1000.0:,.1f}k"


def generate_straddle_payoff_chart(
    currency: str,
    expiry: str,
    dte: float,
    future_price: float,
    atm_iv: float,
    strike: float,
    cost_usd: float,
    breakeven_down: float,
    breakeven_up: float,
    rv: Optional[float] = None,
    iv_percentile: Optional[float] = None,
    vrp: Optional[float] = None,
) -> go.Figure:
    """
    Generate a long-straddle payoff-at-expiry chart.

    Pure function — takes the primitives already computed by
    StraddleScanService (no API/DB access here, per the Core layer rule).

    Layout:
      - fig.data[0] is the P&L curve (y = |S - K| - cost_usd), 2px neutral
        blue. Green/red area fills (added as separate showlegend=False
        traces right after it) mark the profit/loss polarity.
      - Zero-P&L line: thin solid gray, no text annotation (avoids edge
        clipping).
      - Two slim horizontal "range strips" reserved in a bottom sliver of
        the y-axis (below the lowest P&L value) show the IV-implied and
        realized-pace 1-sigma ranges — recessive, not full-height boxes.
      - Vertical dotted/dash-dot markers for strike, current F and both
        breakevens, with staggered small-font labels above the plot so
        nothing overlaps even when strike and F sit close together.
      - A stats box (top-right, inside the plot) with max loss, IV/RV and
        (when provided) IV percentile / VRP context.
      - Title is top-left with a smaller gray subtitle line; legend is
        horizontal, below the plot — the title and legend never compete
        for the same corner.

    Args:
        currency: Currency symbol (BTC, ETH, ...).
        expiry: Expiry label (e.g. "25SEP26").
        dte: Days to expiry (float).
        future_price: F — this expiry's future price (strike-space math).
        atm_iv: ATM implied vol, Deribit native percent units (e.g. 65.0).
        strike: Straddle strike.
        cost_usd: Total premium paid (both legs), USD.
        breakeven_down: strike - cost_usd.
        breakeven_up: strike + cost_usd.
        rv: Realized vol (percent units), or None if unavailable — the
            realized-pace range strip is omitted when None.
        iv_percentile: Per-expiry ATM-IV percentile, 0-100 scale, or None —
            shown in the stats box when provided.
        vrp: Variance risk premium (atm_iv - rv, percentage points), or
            None — shown in the stats box when provided.

    Returns:
        Plotly figure object.
    """
    theme = get_chart_theme()
    time_to_expiry_years = dte / 365.0

    iv_sigma_sqrt_t = (atm_iv / 100.0) * math.sqrt(max(time_to_expiry_years, 0.0))
    iv_range_lo = future_price / math.exp(iv_sigma_sqrt_t) if iv_sigma_sqrt_t > 0 else future_price
    iv_range_hi = future_price * math.exp(iv_sigma_sqrt_t) if iv_sigma_sqrt_t > 0 else future_price

    iv_dollar_sigma = future_price * iv_sigma_sqrt_t
    if iv_dollar_sigma <= 0:
        iv_dollar_sigma = future_price * 0.1  # fallback so the chart isn't degenerate

    x_lo = max(future_price - 2.5 * iv_dollar_sigma, future_price * 0.01)
    x_hi = future_price + 2.5 * iv_dollar_sigma

    steps = 400
    x_values = [x_lo + i * (x_hi - x_lo) / steps for i in range(steps + 1)]
    y_values = [abs(s - strike) - cost_usd for s in x_values]

    rv_range_lo = rv_range_hi = None
    if rv is not None:
        rv_sigma_sqrt_t = (rv / 100.0) * math.sqrt(max(time_to_expiry_years, 0.0))
        if rv_sigma_sqrt_t > 0:
            rv_range_lo = future_price / math.exp(rv_sigma_sqrt_t)
            rv_range_hi = future_price * math.exp(rv_sigma_sqrt_t)

    # ── y-axis layout: reserve a bottom sliver for the two range strips ────
    data_y_min = min(y_values)
    data_y_max = max(y_values)
    data_span = max(data_y_max - data_y_min, 1e-9)
    strip_zone_height = 0.22 * data_span
    y_axis_bottom = data_y_min - strip_zone_height
    y_axis_top = data_y_max + 0.08 * data_span

    IV_STRIP_COLOR = "#3b82f6"   # blue-500 — IV-implied range
    RV_STRIP_COLOR = "#22c55e"   # green-500 — realized-pace range
    iv_strip_y0 = y_axis_bottom + 0.58 * strip_zone_height
    iv_strip_y1 = y_axis_bottom + 0.80 * strip_zone_height
    rv_strip_y0 = y_axis_bottom + 0.16 * strip_zone_height
    rv_strip_y1 = y_axis_bottom + 0.38 * strip_zone_height

    fig = go.Figure()

    # ── data[0]: the P&L curve — invariant relied on by callers/tests ──────
    fig.add_trace(go.Scatter(
        x=x_values, y=y_values, mode="lines", name="P&L at expiry",
        line=dict(color="#60a5fa", width=2),
        hovertemplate="S = $%{x:,.0f}<br>P&L = $%{y:,.0f}<extra></extra>",
        showlegend=False,
    ))

    # ── polarity fills: profit (green) / loss (red), masked segments ───────
    # None gaps break the line/fill at the sign change so each region fills
    # to zero independently instead of one fill spanning the whole curve.
    profit_y = [y if y >= 0 else None for y in y_values]
    loss_y = [y if y < 0 else None for y in y_values]

    fig.add_trace(go.Scatter(
        x=x_values, y=profit_y, mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor="rgba(34,197,94,0.15)",
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=x_values, y=loss_y, mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor="rgba(239,68,68,0.12)",
        hoverinfo="skip", showlegend=False,
    ))

    # ── zero P&L line — thin solid gray, no annotation (avoids clipping) ───
    fig.add_shape(
        type="line", xref="x", yref="y",
        x0=x_lo, x1=x_hi, y0=0, y1=0,
        line=dict(color="#666666", width=1),
    )

    # ── range strips: slim, recessive, stacked in the reserved bottom zone ─
    fig.add_shape(
        type="rect", xref="x", yref="y",
        x0=iv_range_lo, x1=iv_range_hi, y0=iv_strip_y0, y1=iv_strip_y1,
        fillcolor=IV_STRIP_COLOR, opacity=0.5, line_width=0,
    )
    fig.add_annotation(
        x=iv_range_lo, y=(iv_strip_y0 + iv_strip_y1) / 2, xref="x", yref="y",
        text=_fmt_k(iv_range_lo), showarrow=False, xanchor="right", xshift=-4,
        font=dict(color="#9ca3af", size=10),
    )
    fig.add_annotation(
        x=iv_range_hi, y=(iv_strip_y0 + iv_strip_y1) / 2, xref="x", yref="y",
        text=_fmt_k(iv_range_hi), showarrow=False, xanchor="left", xshift=4,
        font=dict(color="#9ca3af", size=10),
    )
    fig.add_trace(go.Scatter(
        x=[None], y=[None], mode="lines",
        line=dict(color=IV_STRIP_COLOR, width=6),
        name="IV-implied 1σ range", showlegend=True,
    ))

    if rv_range_lo is not None and rv_range_hi is not None:
        fig.add_shape(
            type="rect", xref="x", yref="y",
            x0=rv_range_lo, x1=rv_range_hi, y0=rv_strip_y0, y1=rv_strip_y1,
            fillcolor=RV_STRIP_COLOR, opacity=0.5, line_width=0,
        )
        fig.add_annotation(
            x=rv_range_lo, y=(rv_strip_y0 + rv_strip_y1) / 2, xref="x", yref="y",
            text=_fmt_k(rv_range_lo), showarrow=False, xanchor="right", xshift=-4,
            font=dict(color="#9ca3af", size=10),
        )
        fig.add_annotation(
            x=rv_range_hi, y=(rv_strip_y0 + rv_strip_y1) / 2, xref="x", yref="y",
            text=_fmt_k(rv_range_hi), showarrow=False, xanchor="left", xshift=4,
            font=dict(color="#9ca3af", size=10),
        )
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            line=dict(color=RV_STRIP_COLOR, width=6),
            name="Realized-pace 1σ range", showlegend=True,
        ))

    # ── vertical markers: dotted/dash-dot lines, full data-y span ──────────
    def _marker_line(x: float, color: str, dash: str) -> None:
        fig.add_shape(
            type="line", xref="x", yref="y",
            x0=x, x1=x, y0=y_axis_bottom, y1=y_axis_top,
            line=dict(color=color, width=1.5, dash=dash),
        )

    STRIKE_COLOR = "#f59e0b"   # amber
    F_COLOR = "#e5e7eb"        # near-white
    BE_COLOR = "#fb7185"       # rose

    _marker_line(strike, STRIKE_COLOR, "dot")
    _marker_line(future_price, F_COLOR, "dot")
    _marker_line(breakeven_down, BE_COLOR, "dashdot")
    _marker_line(breakeven_up, BE_COLOR, "dashdot")

    # Staggered paper-space labels — two height tiers so nothing collides
    # even when strike and F sit only a small distance apart on the x-axis.
    down_pct = (breakeven_down / future_price - 1.0) * 100.0
    up_pct = (breakeven_up / future_price - 1.0) * 100.0

    def _marker_label(x: float, y: float, text: str, color: str) -> None:
        fig.add_annotation(
            x=x, y=y, xref="x", yref="paper",
            text=text, showarrow=False,
            font=dict(color=color, size=11),
            bgcolor="rgba(26,26,26,0.6)",
        )

    _marker_label(strike, 1.09, f"Strike {strike:,.0f}", STRIKE_COLOR)
    _marker_label(future_price, 1.02, f"F {future_price:,.0f}", F_COLOR)
    _marker_label(breakeven_down, 1.09, f"BE↓ {breakeven_down:,.0f} ({down_pct:+.1f}%)", BE_COLOR)
    _marker_label(breakeven_up, 1.02, f"BE↑ {breakeven_up:,.0f} ({up_pct:+.1f}%)", BE_COLOR)

    # ── stats box: top-right, inside the plot area ──────────────────────────
    stats_lines = [
        f"Max loss ${cost_usd:,.0f} (at {strike:,.0f})",
    ]
    iv_rv_line = f"IV {atm_iv:.1f}%"
    if rv is not None:
        iv_rv_line += f" · RV {rv:.1f}%"
    stats_lines.append(iv_rv_line)

    extra_parts = []
    if iv_percentile is not None:
        extra_parts.append(f"IV %ile {iv_percentile:.1f}%")
    if vrp is not None:
        extra_parts.append(f"VRP {vrp:+.1f}")
    if extra_parts:
        stats_lines.append(" · ".join(extra_parts))

    fig.add_annotation(
        x=0.98, y=0.95, xref="paper", yref="paper",
        xanchor="right", yanchor="top", align="left",
        text="<br>".join(stats_lines), showarrow=False,
        font=dict(color="#9ca3af", size=11),
        bgcolor="rgba(26,26,26,0.85)",
        bordercolor="#444444", borderwidth=1, borderpad=8,
    )

    # ── title (top-left, with gray subtitle) + layout ───────────────────────
    title_text = (
        f"Long Straddle Payoff — {currency} {expiry}"
        f"<br><span style='font-size:12px;color:#9ca3af'>"
        f"Strike {strike:,.0f} · Cost ${cost_usd:,.0f} · "
        f"Breakevens {breakeven_down:,.0f} / {breakeven_up:,.0f} · "
        f"DTE {dte:.0f}</span>"
    )

    # Merge theme's base xaxis/yaxis styling (gridcolor, linecolor, tickcolor)
    # with this chart's own axis settings — both use the "xaxis"/"yaxis" key
    # so a plain **theme spread here would collide with explicit overrides.
    theme_xaxis = dict(theme.pop("xaxis", {}))
    theme_yaxis = dict(theme.pop("yaxis", {}))
    theme_xaxis.update(title="Underlying price at expiry (USD)", tickformat=",", range=[x_lo, x_hi])
    theme_yaxis.update(title="P&L (USD)", tickprefix="$", tickformat="~s", range=[y_axis_bottom, y_axis_top])

    fig.update_layout(
        title=dict(
            text=title_text, x=0.0, xanchor="left", y=0.97, yanchor="top",
            font=dict(size=18, color="#ffffff"),
        ),
        xaxis=theme_xaxis,
        yaxis=theme_yaxis,
        hovermode="x unified",
        autosize=True,
        margin=dict(t=110, r=40, b=90, l=70),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.12,
            xanchor="center", x=0.5,
            bgcolor="rgba(0,0,0,0)",
            font=dict(color="#9ca3af", size=11),
        ),
        **theme
    )

    return fig
