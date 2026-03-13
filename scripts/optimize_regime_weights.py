"""
Regime weight optimizer CLI.

Usage:
    python -m scripts.optimize_regime_weights --currency BTC
    python -m scripts.optimize_regime_weights --currency ETH
    python -m scripts.optimize_regime_weights --currency BTC --directional-threshold 2.0
"""
import argparse
import logging
import sys

# Force UTF-8 output on Windows so Unicode symbols print correctly
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.core.database.regime_dataset_builder import RegimeDatasetBuilder
from coding.core.analytics.regime_weight_optimizer import RegimeWeightOptimizer, ParameterSet

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def _fmt_param_set(ps: ParameterSet, label: str, current: ParameterSet = None) -> str:
    w = ps.weights
    lines = [f"{label}:"]
    lines.append(
        f"  trend={w['trend']:.2f}  vol={w['volatility']:.2f}  "
        f"momentum={w['momentum']:.2f}  onchain={w['onchain']:.2f}  "
        f"sentiment={w['sentiment']:.2f}"
    )
    lines.append(
        f"  sideways=\u00b1{ps.sideways_threshold:.0f}  "
        f"strong=\u00b1{ps.strong_threshold:.0f}"
    )

    acc_str = f"{ps.fitness_a * 100:.1f}%"
    shr_str = f"{ps.fitness_b:.2f}"

    if current is not None and label == "ACCURACY-OPTIMAL":
        delta_a = (ps.fitness_a - current.fitness_a) * 100
        acc_str += f" ({delta_a:+.1f} pp)"
    elif current is not None and label == "SHARPE-OPTIMAL":
        delta_b = ps.fitness_b - current.fitness_b
        shr_str += f" ({delta_b:+.2f})"

    lines.append(f"  Accuracy: {acc_str}   Sharpe: {shr_str}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Optimize regime detection weights")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--directional-threshold", type=float, default=1.5,
                        dest="directional_threshold")
    args = parser.parse_args()

    repo = DatabaseRepository()
    builder = RegimeDatasetBuilder(repo)

    logger.info(f"Building dataset for {args.currency}...")
    df = builder.build(args.currency)

    if df.empty:
        print("No data available. Exiting.")
        return

    date_start = df["detected_at"].min().strftime("%Y-%m-%d")
    date_end = df["detected_at"].max().strftime("%Y-%m-%d")
    n = len(df)

    print()
    print("=== REGIME WEIGHT OPTIMIZER ===")
    print(f"Currency: {args.currency}")
    print(f"Dataset: {n} detections ({date_start} \u2192 {date_end})")
    print()
    print("Horizon coverage:")
    print(builder.summary(df))
    print()

    logger.info("Running optimizer...")
    opt = RegimeWeightOptimizer(df, directional_threshold=args.directional_threshold)
    result = opt.optimize()

    sep = "\u2500" * 60
    print(sep)
    print(_fmt_param_set(result.current, "CURRENT (hand-tuned)"))
    print()
    print(_fmt_param_set(result.accuracy_optimal, "ACCURACY-OPTIMAL", result.current))
    print()
    print(_fmt_param_set(result.sharpe_optimal, "SHARPE-OPTIMAL", result.current))
    print()
    blended_lines = _fmt_param_set(result.blended_optimal, "BLENDED (50/50)")
    print(blended_lines)
    print("  (no delta \u2014 blended has no single natural baseline)")
    print(sep)
    print("To apply: update WEIGHTS and REGIME_THRESHOLDS in market_regime_detector.py")
    print()


if __name__ == "__main__":
    main()
