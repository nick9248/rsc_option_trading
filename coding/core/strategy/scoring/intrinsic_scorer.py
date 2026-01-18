"""
Intrinsic scorer for strategy evaluation.

Scores strategies based on their inherent characteristics:
- Risk/Reward ratio
- Cost efficiency
- Greek profile
- Breakeven distance
- Strike moneyness (distance from strike to current price)
"""

import logging
from typing import Dict

from .base_scorer import BaseScorer

logger = logging.getLogger(__name__)


class IntrinsicScorer(BaseScorer):
    """
    Scores strategies based on intrinsic metrics.

    Components (weights):
    1. Risk/Reward Ratio (30%): Profit potential vs risk
    2. Cost Efficiency (20%): Cost relative to underlying price
    3. Greek Profile (20%): Greeks alignment with strategy type
    4. Breakeven Distance (15%): Distance to breakeven point
    5. Strike Moneyness (15%): Distance from strike to current price (optimal OTM range)

    All scores are on 0-10 scale, higher is better.
    """

    # Component weights (must sum to 1.0)
    WEIGHTS = {
        "risk_reward_ratio": 0.30,
        "cost_efficiency": 0.20,
        "greek_profile": 0.20,
        "breakeven_distance": 0.15,
        "strike_moneyness": 0.15
    }

    def calculate_score(self, strategy, market_context: Dict) -> float:
        """
        Calculate overall intrinsic score.

        Args:
            strategy: Strategy instance
            market_context: Market data (not heavily used for intrinsic scoring)

        Returns:
            Overall intrinsic score (0-10)
        """
        components = self.get_breakdown(strategy, market_context)
        return self.weighted_average(components, self.WEIGHTS)

    def get_breakdown(self, strategy, market_context: Dict) -> Dict[str, float]:
        """
        Get breakdown of all intrinsic components.

        Args:
            strategy: Strategy instance
            market_context: Market data

        Returns:
            Dictionary with component scores (0-10)
        """
        return {
            "risk_reward_ratio": self._score_risk_reward_ratio(strategy),
            "cost_efficiency": self._score_cost_efficiency(strategy),
            "greek_profile": self._score_greek_profile(strategy),
            "breakeven_distance": self._score_breakeven_distance(strategy),
            "strike_moneyness": self._score_strike_moneyness(strategy)
        }

    def _score_risk_reward_ratio(self, strategy) -> float:
        """
        Score risk/reward ratio.

        Higher profit/risk ratio = higher score.
        Handle unlimited profit scenarios specially.

        Args:
            strategy: Strategy instance

        Returns:
            Score (0-10)
        """
        max_risk = strategy.get_max_risk()
        max_profit = strategy.get_max_profit()

        if max_risk <= 0:
            logger.warning(f"{strategy.name}: Max risk is zero or negative")
            return 0.0

        # Handle unlimited profit (e.g., long call)
        if max_profit is None:
            # Award high score for unlimited profit potential
            # But still consider risk - lower risk = higher score
            # Normalize based on risk as % of underlying
            risk_pct = (max_risk / strategy.underlying_price) * 100

            # Lower risk % = higher score
            # 1% risk = 10 score, 10% risk = 5 score, 20%+ risk = 0 score
            score = self.normalize_score(
                score=20.0 - risk_pct,
                min_score=0.0,
                max_score=20.0
            )

            logger.debug(
                f"{strategy.name}: Unlimited profit, risk={risk_pct:.2f}%, score={score:.2f}"
            )
            return score

        # Calculate profit/risk ratio
        if max_profit <= 0:
            logger.warning(f"{strategy.name}: Max profit is zero or negative")
            return 0.0

        ratio = max_profit / max_risk

        # Normalize ratio to 0-10 scale
        # Ratio 0.5 = 0, ratio 1.0 = 5, ratio 2.0 = 8, ratio 3.0+ = 10
        score = self.normalize_score(
            score=ratio,
            min_score=0.5,
            max_score=3.0
        )

        logger.debug(
            f"{strategy.name}: Risk/Reward ratio={ratio:.2f}, score={score:.2f}"
        )

        return score

    def _score_cost_efficiency(self, strategy) -> float:
        """
        Score cost efficiency.

        Lower cost relative to underlying = higher score.
        Considers max loss percentage.

        Args:
            strategy: Strategy instance

        Returns:
            Score (0-10)
        """
        max_loss_pct = strategy.get_max_loss_percentage()

        if max_loss_pct == float('inf'):
            logger.warning(f"{strategy.name}: Unlimited risk, cost efficiency score=0")
            return 0.0

        # Lower loss % = higher score
        # 0.5% loss = 10, 2% loss = 8, 5% loss = 5, 10%+ loss = 0
        score = self.normalize_score(
            score=10.0 - max_loss_pct,
            min_score=0.0,
            max_score=10.0
        )

        logger.debug(
            f"{strategy.name}: Max loss={max_loss_pct:.2f}%, cost_efficiency={score:.2f}"
        )

        return score

    def _score_greek_profile(self, strategy) -> float:
        """
        Score greek profile based on strategy type.

        Different strategy types prefer different greek profiles:
        - Directional bullish/bearish: High delta magnitude
        - Neutral: Low delta, positive theta
        - Volatility long: High vega
        - Volatility short: Negative vega, positive theta

        Args:
            strategy: Strategy instance

        Returns:
            Score (0-10)
        """
        greeks = strategy.get_net_greeks()
        strategy_type = strategy.strategy_type

        if strategy_type == "directional_bullish":
            # Want high positive delta (0.5-1.0 is ideal)
            delta = greeks.get("delta", 0)
            score = self.normalize_score(
                score=delta,
                min_score=0.0,
                max_score=1.0
            )

            logger.debug(
                f"{strategy.name}: Bullish strategy, delta={delta:.3f}, greek_score={score:.2f}"
            )
            return score

        elif strategy_type == "directional_bearish":
            # Want high negative delta (-0.5 to -1.0 is ideal)
            delta = greeks.get("delta", 0)
            # Flip sign for scoring
            score = self.normalize_score(
                score=-delta,
                min_score=0.0,
                max_score=1.0
            )

            logger.debug(
                f"{strategy.name}: Bearish strategy, delta={delta:.3f}, greek_score={score:.2f}"
            )
            return score

        elif strategy_type == "neutral":
            # Want low delta, positive theta
            delta = abs(greeks.get("delta", 0))
            theta = greeks.get("theta", 0)

            # Combine: low delta + positive theta
            delta_score = 10.0 - (delta * 10.0)  # Lower delta = higher score
            theta_score = self.normalize_score(theta, min_score=-0.1, max_score=0.1)

            score = (delta_score + theta_score) / 2.0

            logger.debug(
                f"{strategy.name}: Neutral strategy, delta={delta:.3f}, theta={theta:.3f}, "
                f"greek_score={score:.2f}"
            )
            return score

        elif strategy_type == "volatility_long":
            # Want high positive vega
            vega = greeks.get("vega", 0)
            score = self.normalize_score(
                score=vega,
                min_score=0.0,
                max_score=1.0
            )

            logger.debug(
                f"{strategy.name}: Vol long strategy, vega={vega:.3f}, greek_score={score:.2f}"
            )
            return score

        elif strategy_type == "volatility_short":
            # Want high negative vega, positive theta
            vega = greeks.get("vega", 0)
            theta = greeks.get("theta", 0)

            vega_score = 10.0 - (abs(vega) * 10.0)  # More negative vega = higher score
            theta_score = self.normalize_score(theta, min_score=-0.1, max_score=0.1)

            score = (vega_score + theta_score) / 2.0

            logger.debug(
                f"{strategy.name}: Vol short strategy, vega={vega:.3f}, theta={theta:.3f}, "
                f"greek_score={score:.2f}"
            )
            return score

        else:
            logger.warning(f"{strategy.name}: Unknown strategy type: {strategy_type}")
            return 5.0  # Neutral score

    def _score_breakeven_distance(self, strategy) -> float:
        """
        Score breakeven distance from current price.

        Closer breakeven = higher probability of profit = higher score.
        For single-breakeven strategies (long call/put).

        Args:
            strategy: Strategy instance

        Returns:
            Score (0-10)
        """
        breakeven_points = strategy.get_breakeven_points()

        if not breakeven_points:
            logger.warning(f"{strategy.name}: No breakeven points")
            return 0.0

        # For single breakeven (long call/put), calculate distance
        if len(breakeven_points) == 1:
            breakeven = breakeven_points[0]
            distance_pct = abs((breakeven - strategy.underlying_price) / strategy.underlying_price) * 100

            # Closer breakeven = higher score
            # 0% distance = 10, 5% distance = 7, 10% distance = 4, 20%+ = 0
            score = self.normalize_score(
                score=20.0 - distance_pct,
                min_score=0.0,
                max_score=20.0
            )

            logger.debug(
                f"{strategy.name}: Breakeven distance={distance_pct:.2f}%, score={score:.2f}"
            )

            return score

        # For multiple breakevens (spreads), calculate range width
        elif len(breakeven_points) == 2:
            lower = min(breakeven_points)
            upper = max(breakeven_points)

            # Check if current price is within range
            if lower <= strategy.underlying_price <= upper:
                # Already in profit zone, high score
                score = 10.0
            else:
                # Calculate distance to nearest breakeven
                distance_to_lower = abs((lower - strategy.underlying_price) / strategy.underlying_price) * 100
                distance_to_upper = abs((upper - strategy.underlying_price) / strategy.underlying_price) * 100

                min_distance = min(distance_to_lower, distance_to_upper)

                score = self.normalize_score(
                    score=20.0 - min_distance,
                    min_score=0.0,
                    max_score=20.0
                )

            logger.debug(
                f"{strategy.name}: Two breakevens, score={score:.2f}"
            )

            return score

        else:
            # More than 2 breakevens (complex strategies)
            # Calculate average distance
            distances = [
                abs((be - strategy.underlying_price) / strategy.underlying_price) * 100
                for be in breakeven_points
            ]
            avg_distance = sum(distances) / len(distances)

            score = self.normalize_score(
                score=20.0 - avg_distance,
                min_score=0.0,
                max_score=20.0
            )

            logger.debug(
                f"{strategy.name}: Multiple breakevens, avg_distance={avg_distance:.2f}%, "
                f"score={score:.2f}"
            )

            return score

    def _score_strike_moneyness(self, strategy) -> float:
        """
        Score strike moneyness (distance from strike to current price).

        For directional strategies:
        - Slightly OTM (3-10% out): Optimal balance of cost and probability (score 10)
        - ATM (0-3% out): Good probability, higher cost (score 8)
        - Moderately OTM (10-20% out): Cheaper but lower probability (score 6)
        - Far OTM (>20%): Very cheap but very low probability (score 3)
        - ITM: Expensive, behaves like stock (score 5)

        Args:
            strategy: Strategy instance

        Returns:
            Score (0-10)
        """
        if not strategy.legs:
            logger.warning(f"{strategy.name}: No legs to score strike moneyness")
            return 5.0

        # For single-leg strategies, use the primary leg's strike
        if len(strategy.legs) == 1:
            leg = strategy.legs[0]
            strike = leg.strike
            current_price = strategy.underlying_price

            # Calculate moneyness percentage
            if leg.option_type.lower() in ["call", "c"]:
                # For calls: OTM when strike > current
                moneyness_pct = ((strike - current_price) / current_price) * 100
            else:
                # For puts: OTM when strike < current
                moneyness_pct = ((current_price - strike) / current_price) * 100

            strategy_type = strategy.strategy_type

            if strategy_type in ["directional_bullish", "directional_bearish"]:
                # Directional strategies benefit from slightly OTM strikes

                if moneyness_pct < -5:
                    # Deep ITM - expensive, low leverage
                    score = 4.0
                elif -5 <= moneyness_pct < 0:
                    # Slightly ITM - good delta but more expensive
                    score = 6.0
                elif 0 <= moneyness_pct < 3:
                    # Near ATM - good balance
                    score = 8.0
                elif 3 <= moneyness_pct < 10:
                    # Slightly OTM - OPTIMAL (good cost/probability balance)
                    score = 10.0
                elif 10 <= moneyness_pct < 20:
                    # Moderately OTM - cheaper but lower probability
                    score = 7.0
                elif 20 <= moneyness_pct < 30:
                    # Far OTM - very cheap but low probability
                    score = 4.0
                else:
                    # Very far OTM - lottery ticket
                    score = 2.0

                logger.debug(
                    f"{strategy.name}: Directional, moneyness={moneyness_pct:+.2f}%, "
                    f"strike_score={score:.2f}"
                )

                return score

            else:
                # For other strategy types, prefer ATM
                distance_from_atm = abs(moneyness_pct)

                # Closer to ATM = higher score
                score = self.normalize_score(
                    score=20.0 - distance_from_atm,
                    min_score=0.0,
                    max_score=20.0
                )

                logger.debug(
                    f"{strategy.name}: Non-directional, ATM distance={distance_from_atm:.2f}%, "
                    f"strike_score={score:.2f}"
                )

                return score

        # For multi-leg strategies, average the moneyness scores
        else:
            leg_scores = []

            for leg in strategy.legs:
                strike = leg.strike
                current_price = strategy.underlying_price

                if leg.option_type.lower() in ["call", "c"]:
                    moneyness_pct = ((strike - current_price) / current_price) * 100
                else:
                    moneyness_pct = ((current_price - strike) / current_price) * 100

                # For spreads, prefer strikes within 5-15% range
                distance_from_atm = abs(moneyness_pct)
                leg_score = self.normalize_score(
                    score=20.0 - distance_from_atm,
                    min_score=0.0,
                    max_score=20.0
                )

                leg_scores.append(leg_score)

            avg_score = sum(leg_scores) / len(leg_scores)

            logger.debug(
                f"{strategy.name}: Multi-leg, average strike_score={avg_score:.2f}"
            )

            return avg_score
