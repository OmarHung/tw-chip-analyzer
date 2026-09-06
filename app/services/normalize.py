"""特徵正規化工具（見 docs/03 §9）。

不同股票不可直接比較張數，一律先轉成 Z-score / percentile / ratio。
"""
from __future__ import annotations

from collections.abc import Sequence
from math import tanh

import numpy as np


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def squash_z(z: float) -> float:
    """把約 z=-3..3 平滑映射到 -1..1。"""
    return tanh(z / 2.0)


def zscore(value: float, mean: float, std: float, eps: float = 1e-9) -> float:
    return (value - mean) / (std + eps)


def rolling_zscore(history: Sequence[float], value: float) -> float:
    """以歷史樣本的平均/標準差計算 value 的 Z-score。樣本不足回 0。"""
    arr = np.asarray(history, dtype=float)
    if arr.size < 2:
        return 0.0
    mean = float(arr.mean())
    std = float(arr.std(ddof=0))
    return zscore(value, mean, std)


def percentile_rank(history: Sequence[float], value: float) -> float:
    """value 在歷史分布中的百分位（0..1）。樣本不足回 0.5。"""
    arr = np.asarray(history, dtype=float)
    if arr.size == 0:
        return 0.5
    return float((arr <= value).mean())


def ratio(numerator: float, denominator: float, eps: float = 1e-9) -> float:
    return numerator / (denominator + eps)
