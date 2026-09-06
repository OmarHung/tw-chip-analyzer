"""Cumulative Volume Delta。見 docs/03 §8。

Delta = BuyVolume - SellVolume；CVD(t) = CVD(t-1) + Delta(t)。
另提供 slope 與 divergence 判定。
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def delta(buy_volume: float, sell_volume: float) -> float:
    return float(buy_volume - sell_volume)


def cumulative(deltas: Sequence[float]) -> list[float]:
    out: list[float] = []
    acc = 0.0
    for d in deltas:
        acc += d
        out.append(acc)
    return out


def slope(cvd_series: Sequence[float]) -> float:
    """對 CVD 序列做線性回歸斜率（每根 bar 的平均增量）。"""
    y = np.asarray(cvd_series, dtype=float)
    if y.size < 2:
        return 0.0
    x = np.arange(y.size, dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def price_cvd_divergence(
    price_series: Sequence[float], cvd_series: Sequence[float]
) -> int:
    """回傳背離方向：
    +1 = 價跌但 CVD 升（潛在多方背離）
    -1 = 價升但 CVD 降（潛在空方背離 / 出貨）
     0 = 無明顯背離
    """
    p = np.asarray(price_series, dtype=float)
    c = np.asarray(cvd_series, dtype=float)
    if p.size < 2 or c.size < 2:
        return 0
    price_up = p[-1] > p[0]
    cvd_up = c[-1] > c[0]
    if price_up and not cvd_up:
        return -1
    if not price_up and cvd_up:
        return 1
    return 0
