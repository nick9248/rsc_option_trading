"""
Base chart generator for strategy signal visualization.

Provides common chart generation logic that all strategy-specific
generators inherit from. This ensures consistent theming, layout,
and behavior across all strategy types.

Design:
- BaseStrategyChartGenerator: Abstract base with all common logic
- Subclasses can override specific methods for customization
- Prevents one strategy's changes from breaking others
"""

import logging
from abc import ABC
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import plotly.graph_objects as go

from coding.core.strategy.definitions.base_strategy import StrategyLeg
from coding.core.strategy.models.strategy_signal import StrategySignal
from coding.core.strategy.pnl_calculator import StrategyPnLCalculator

logger = logging.getLogger(__name__)

# Chart output directory
CHARTS_BASE_DIR = Path(__file__).parent.parent.parent.parent.parent / "output" / "strategies"

# Color palette for dark theme
COLORS = {
    "profit_area": "rgba(46, 204, 113, 0.2)",  # Green fill
    "loss_area": "rgba(231, 76, 60, 0.2)",     # Red fill
    "pnl_line": "#3498db",                      # Blue line
    "zero_line": "#666666",                     # Gray
    "current_price": "#ffd93d",                 # Yellow
    "max_pain": "#e74c3c",                      # Red
    "breakeven": "#ffffff",                     # White
    "strike": "#4ecdc4",                        # Cyan
    "call_resistance": "#1abc9c",               # Teal
    "put_support": "#ff6b6b",                   # Coral
    "gamma_flip": "#f39c12",                    # Orange
    "resistance": "#e67e22",                    # Orange-red
    "support": "#16a085",                       # Dark teal
}


