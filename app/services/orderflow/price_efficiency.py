"""價格路徑效率（方向性 Efficiency Ratio）。見 docs/03 §8。

盤中價格單向移動的效率 = 淨位移 / 總路徑長度，帶方向，範圍 -1..1：
- +1：完美單向上行（趨勢強、回撤少 → 籌碼承接有力，偏多）
- -1：完美單向下行
- ~0：來回震盪、無方向

turnover-neutral、以單股自身價格序列計算（跨股票再交由橫斷面 Z-score）。
"""
from __future__ import annotations

from collections.abc import Sequence


def price_efficiency(prices: Sequence[float]) -> float:
    """回傳方向性路徑效率 ∈ -1..1；樣本 <2 或路徑長度為 0 時回 0。"""
    if len(prices) < 2:
        return 0.0
    p = [float(x) for x in prices]
    net = p[-1] - p[0]
    path = sum(abs(p[i] - p[i - 1]) for i in range(1, len(p)))
    if path <= 0:
        return 0.0
    return max(-1.0, min(1.0, net / path))
