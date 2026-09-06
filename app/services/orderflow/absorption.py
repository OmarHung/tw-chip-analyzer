"""吸收（Absorption）。見 docs/03 §8。

Sell Absorption：大量主動 SELL 但價格跌不下去。
Buy  Absorption：大量主動 BUY  但價格漲不上去。

第一版：executed_volume_z / (abs(price_return_z) + epsilon)，並依方向拆開。
"""
from __future__ import annotations

EPS = 0.5


def absorption_score(executed_volume_z: float, price_return_z: float, eps: float = EPS) -> float:
    """量能相對強、但價格反應相對弱 → 分數高。"""
    return executed_volume_z / (abs(price_return_z) + eps)


def directional_absorption(
    executed_volume_z: float, price_return_z: float, dominant_side: int, eps: float = EPS
) -> tuple[float, float]:
    """回傳 (buy_absorption, sell_absorption)。

    dominant_side：該區間主動方向（1=買方主導、-1=賣方主導、0=不明）。
    - 賣方主導但價格沒跌 → sell_absorption（低檔承接，偏多）
    - 買方主導但價格沒漲 → buy_absorption（高檔賣壓，偏空）
    """
    score = absorption_score(executed_volume_z, price_return_z, eps)
    buy_abs = 0.0
    sell_abs = 0.0
    if dominant_side < 0 and price_return_z >= 0:
        sell_abs = score
    elif dominant_side > 0 and price_return_z <= 0:
        buy_abs = score
    return buy_abs, sell_abs
