"""大單判定。見 docs/03 §8。

禁止固定 100 張門檻；用 rolling quantile（樣本不足則不判定大單）。
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def large_threshold(
    recent_volumes: Sequence[float], percentile: float = 0.95, min_samples: int = 500
) -> float | None:
    """回傳大單量門檻；樣本數不足 min_samples 時回 None（不判定）。"""
    arr = np.asarray(recent_volumes, dtype=float)
    if arr.size < min_samples:
        return None
    return float(np.quantile(arr, percentile))


def is_large(volume: float, threshold: float | None) -> bool:
    return threshold is not None and volume >= threshold


def large_trade_delta(
    trades: Sequence[tuple[int, float]],
    threshold: float | None,
) -> float:
    """trades: [(side, volume), ...]，side ∈ {1,-1,0}。
    回傳大單淨量 = 大買量 - 大賣量。
    """
    if threshold is None:
        return 0.0
    buy = sum(v for s, v in trades if s > 0 and v >= threshold)
    sell = sum(v for s, v in trades if s < 0 and v >= threshold)
    return float(buy - sell)
