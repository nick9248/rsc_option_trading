"""
Small, dependency-light statistics for testing whether a daily signal
relates to later price behaviour: Spearman rank correlation, difference of
group means, and a moving-block bootstrap confidence interval (daily rows
are autocorrelated and multi-day outcome windows overlap, so resampling
single days would understate the uncertainty).

Pure: no database. Missing values (None or NaN) are dropped pairwise.
"""

import math
import random
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TypeGuard


def _is_present(value: Optional[float]) -> TypeGuard[float]:
    """True when the value is a number (not None and not NaN)."""
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def average_ranks(values: Sequence[float]) -> List[float]:
    """1-based ranks; tied values get the mean of the ranks they span."""
    order = sorted(range(len(values)), key=lambda position: values[position])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        mean_rank = (start + end) / 2.0 + 1.0
        for position in order[start:end + 1]:
            ranks[position] = mean_rank
        start = end + 1
    return ranks


def pearson_correlation(first: Sequence[float], second: Sequence[float]) -> Optional[float]:
    """Pearson correlation; None for fewer than 3 points or a constant series."""
    if len(first) != len(second):
        raise ValueError("series must have the same length")
    count = len(first)
    if count < 3:
        return None
    mean_first = sum(first) / count
    mean_second = sum(second) / count
    covariance = sum((a - mean_first) * (b - mean_second) for a, b in zip(first, second))
    variance_first = sum((a - mean_first) ** 2 for a in first)
    variance_second = sum((b - mean_second) ** 2 for b in second)
    if variance_first == 0 or variance_second == 0:
        return None
    return covariance / math.sqrt(variance_first * variance_second)


def spearman_rank_correlation(
    first: Sequence[Optional[float]], second: Sequence[Optional[float]]
) -> Optional[float]:
    """Spearman correlation over the pairs where both values are present."""
    pairs = [(a, b) for a, b in zip(first, second) if _is_present(a) and _is_present(b)]
    if len(pairs) < 3:
        return None
    return pearson_correlation(average_ranks([a for a, _ in pairs]),
                               average_ranks([b for _, b in pairs]))


def group_mean_difference(
    values: Sequence[Optional[float]], in_group: Sequence[Optional[bool]]
) -> Optional[float]:
    """
    mean(values where in_group is True) - mean(values where it is False);
    rows with a missing value or group are ignored. None when a group is empty.
    """
    group_true = [value for value, member in zip(values, in_group) if _is_present(value) and member is True]
    group_false = [value for value, member in zip(values, in_group) if _is_present(value) and member is False]
    if not group_true or not group_false:
        return None
    return sum(group_true) / len(group_true) - sum(group_false) / len(group_false)


def moving_block_bootstrap_indices(
    sample_size: int, block_length: int, generator: random.Random
) -> List[int]:
    """
    One moving-block bootstrap resample of positions 0..sample_size-1:
    random contiguous blocks of ``block_length`` (wrapping around the end),
    concatenated and cut to ``sample_size``.
    """
    if sample_size <= 0 or block_length <= 0:
        raise ValueError("sample_size and block_length must be positive")
    indices: List[int] = []
    while len(indices) < sample_size:
        start = generator.randrange(sample_size)
        indices.extend((start + offset) % sample_size for offset in range(block_length))
    return indices[:sample_size]


def moving_block_bootstrap_interval(
    sample_size: int,
    statistic: Callable[[Sequence[int]], Optional[float]],
    block_length: int,
    resample_count: int,
    seed: int,
    confidence: float = 0.95,
) -> Dict[str, Optional[float]]:
    """
    Percentile confidence interval of ``statistic`` (a function of row
    positions) under the moving-block bootstrap.

    Returns:
        {"low", "high", "share_at_or_below_zero", "valid_resamples"};
        ``share_at_or_below_zero`` is the fraction of resampled statistics
        <= 0 (a two-sided bootstrap p-value is 2 * min(share, 1 - share)).
        low/high are None when fewer than 100 resamples give a value.
    """
    generator = random.Random(seed)
    estimates = []
    for _ in range(resample_count):
        value = statistic(moving_block_bootstrap_indices(sample_size, block_length, generator))
        if _is_present(value):
            estimates.append(value)
    if len(estimates) < 100:
        return {"low": None, "high": None, "share_at_or_below_zero": None,
                "valid_resamples": len(estimates)}
    estimates.sort()
    tail = (1.0 - confidence) / 2.0
    low_position = int(math.floor(tail * (len(estimates) - 1)))
    high_position = int(math.ceil((1.0 - tail) * (len(estimates) - 1)))
    return {
        "low": estimates[low_position],
        "high": estimates[high_position],
        "share_at_or_below_zero": sum(1 for value in estimates if value <= 0) / len(estimates),
        "valid_resamples": len(estimates),
    }


def two_sided_bootstrap_p_value(share_at_or_below_zero: Optional[float]) -> Optional[float]:
    """2 * min(share, 1 - share), capped at 1; None when the share is None."""
    if share_at_or_below_zero is None:
        return None
    return min(1.0, 2.0 * min(share_at_or_below_zero, 1.0 - share_at_or_below_zero))


def select(values: Sequence, positions: Sequence[int]) -> List:
    """values at the given positions (a bootstrap resample)."""
    return [values[position] for position in positions]


def paired_present(
    first: Sequence[Optional[float]], second: Sequence[Optional[float]]
) -> Tuple[List[float], List[float]]:
    """The two series restricted to rows where both are present."""
    kept = [(a, b) for a, b in zip(first, second) if _is_present(a) and _is_present(b)]
    return [a for a, _ in kept], [b for _, b in kept]
