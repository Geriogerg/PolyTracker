"""Price-jump detection: a stand-in for "the moment the news broke".

We look for moves of at least `threshold` (in probability points) within
`window_s` on the price of outcome 0, keep only moves whose new level holds
afterwards (to ignore a single stray print), and date each jump from the
calm level just before the climb.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Jump:
    condition_id: str
    start_ts: int
    end_ts: int
    p_before: float
    p_after: float

    @property
    def direction(self) -> int:
        return 1 if self.p_after > self.p_before else -1

    @property
    def magnitude(self) -> float:
        return abs(self.p_after - self.p_before)

    def as_row(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "start_ts": int(self.start_ts),
            "end_ts": int(self.end_ts),
            "p_before": round(float(self.p_before), 4),
            "p_after": round(float(self.p_after), 4),
            "direction": self.direction,
        }


def smooth_trade_prices(ts: np.ndarray, p: np.ndarray, k: int = 3) -> np.ndarray:
    """Rolling median over k prints: trade-derived series are noisy."""
    if len(p) < k:
        return p
    out = p.copy()
    half = k // 2
    for i in range(len(p)):
        lo, hi = max(0, i - half), min(len(p), i + half + 1)
        out[i] = np.median(p[lo:hi])
    return out


def detect_jumps(
    condition_id: str,
    ts: np.ndarray,
    p: np.ndarray,
    threshold: float = 0.2,
    window_s: int = 7200,
    hold_s: int = 1800,
) -> list[Jump]:
    n = len(ts)
    if n < 3:
        return []
    order = np.argsort(ts, kind="stable")
    ts, p = ts[order], p[order]
    jumps: list[Jump] = []
    tol = max(0.015, 0.1 * threshold)  # "still at the pre-move level"
    i = 0
    while i < n - 1:
        # window [i, j_end)
        j_end = int(np.searchsorted(ts, ts[i] + window_s, side="right"))
        if j_end <= i + 1:
            i += 1
            continue
        seg = p[i:j_end]
        j_max = i + int(np.argmax(seg))
        j_min = i + int(np.argmin(seg))
        up = p[j_max] - p[i] >= threshold
        down = p[i] - p[j_min] >= threshold
        if not (up or down):
            i += 1
            continue
        # pick the move whose extreme comes first if both qualify
        if up and (not down or j_max <= j_min):
            end = j_max
            base = float(np.min(p[i : end + 1]))
            calm = np.nonzero(p[i : end + 1] - base <= tol)[0]
        else:
            end = j_min
            base = float(np.max(p[i : end + 1]))
            calm = np.nonzero(base - p[i : end + 1] <= tol)[0]
        # date the jump from the LAST calm print before the climb, not the first:
        # on a flat pre-move series the first one can be hours too early
        start = i + int(calm[-1]) if len(calm) else i
        # extend the end while the move keeps going the same way
        d = 1 if p[end] > p[start] else -1
        while True:
            nxt = int(np.searchsorted(ts, ts[end] + window_s, side="right"))
            if nxt <= end + 1:
                break
            seg = p[end:nxt]
            k = end + int(np.argmax(seg) if d > 0 else np.argmin(seg))
            if (p[k] - p[end]) * d >= 0.02:
                end = k
            else:
                break
        p0, p1 = float(p[start]), float(p[end])
        if abs(p1 - p0) < threshold:
            i += 1
            continue
        # persistence: the new level must hold during the next `hold_s`
        after = p[(ts > ts[end]) & (ts <= ts[end] + hold_s)]
        if len(after):
            level = float(np.median(after))
            held = (level - p0) * np.sign(p1 - p0) >= 0.75 * threshold
            if not held:
                i = end + 1
                continue
        jumps.append(Jump(condition_id, int(ts[start]), int(ts[end]), p0, p1))
        i = end + 1
    return jumps
