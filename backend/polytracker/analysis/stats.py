"""Statistical helpers.

The key idea: a bet bought at price p is, under the null hypothesis that the
buyer has no edge, a coin that lands "win" with probability p. A wallet's
record is then a sum of unequal coins: a Poisson-binomial distribution. Its
upper tail P(X >= observed wins) tells us how surprising the record is,
accounting for the odds paid. Ten wins on 90-cent favourites is ordinary;
five wins on 12-cent longshots is not.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def poisson_binomial_sf(probs: Sequence[float], k: int) -> float:
    """P(X >= k) where X is a sum of independent Bernoulli(p_i)."""
    n = len(probs)
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    dist = np.zeros(n + 1)
    dist[0] = 1.0
    for i, p in enumerate(probs):
        p = min(max(float(p), 1e-6), 1 - 1e-6)
        # shift-and-add convolution with [1-p, p]
        dist[1 : i + 2] = dist[1 : i + 2] * (1 - p) + dist[0 : i + 1] * p
        dist[0] *= 1 - p
    tail = float(dist[k:].sum())
    return min(max(tail, 1e-300), 1.0)


def surprise(p_value: float) -> float:
    """-log10(p): 2 = 1-in-100, 6 = 1-in-a-million."""
    return -math.log10(max(p_value, 1e-300))


def points_from_p(p_value: float, max_points: float, start: float = 1.0, full: float = 6.0) -> float:
    """Map a p-value to points: nothing above p=0.1, full marks at p<=1e-6."""
    s = surprise(p_value)
    frac = (s - start) / (full - start)
    return round(max_points * min(max(frac, 0.0), 1.0), 1)


def fmt_p(p: float) -> str:
    if p >= 0.01:
        return f"{p:.2f}"
    exp = math.floor(math.log10(max(p, 1e-300)))
    mant = p / 10**exp
    return f"{mant:.1f}e{exp}"


def interval_coverage(intervals: list[tuple[int, int]], span: tuple[int, int]) -> float:
    """Fraction of `span` covered by the union of `intervals` (clipped to span)."""
    lo, hi = span
    if hi <= lo:
        return 0.0
    clipped = sorted((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    total, cur_a, cur_b = 0, None, None
    for a, b in clipped:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total / (hi - lo)