def get_chart_theme() -> Dict[str, Any]:
    """
    Get consistent dark theme for strategy charts.

    Returns:
        Dictionary with theme configuration matching existing charts.
    """
    return {
        "paper_bgcolor": "#1a1a1a",
        "plot_bgcolor": "#1a1a1a",
        "font": {"color": "#e0e0e0", "family": "Arial, sans-serif"},
        "title_font": {"size": 18, "color": "#ffffff"},
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


class BaseStrategyChartGenerator(ABC):
    """
    Base class for strategy chart generators.

    Provides all common chart generation logic:
    - P&L profile chart (full screen)
    - Info box overlay (Greeks, Risk metrics, Trend analysis)
    - All support/resistance levels from on-chain and GEX/DEX
    - Leg description as horizontal caption below chart
    - Legend

    Subclasses can override specific methods for strategy-specific customization
    while inheriting all common functionality.

    This design ensures:
    - Existing Long Call/Long Put charts remain unchanged
    - New strategies (spreads, etc.) can add customizations
    - One strategy's changes don't break others
    """

    def __init__(self, output_base_dir: Optional[Path] = None, repository=None):
        """
        Initialize chart generator.

        Args:
            output_base_dir: Base directory for chart output (defaults to CHARTS_BASE_DIR)
            repository: Database repository for trend analysis
        """
        self.output_base_dir = output_base_dir or CHARTS_BASE_DIR
        self.repository = repository

    def generate_strategy_chart(
        self,
        signal: StrategySignal,
        market_context: Dict[str, Any],
        currency: str,
        expiration: str,
        price_range_pct: float = 30.0
    ) -> str:
        """
        Generate comprehensive strategy chart.

        Args:
            signal: Strategy signal with all evaluation data
            market_context: Market context dict with underlying_price, max_pain, etc.
            currency: Currency symbol (e.g., "BTC", "ETH")
            expiration: Expiration date string (e.g., "31JAN25")
            price_range_pct: Price range as percentage of underlying (default ±30%)

        Returns:
            Path to saved HTML chart file

        Raises:
            ValueError: If signal or market_context is invalid
        """
        if not signal.legs:
            raise ValueError("Signal must have at least one leg")

        underlying_price = market_context.get("underlying_price")
        if not underlying_price:
            raise ValueError("Market context must include underlying_price")

        logger.info(f"Generating chart for {signal.strategy_name} - {currency} {expiration}")

        # Calculate price range
        price_range = self._calculate_price_range(underlying_price, price_range_pct)

        # Convert leg dictionaries to StrategyLeg objects
        legs = self._deserialize_legs(signal.legs)

        # Create P&L calculator
        calculator = StrategyPnLCalculator(legs, underlying_price)

        # Calculate P&L profile
        pnl_profile = calculator.calculate_pnl_profile(price_range, num_points=200)

        # Calculate days to expiry
        days_to_expiry = self._calculate_days_to_expiry(expiration)

        # Get trend analysis (last 10 captures for smoother trends)
        trend_analysis = self._get_trend_analysis(currency, expiration, lookback=10)

        # Create figure
        fig = go.Figure()

        # Add P&L profile
        self._add_pnl_profile(fig, pnl_profile, signal)

        # Add key price levels (vertical lines)
        self._add_price_levels(fig, signal, market_context, price_range, legs)

        # Add all support/resistance levels (from on-chain and GEX/DEX)
        self._add_all_support_resistance(fig, market_context, price_range)

        # Add info box overlay
        self._add_info_box(fig, signal, market_context, days_to_expiry, trend_analysis)

        # Apply theme and layout
        self._apply_theme_and_layout(fig, signal, currency, expiration, days_to_expiry, legs)

        # Save chart
        chart_path = self._save_chart(fig, signal, currency, expiration)

        logger.info(f"Chart saved: {chart_path}")
        return chart_path

    def _calculate_price_range(self, underlying_price: float, range_pct: float) -> Tuple[float, float]:
        """Calculate price range for chart."""
        range_multiplier = range_pct / 100.0
        min_price = underlying_price * (1 - range_multiplier)
        max_price = underlying_price * (1 + range_multiplier)
        return (min_price, max_price)

    def _calculate_days_to_expiry(self, expiration: str) -> int:
        """Calculate days remaining until expiration."""
        try:
            exp_date = datetime.strptime(expiration, "%d%b%y")
            today = datetime.now()
            delta = exp_date - today
            return max(0, delta.days)
        except Exception as e:
            logger.warning(f"Failed to parse expiration date {expiration}: {e}")
            return 0

    def _get_trend_analysis(self, currency: str, expiration: str, lookback: int = 10) -> Dict[str, str]:
        """
        Get trend analysis from historical database captures.

        Analyzes the last N captures (default 10) to determine trend direction.
        Compares first vs last value to avoid noise from fluctuations.

        Args:
            currency: Currency symbol
            expiration: Expiration date
            lookback: Number of historical captures to analyze (default 10)

        Returns:
            Dict with trend analysis strings
        """
        trends = {
            "max_pain_trend": "No data",
            "oi_trend": "No data",
            "volume_trend": "No data",
            "pc_ratio_trend": "No data",
        }

        if not self.repository:
            return trends

        try:
            # Get last N max pain captures
            max_pain_data = self.repository.get_max_pain_history(currency, expiration, limit=lookback)
            if len(max_pain_data) >= 2:
                first_mp = max_pain_data[0].get("max_pain_strike", 0)
                last_mp = max_pain_data[-1].get("max_pain_strike", 0)
                if last_mp > first_mp:
                    pct = ((last_mp - first_mp) / first_mp * 100) if first_mp else 0
                    trends["max_pain_trend"] = f"↑ ${first_mp:,.0f} → ${last_mp:,.0f} (+{pct:.1f}%)"
                elif last_mp < first_mp:
                    pct = ((first_mp - last_mp) / first_mp * 100) if first_mp else 0
                    trends["max_pain_trend"] = f"↓ ${first_mp:,.0f} → ${last_mp:,.0f} (-{pct:.1f}%)"
                else:
                    trends["max_pain_trend"] = f"→ Stable at ${last_mp:,.0f}"

            # Get last N OI captures
            oi_data = self.repository.get_open_interest_history(currency, expiration, limit=lookback)
            if len(oi_data) >= 2:
                first_oi = oi_data[0].get("total_oi", 0)
                last_oi = oi_data[-1].get("total_oi", 0)
                pct_change = ((last_oi - first_oi) / first_oi * 100) if first_oi > 0 else 0
                if abs(pct_change) < 3:
                    trends["oi_trend"] = f"→ Stable ({pct_change:+.1f}%)"
                else:
                    arrow = "↑" if pct_change > 0 else "↓"
                    trends["oi_trend"] = f"{arrow} {abs(pct_change):.1f}% over {len(oi_data)} captures"

                # P/C Ratio trend
                first_pc = oi_data[0].get("put_call_ratio", 0)
                last_pc = oi_data[-1].get("put_call_ratio", 0)
                if abs(last_pc - first_pc) < 0.1:
                    trends["pc_ratio_trend"] = f"→ Stable at {last_pc:.2f}"
                elif last_pc > first_pc:
                    trends["pc_ratio_trend"] = f"↑ {first_pc:.2f} → {last_pc:.2f} (More bearish)"
                else:
                    trends["pc_ratio_trend"] = f"↓ {first_pc:.2f} → {last_pc:.2f} (More bullish)"

            # Get last N volume captures
            volume_data = self.repository.get_volume_history(currency, expiration, limit=lookback)
            if len(volume_data) >= 2:
                first_vol = volume_data[0].get("total_volume", 0)
                last_vol = volume_data[-1].get("total_volume", 0)
                pct_change = ((last_vol - first_vol) / first_vol * 100) if first_vol > 0 else 0
                if abs(pct_change) < 5:
                    trends["volume_trend"] = f"→ Stable ({pct_change:+.1f}%)"
                else:
                    arrow = "↑" if pct_change > 0 else "↓"
                    trends["volume_trend"] = f"{arrow} {abs(pct_change):.1f}% over {len(volume_data)} captures"

        except Exception as e:
            logger.warning(f"Failed to get trend analysis: {e}")

        return trends

    def _deserialize_legs(self, legs_data: List[Dict]) -> List[StrategyLeg]:
        """Convert leg dictionaries to StrategyLeg objects."""
        legs = []
        for leg_dict in legs_data:
            leg = StrategyLeg(
                action=leg_dict["action"],
                option_type=leg_dict["option_type"],
                strike=leg_dict["strike"],
                quantity=leg_dict["quantity"],
                cost=leg_dict["cost"],
                greeks=leg_dict.get("greeks", {}),
                instrument_name=leg_dict.get("instrument_name", "")
            )
            legs.append(leg)
        return legs

    def _add_pnl_profile(self, fig: go.Figure, pnl_profile: Dict[float, float], signal: StrategySignal):
        """Add P&L profile line and filled areas to chart."""
        prices = sorted(pnl_profile.keys())
        pnls = [pnl_profile[p] for p in prices]

        # Main P&L line
        fig.add_trace(go.Scatter(
            x=prices,
            y=pnls,
            mode="lines",
            name="P&L Profile",
            line={"color": COLORS["pnl_line"], "width": 3},
            hovertemplate="<b>Price: $%{x:,.2f}</b><br>P&L: $%{y:,.2f}<extra></extra>",
            showlegend=True,
        ))

        # Fill profit area (green)
        profit_prices = [p for p in prices if pnl_profile[p] > 0]
        if profit_prices:
            profit_pnls = [pnl_profile[p] for p in profit_prices]
            fig.add_trace(go.Scatter(
                x=profit_prices,
                y=profit_pnls,
                fill='tozeroy',
                fillcolor=COLORS["profit_area"],
                line={"width": 0},
                showlegend=False,
                hoverinfo='skip',
            ))

        # Fill loss area (red)
        loss_prices = [p for p in prices if pnl_profile[p] < 0]
        if loss_prices:
            loss_pnls = [pnl_profile[p] for p in loss_prices]
            fig.add_trace(go.Scatter(
                x=loss_prices,
                y=loss_pnls,
                fill='tozeroy',
                fillcolor=COLORS["loss_area"],
                line={"width": 0},
                showlegend=False,
                hoverinfo='skip',
            ))

        # Zero line
        fig.add_hline(
            y=0,
            line_dash="dash",
            line_color=COLORS["zero_line"],
            line_width=1,
            annotation_text="Zero P&L",
            annotation_position="right",
        )

    def _add_price_levels(
        self,
        fig: go.Figure,
        signal: StrategySignal,
        market_context: Dict[str, Any],
        price_range: Tuple[float, float],
        legs: List[StrategyLeg]
    ):
        """Add vertical lines for key price levels."""
        underlying_price = market_context.get("underlying_price")
        max_pain_strike = market_context.get("max_pain_strike")

        # Current price
        if underlying_price:
            fig.add_vline(
                x=underlying_price,
                line_dash="dash",
                line_color=COLORS["current_price"],
                line_width=2,
                annotation_text=f"Current: ${underlying_price:,.0f}",
                annotation_position="top left",
            )
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode='lines',
                line=dict(color=COLORS["current_price"], width=2, dash='dash'),
                name=f'Current (${underlying_price:,.0f})',
                showlegend=True
            ))

        # Max pain
        if max_pain_strike and price_range[0] <= max_pain_strike <= price_range[1]:
            fig.add_vline(
                x=max_pain_strike,
                line_dash="dot",
                line_color=COLORS["max_pain"],
                line_width=2,
                annotation_text=f"Max Pain: ${max_pain_strike:,.0f}",
                annotation_position="bottom left",
            )
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode='lines',
                line=dict(color=COLORS["max_pain"], width=2, dash='dot'),
                name=f'Max Pain (${max_pain_strike:,.0f})',
                showlegend=True
            ))

        # Breakeven points
        for i, breakeven in enumerate(signal.breakeven_points or []):
            if price_range[0] <= breakeven <= price_range[1]:
                fig.add_vline(
                    x=breakeven,
                    line_dash="dash",
                    line_color=COLORS["breakeven"],
                    line_width=2,
                    annotation_text=f"BE: ${breakeven:,.0f}",
                    annotation_position="top right" if i % 2 == 0 else "bottom right",
                )
                if i == 0:
                    fig.add_trace(go.Scatter(
                        x=[None], y=[None],
                        mode='lines',
                        line=dict(color=COLORS["breakeven"], width=2, dash='dash'),
                        name='Breakeven',
                        showlegend=True
                    ))

        # Strike prices
        for leg in legs:
            if price_range[0] <= leg.strike <= price_range[1]:
                fig.add_vline(
                    x=leg.strike,
                    line_dash="dot",
                    line_color=COLORS["strike"],
                    line_width=1,
                    opacity=0.6,
                    annotation_text=f"{leg.option_type.upper()} ${leg.strike:,.0f}",
                    annotation_position="top",
                    annotation_font_size=10,
                )
        if legs:
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode='lines',
                line=dict(color=COLORS["strike"], width=1, dash='dot'),
                name='Strike',
                showlegend=True
            ))

    def _add_all_support_resistance(
        self,
        fig: go.Figure,
        market_context: Dict[str, Any],
        price_range: Tuple[float, float]
    ):
        """Add ALL support/resistance levels from on-chain and GEX/DEX."""
        # GEX/DEX levels
        gex_dex_data = market_context.get("gex_dex_data", {})
        key_levels = gex_dex_data.get("key_levels", {})

        # Call resistance (GEX/DEX)
        call_resistance_data = key_levels.get("call_resistance")
        if call_resistance_data and call_resistance_data.get("strike"):
            call_resistance = call_resistance_data["strike"]
            if price_range[0] <= call_resistance <= price_range[1]:
                fig.add_vline(
                    x=call_resistance,
                    line_dash="dashdot",
                    line_color=COLORS["call_resistance"],
                    line_width=2,
                    opacity=0.7,
                )
                fig.add_trace(go.Scatter(
                    x=[None], y=[None],
                    mode='lines',
                    line=dict(color=COLORS["call_resistance"], width=2, dash='dashdot'),
                    name=f'Call Wall (${call_resistance:,.0f})',
                    showlegend=True
                ))

        # Put support (GEX/DEX)
        put_support_data = key_levels.get("put_support")
        if put_support_data and put_support_data.get("strike"):
            put_support = put_support_data["strike"]
            if price_range[0] <= put_support <= price_range[1]:
                fig.add_vline(
                    x=put_support,
                    line_dash="dashdot",
                    line_color=COLORS["put_support"],
                    line_width=2,
                    opacity=0.7,
                )
                fig.add_trace(go.Scatter(
                    x=[None], y=[None],
                    mode='lines',
                    line=dict(color=COLORS["put_support"], width=2, dash='dashdot'),
                    name=f'Put Wall (${put_support:,.0f})',
                    showlegend=True
                ))

        # Gamma flip / Zero Gamma (GEX/DEX)
        gamma_flip = key_levels.get("hvl")
        if gamma_flip and price_range[0] <= gamma_flip <= price_range[1]:
            fig.add_vline(
                x=gamma_flip,
                line_dash="dot",
                line_color=COLORS["gamma_flip"],
                line_width=2,
                opacity=0.6,
            )
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode='lines',
                line=dict(color=COLORS["gamma_flip"], width=2, dash='dot'),
                name=f'Zero Gamma (${gamma_flip:,.0f})',
                showlegend=True
            ))

        # On-chain support/resistance levels
        support_resistance = market_context.get("support_resistance", {})

        logger.debug(f"Support/Resistance data: {support_resistance}")

        # Top 3 Resistance levels (from Call OI)
        resistance_levels = support_resistance.get("resistance_levels", [])
        logger.debug(f"Resistance levels to plot: {resistance_levels}")
        for i, level in enumerate(resistance_levels[:3], 1):
            strike = level.get("strike")
            logger.debug(f"Resistance level {i}: strike={strike}, range={price_range}")
            if strike and price_range[0] <= strike <= price_range[1]:
                logger.info(f"Adding resistance level at ${strike:,.0f}")
                fig.add_vline(
                    x=strike,
                    line_dash="dash",
                    line_color=COLORS["resistance"],
                    line_width=1,
                    opacity=0.5,
                )
                if i == 1:  # Only add to legend once
                    fig.add_trace(go.Scatter(
                        x=[None], y=[None],
                        mode='lines',
                        line=dict(color=COLORS["resistance"], width=1, dash='dash'),
                        name='Resistance (OI)',
                        showlegend=True
                    ))
            else:
                logger.debug(f"Skipping resistance level at ${strike} (outside range {price_range})")

        # Top 3 Support levels (from Put OI)
        support_levels = support_resistance.get("support_levels", [])
        logger.debug(f"Support levels to plot: {support_levels}")
        for i, level in enumerate(support_levels[:3], 1):
            strike = level.get("strike")
            logger.debug(f"Support level {i}: strike={strike}, range={price_range}")
            if strike and price_range[0] <= strike <= price_range[1]:
                logger.info(f"Adding support level at ${strike:,.0f}")
                fig.add_vline(
                    x=strike,
                    line_dash="dash",
                    line_color=COLORS["support"],
                    line_width=1,
                    opacity=0.5,
                )
                if i == 1:  # Only add to legend once
                    fig.add_trace(go.Scatter(
                        x=[None], y=[None],
                        mode='lines',
                        line=dict(color=COLORS["support"], width=1, dash='dash'),
                        name='Support (OI)',
                        showlegend=True
                    ))
            else:
                logger.debug(f"Skipping support level at ${strike} (outside range {price_range})")

    def _add_info_box(
        self,
        fig: go.Figure,
        signal: StrategySignal,
        market_context: Dict[str, Any],
        days_to_expiry: int,
        trend_analysis: Dict[str, str]
    ):
        """Add info box overlay in top-right corner."""
        info_lines = []

        # Greeks
        info_lines.append("<b>GREEKS</b>")
        info_lines.append(f"Δ: {signal.net_delta:.4f}")
        info_lines.append(f"Γ: {signal.net_gamma:.6f}")
        info_lines.append(f"Θ: {signal.net_theta:.2f}/day")
        info_lines.append(f"ν: {signal.net_vega:.4f}")
        info_lines.append("")

        # Risk
        info_lines.append("<b>RISK</b>")
        info_lines.append(f"Max: ${signal.max_risk:,.0f}")
        if signal.max_profit and signal.max_profit != float('inf'):
            info_lines.append(f"Profit: ${signal.max_profit:,.0f}")
        else:
            info_lines.append(f"Profit: Unlimited")
        info_lines.append(f"Cost: ${signal.total_cost:,.0f}")
        info_lines.append(f"Loss%: {signal.max_loss_percentage:.1f}%")
        info_lines.append("")

        # Scores
        info_lines.append("<b>SCORES</b>")
        info_lines.append(f"Total: {signal.composite_score:.2f}/10")
        info_lines.append(f"Intrinsic: {signal.intrinsic_score:.2f}")
        info_lines.append(f"On-Chain: {signal.on_chain_score:.2f}")
        info_lines.append("")

        # Market
        info_lines.append("<b>MARKET</b>")
        pc_ratio = market_context.get("put_call_ratio", 0)
        sentiment = "Bullish" if pc_ratio < 0.7 else ("Bearish" if pc_ratio > 1.3 else "Neutral")
        info_lines.append(f"P/C: {pc_ratio:.2f} ({sentiment})")

        gex_total = market_context.get("gex_dex_data", {}).get("totals", {}).get("total_net_gex", 0)
        gex_type = "Stabilize" if gex_total > 0 else "Destabilize"
        info_lines.append(f"GEX: {gex_type}")

        iv_percentile = market_context.get("iv_percentile", 0)
        dvol = market_context.get("dvol", 0)
        if iv_percentile:
            info_lines.append(f"IV%: {iv_percentile:.0f}")
        if dvol:
            info_lines.append(f"DVOL: {dvol:.1f}%")
        info_lines.append("")

        # Trends
        info_lines.append("<b>TRENDS (10 captures)</b>")
        info_lines.append(f"MP: {trend_analysis.get('max_pain_trend', 'N/A')}")
        info_lines.append(f"OI: {trend_analysis.get('oi_trend', 'N/A')}")
        info_lines.append(f"Vol: {trend_analysis.get('volume_trend', 'N/A')}")
        info_lines.append(f"P/C: {trend_analysis.get('pc_ratio_trend', 'N/A')}")

        info_text = "<br>".join(info_lines)

        fig.add_annotation(
            x=0.99,
            y=0.99,
            xref="paper",
            yref="paper",
            text=info_text,
            showarrow=False,
            align="left",
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(26, 26, 26, 0.95)",
            bordercolor="#444444",
            borderwidth=2,
            borderpad=8,
            font=dict(size=9, color="#e0e0e0", family="Courier New, monospace"),
        )

    def _apply_theme_and_layout(
        self,
        fig: go.Figure,
        signal: StrategySignal,
        currency: str,
        expiration: str,
        days_to_expiry: int,
        legs: List[StrategyLeg]
    ):
        """Apply theme and layout with horizontal strategy caption."""
        theme = get_chart_theme()

        # Title
        title_text = (
            f"{signal.strategy_name} - {currency} {expiration} - "
            f"Score: {signal.composite_score:.2f}/10 - {days_to_expiry} days"
        )

        # Strategy structure as horizontal caption
        leg_parts = []
        for i, leg in enumerate(legs, 1):
            leg_parts.append(
                f"{leg.action.upper()} {abs(leg.quantity)} {leg.option_type.upper()} "
                f"@ ${leg.strike:,.0f} (${leg.cost:.2f})"
            )
        strategy_caption = " | ".join(leg_parts)
        strategy_caption += f" | Total: ${signal.total_cost:.2f}"
        if signal.breakeven_points:
            be_str = ", ".join([f"${bp:,.0f}" for bp in signal.breakeven_points])
            strategy_caption += f" | BE: {be_str}"

        fig.update_layout(
            title={
                "text": f"<b>{title_text}</b><br><sub>{strategy_caption}</sub>",
                "x": 0.5,
                "xanchor": "center",
                "y": 0.98,
                "yanchor": "top",
            },
            xaxis_title="Price at Expiration ($)",
            yaxis_title="Profit/Loss ($)",
            hovermode="x unified",
            legend={
                "orientation": "v",
                "yanchor": "top",
                "y": 0.95,
                "xanchor": "left",
                "x": 0.01,
                "bgcolor": "rgba(26, 26, 26, 0.8)",
                "bordercolor": "#444444",
                "borderwidth": 1,
                "font": {"size": 9},
            },
            height=None,  # Auto height - will be controlled by HTML
            autosize=True,
            margin=dict(t=100, b=40, l=60, r=60),
            **theme
        )

    def _save_chart(
        self,
        fig: go.Figure,
        signal: StrategySignal,
        currency: str,
        expiration: str
    ) -> str:
        """Save chart to HTML file with full screen layout."""
        charts_dir = self.output_base_dir / expiration / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        strategy_name = signal.strategy_name.replace(" ", "_")

        # For multi-leg strategies, add strikes to filename for uniqueness
        if len(signal.legs) > 1:
            strikes = [f"{int(leg['strike'])}" for leg in signal.legs]
            strike_str = "_" + "_".join(strikes)
            filename = f"{currency}_{strategy_name}{strike_str}_{timestamp}.html"
        else:
            filename = f"{currency}_{strategy_name}_{timestamp}.html"

        chart_path = charts_dir / filename

        # Save with full screen HTML wrapper
        html_string = fig.to_html(
            config={
                'displayModeBar': True,
                'displaylogo': False,
                'modeBarButtonsToRemove': ['pan2d', 'lasso2d', 'select2d'],
                'responsive': True,
            },
            include_plotlyjs='cdn',
            full_html=False
        )

        # Wrap in full HTML with viewport height
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{signal.strategy_name} - {currency} {expiration}</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background-color: #1a1a1a;
            overflow: hidden;
        }}
        .plotly-graph-div {{
            width: 100vw !important;
            height: 100vh !important;
        }}
    </style>
</head>
<body>
    {html_string}
</body>
</html>"""

        with open(chart_path, 'w', encoding='utf-8') as f:
            f.write(full_html)

        return str(chart_path)
