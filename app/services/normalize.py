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


def cross_sectional_percentile(values: Sequence[float]) -> list[float]:
    """整組值 → 各自的橫斷面百分位(0..100),同值取平均 rank。

    用於 chip_score 散度修復(scoring.mapping=percentile):z-score 加權合成
    必然回歸中性(50)、實測天花板 ~64,高分 bucket 永遠無樣本可回測。
    改以「當日全市場排名」映射:分布均勻、rank-preserving(不改變 Spearman
    IC、不製造假 alpha),讓 §28 成功標準變得可測量。
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        return []
    if n == 1:
        return [50.0]
    order = np.argsort(arr, kind="stable")
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and arr[order[j + 1]] == arr[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based 平均 rank
        ranks[order[i : j + 1]] = avg
        i = j + 1
    # (rank-0.5)/n 映射,避免端點恰為 0/100
    return [round(float((r - 0.5) / n * 100), 1) for r in ranks]
