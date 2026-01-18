"""
Strategy evaluation report generator.

Generates comprehensive text files with all market data, strategy details,
scores, and analysis for each evaluated strategy.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class StrategyReportGenerator:
    """
    Generates detailed text reports for strategy evaluations.

    Reports are saved to output/strategies/{expiration}/ and contain:
    - Strategy definition (structured leg descriptions)
    - Market context (underlying price, IV, max pain, P/C ratio)
    - On-chain metrics (OI, volume, moneyness, GEX/DEX levels)
    - Scoring breakdown (intrinsic, on-chain, composite)
    - Risk metrics (max loss, breakeven, greeks)
    - Days to expiry
    """

    def __init__(self, output_base_dir: str = None):
        """
        Initialize report generator.

        Args:
            output_base_dir: Base directory for strategy reports
        """
        if output_base_dir is None:
            # Use project root output directory
            project_root = Path(__file__).parent.parent.parent.parent
            output_base_dir = project_root / "output" / "strategies"

        self.output_base_dir = Path(output_base_dir)

    def generate_report(
        self,
        signal,  # StrategySignal
        market_context: Dict[str, Any],
        currency: str,
        expiration: str
    ) -> str:
        """
        Generate comprehensive strategy report.

        Args:
            signal: StrategySignal with all scores and data
            market_context: Market data used for evaluation
            currency: Currency symbol
            expiration: Expiration date string

        Returns:
            Path to generated report file
        """
        # Create output directory
        expiry_dir = self.output_base_dir / expiration
        expiry_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{currency}_{signal.strategy_name.replace(' ', '_')}_{timestamp}.txt"
        filepath = expiry_dir / filename

        # Build report content
        report_lines = []
        report_lines.extend(self._generate_header(signal, currency, expiration))
        report_lines.extend(self._generate_strategy_definition(signal, market_context))
        report_lines.extend(self._generate_market_context(signal, market_context))
        report_lines.extend(self._generate_on_chain_metrics(market_context))
        report_lines.extend(self._generate_scoring_breakdown(signal))
        report_lines.extend(self._generate_risk_metrics(signal, market_context))
        report_lines.extend(self._generate_greek_profile(signal))
        report_lines.extend(self._generate_footer())

        # Write to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(report_lines))

        logger.info(f"Generated strategy report: {filepath}")
        return str(filepath)

    def _generate_header(self, signal, currency: str, expiration: str) -> List[str]:
        """Generate report header."""
        return [
            "=" * 100,
            f"STRATEGY EVALUATION REPORT".center(100),
            "=" * 100,
            "",
            f"Generated: {signal.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Strategy: {signal.strategy_name}",
            f"Currency: {currency}",
            f"Expiration: {expiration}",
            "",
            "=" * 100,
            ""
        ]

    def _generate_strategy_definition(self, signal, market_context: Dict) -> List[str]:
        """Generate structured strategy definition."""
        lines = [
            "STRATEGY DEFINITION",
            "-" * 100,
            ""
        ]

        underlying_price = signal.underlying_price

        for i, leg in enumerate(signal.legs, 1):
            action = leg['action'].upper()  # BUY or SELL
            # Handle both formats: "call"/"put" or "C"/"P"
            opt_type = leg['option_type']
            if opt_type.lower() == 'call' or opt_type == 'C':
                option_type = "CALL"
            else:
                option_type = "PUT"

            strike = leg['strike']
            quantity = leg['quantity']
            cost_usd = leg['cost']  # Already in USD
            cost_currency = cost_usd / underlying_price  # Convert to currency units (ETH/BTC)

            lines.append(f"Leg {i}:")
            lines.append(f"  {action} {quantity} {option_type} @ Strike ${strike:,.2f}")
            lines.append(f"  Premium: {cost_currency:.4f} {signal.currency} (${cost_usd:.2f} USD)")

            # Greeks
            if leg.get('greeks'):
                greeks = leg['greeks']
                lines.append(f"  Greeks: Delta={greeks.get('delta', 0):.4f}, Gamma={greeks.get('gamma', 0):.6f}, "
                           f"Theta={greeks.get('theta', 0):.4f}, Vega={greeks.get('vega', 0):.4f}")
            lines.append("")

        # Overall strategy metrics
        lines.append(f"Total Cost: ${signal.total_cost:.2f} USD ({signal.total_cost / underlying_price:.4f} ETH)")
        lines.append(f"Max Risk: ${signal.max_risk:.2f} USD")

        if signal.max_profit and signal.max_profit != float('inf'):
            lines.append(f"Max Profit: ${signal.max_profit:.2f} USD")
        else:
            lines.append(f"Max Profit: Unlimited")

        lines.append(f"Max Loss %: {signal.max_loss_percentage:.2f}% of underlying")

        if signal.take_profit_percentage:
            lines.append(f"Take Profit Target: {signal.take_profit_percentage:.0f}%")

        if signal.breakeven_points:
            breakevens = ", ".join([f"${bp:,.2f}" for bp in signal.breakeven_points])
            lines.append(f"Breakeven Points: {breakevens}")

        lines.append("")
        lines.append("")

        return lines

    def _generate_market_context(self, signal, market_context: Dict) -> List[str]:
        """Generate market context section."""
        lines = [
            "MARKET CONTEXT",
            "-" * 100,
            ""
        ]

        lines.append(f"Underlying Price: ${signal.underlying_price:,.2f}")

        if signal.implied_volatility:
            lines.append(f"Implied Volatility: {signal.implied_volatility:.2f}%")

        if signal.max_pain_strike:
            lines.append(f"Max Pain Strike: ${signal.max_pain_strike:,.2f}")
            distance = ((signal.max_pain_strike - signal.underlying_price) / signal.underlying_price) * 100
            lines.append(f"Max Pain Distance: {distance:+.2f}% from current price")

        # Days to expiry
        if 'days_to_expiry' in market_context:
            lines.append(f"Days to Expiry: {market_context['days_to_expiry']} days")

        # Put/Call Ratio
        if 'put_call_ratio' in market_context:
            pc_ratio = market_context['put_call_ratio']
            lines.append(f"Put/Call Ratio: {pc_ratio:.3f}")
            if pc_ratio < 0.7:
                lines.append(f"  → Bullish sentiment (more calls than puts)")
            elif pc_ratio > 1.3:
                lines.append(f"  → Bearish sentiment (more puts than calls)")
            else:
                lines.append(f"  → Neutral sentiment")

        # Volume ratios
        if 'volume_put_call_ratio' in market_context:
            vol_ratio = market_context['volume_put_call_ratio']
            lines.append(f"Volume Put/Call Ratio: {vol_ratio:.3f}")

        lines.append("")
        lines.append("")

        return lines

    def _generate_on_chain_metrics(self, market_context: Dict) -> List[str]:
        """Generate detailed on-chain metrics section."""
        lines = [
            "ON-CHAIN METRICS",
            "-" * 100,
            ""
        ]

        # Open Interest
        if 'oi_data' in market_context:
            lines.append("Open Interest Analysis:")
            oi_data = market_context['oi_data']

            if 'total_call_oi' in oi_data:
                lines.append(f"  Total Call OI: {oi_data['total_call_oi']:,.0f} contracts")
            if 'total_put_oi' in oi_data:
                lines.append(f"  Total Put OI: {oi_data['total_put_oi']:,.0f} contracts")
            if 'max_call_oi_strike' in oi_data:
                lines.append(f"  Max Call OI Strike: ${oi_data['max_call_oi_strike']:,.2f}")
            if 'max_put_oi_strike' in oi_data:
                lines.append(f"  Max Put OI Strike: ${oi_data['max_put_oi_strike']:,.2f}")

            lines.append("")

        # Volume
        if 'volume_data' in market_context:
            lines.append("Volume Analysis:")
            vol_data = market_context['volume_data']

            if 'total_call_volume' in vol_data:
                lines.append(f"  Total Call Volume: {vol_data['total_call_volume']:,.0f}")
            if 'total_put_volume' in vol_data:
                lines.append(f"  Total Put Volume: {vol_data['total_put_volume']:,.0f}")

            lines.append("")

        # Moneyness Distribution
        if 'moneyness_distribution' in market_context:
            lines.append("Moneyness Distribution:")
            dist = market_context['moneyness_distribution']

            if 'itm_calls_pct' in dist:
                lines.append(f"  ITM Calls: {dist['itm_calls_pct']:.1f}%")
            if 'otm_calls_pct' in dist:
                lines.append(f"  OTM Calls: {dist['otm_calls_pct']:.1f}%")
            if 'itm_puts_pct' in dist:
                lines.append(f"  ITM Puts: {dist['itm_puts_pct']:.1f}%")
            if 'otm_puts_pct' in dist:
                lines.append(f"  OTM Puts: {dist['otm_puts_pct']:.1f}%")

            lines.append("")

        # GEX/DEX Levels
        if 'gex_dex_data' in market_context:
            lines.append("GEX/DEX Analysis:")
            gex_data = market_context['gex_dex_data']

            if 'total_net_gex' in gex_data:
                total_gex = gex_data['total_net_gex']
                lines.append(f"  Total Net GEX: {total_gex:,.0f}")
                if total_gex > 0:
                    lines.append(f"    → Positive GEX: Dealers provide stability (sell rallies, buy dips)")
                else:
                    lines.append(f"    → Negative GEX: Dealers amplify moves (buy rallies, sell dips)")

            if 'total_net_dex' in gex_data:
                lines.append(f"  Total Net DEX: {gex_data['total_net_dex']:,.0f}")

            # Key levels structure from GexDexCalculator
            if 'key_levels' in gex_data:
                key_levels = gex_data['key_levels']

                if key_levels.get('call_resistance'):
                    call_res = key_levels['call_resistance']
                    lines.append(f"  Call Wall (Resistance): ${call_res['strike']:,.2f} (GEX: {call_res['net_gex']:,.0f})")

                if key_levels.get('put_support'):
                    put_sup = key_levels['put_support']
                    lines.append(f"  Put Wall (Support): ${put_sup['strike']:,.2f} (GEX: {put_sup['net_gex']:,.0f})")

                if key_levels.get('gamma_flip'):
                    lines.append(f"  Gamma Flip Point: ${key_levels['gamma_flip']:,.2f}")

            lines.append("")
        else:
            lines.append("GEX/DEX: Not available")
            lines.append("  → Insufficient gamma/delta data for this expiration")
            lines.append("  → GEX/DEX score defaulting to neutral (5.0/10)")
            lines.append("")

        # Support/Resistance Levels
        if 'support_resistance' in market_context:
            sr_data = market_context['support_resistance']

            if 'resistance_levels' in sr_data and sr_data['resistance_levels']:
                lines.append("Resistance Levels:")
                for level in sr_data['resistance_levels'][:5]:  # Top 5
                    lines.append(f"  ${level:,.2f}")
                lines.append("")

            if 'support_levels' in sr_data and sr_data['support_levels']:
                lines.append("Support Levels:")
                for level in sr_data['support_levels'][:5]:  # Top 5
                    lines.append(f"  ${level:,.2f}")
                lines.append("")

        # Trend Analysis
        if 'max_pain_trend' in market_context:
            lines.append("Trend Analysis:")
            trend = market_context['max_pain_trend']

            if trend == 'decreasing':
                lines.append(f"  Max Pain Trend: Decreasing (Bullish signal)")
            elif trend == 'increasing':
                lines.append(f"  Max Pain Trend: Increasing (Bearish signal)")
            else:
                lines.append(f"  Max Pain Trend: Neutral")

            if 'volume_trend' in market_context:
                vol_trend = market_context['volume_trend']
                if vol_trend == 'increasing':
                    lines.append(f"  Volume Trend: Increasing (Stronger conviction)")
                elif vol_trend == 'decreasing':
                    lines.append(f"  Volume Trend: Decreasing (Weaker conviction)")
                else:
                    lines.append(f"  Volume Trend: Neutral")

            lines.append("")

        lines.append("")

        return lines

    def _generate_scoring_breakdown(self, signal) -> List[str]:
        """Generate detailed scoring breakdown."""
        lines = [
            "SCORING BREAKDOWN",
            "-" * 100,
            ""
        ]

        lines.append(f"Composite Score: {signal.composite_score:.2f}/10")
        lines.append(f"  Intrinsic Score: {signal.intrinsic_score:.2f}/10 (50% weight)")
        lines.append(f"  On-Chain Score: {signal.on_chain_score:.2f}/10 (50% weight)")
        lines.append("")

        # Intrinsic breakdown
        if signal.intrinsic_breakdown:
            lines.append("Intrinsic Components:")
            for key, value in signal.intrinsic_breakdown.items():
                formatted_key = key.replace('_', ' ').title()
                lines.append(f"  {formatted_key}: {value:.2f}/10")
            lines.append("")

        # On-chain breakdown
        if signal.on_chain_breakdown:
            lines.append("On-Chain Components:")
            for key, value in signal.on_chain_breakdown.items():
                formatted_key = key.replace('_', ' ').title()
                lines.append(f"  {formatted_key}: {value:.2f}/10")
            lines.append("")

        # Score interpretation
        lines.append("Score Interpretation:")
        if signal.composite_score >= 8.0:
            lines.append("  → EXCELLENT: Strong opportunity with favorable conditions")
        elif signal.composite_score >= 6.0:
            lines.append("  → GOOD: Solid opportunity worth considering")
        elif signal.composite_score >= 4.0:
            lines.append("  → NEUTRAL: Marginal opportunity, proceed with caution")
        else:
            lines.append("  → POOR: Unfavorable conditions, avoid")

        lines.append("")
        lines.append("")

        return lines

    def _generate_risk_metrics(self, signal, market_context: Dict) -> List[str]:
        """Generate risk metrics section."""
        lines = [
            "RISK METRICS",
            "-" * 100,
            ""
        ]

        lines.append(f"Maximum Risk: ${signal.max_risk:.2f}")
        lines.append(f"Maximum Loss %: {signal.max_loss_percentage:.2f}% of underlying price")

        if signal.max_profit and signal.max_profit != float('inf'):
            lines.append(f"Maximum Profit: ${signal.max_profit:.2f}")
            risk_reward = signal.max_profit / signal.max_risk if signal.max_risk > 0 else 0
            lines.append(f"Risk/Reward Ratio: {risk_reward:.2f}:1")
        else:
            lines.append(f"Maximum Profit: Unlimited")
            lines.append(f"Risk/Reward Ratio: Unlimited upside")

        lines.append(f"Total Cost: ${signal.total_cost:.2f}")
        cost_pct = (signal.total_cost / signal.underlying_price) * 100
        lines.append(f"Cost as % of Underlying: {cost_pct:.2f}%")

        if signal.breakeven_points:
            for i, bp in enumerate(signal.breakeven_points, 1):
                distance = ((bp - signal.underlying_price) / signal.underlying_price) * 100
                lines.append(f"Breakeven Point {i}: ${bp:,.2f} ({distance:+.2f}% from current)")

        if signal.take_profit_percentage:
            target_profit = signal.total_cost * (signal.take_profit_percentage / 100)
            lines.append(f"Take Profit Target: {signal.take_profit_percentage:.0f}% gain (${target_profit:.2f})")

        lines.append("")
        lines.append("")

        return lines

    def _generate_greek_profile(self, signal) -> List[str]:
        """Generate greek profile section."""
        lines = [
            "GREEK PROFILE",
            "-" * 100,
            ""
        ]

        lines.append(f"Net Delta: {signal.net_delta:.4f}")
        lines.append(f"  → Directional exposure: ${abs(signal.net_delta) * signal.underlying_price:.2f} per $1 move")

        lines.append(f"Net Gamma: {signal.net_gamma:.6f}")
        lines.append(f"  → Delta acceleration: {signal.net_gamma:.6f} per $1 move")

        lines.append(f"Net Theta: {signal.net_theta:.4f}")
        if signal.net_theta < 0:
            daily_decay = abs(signal.net_theta)
            lines.append(f"  → Time decay: -${daily_decay:.2f} per day")
        else:
            lines.append(f"  → Time value earned: +${signal.net_theta:.2f} per day")

        lines.append(f"Net Vega: {signal.net_vega:.4f}")
        lines.append(f"  → IV sensitivity: ${signal.net_vega:.2f} per 1% IV change")

        lines.append("")
        lines.append("")

        return lines

    def _generate_footer(self) -> List[str]:
        """Generate report footer."""
        return [
            "=" * 100,
            "END OF REPORT",
            "=" * 100,
            "",
            "This report was generated by the Strategy Evaluation System.",
            "For detailed scoring formulas and interpretation, see documentation/strategy_system_guide.md",
            ""
        ]
