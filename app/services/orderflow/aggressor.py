"""主動方判定（Aggressor Side）。見 docs/03 §8。

回傳 1=BUY、-1=SELL、0=UNKNOWN。UNKNOWN 禁止強制歸類。
"""
from __future__ import annotations

BUY = 1
SELL = -1
UNKNOWN = 0


def classify_side(
    price: float,
    bid1: float | None,
    ask1: float | None,
    prev_price: float | None = None,
) -> int:
    if ask1 is not None and price >= ask1:
        return BUY
    if bid1 is not None and price <= bid1:
        return SELL
    # fallback tick rule
    if prev_price is not None:
        if price > prev_price:
            return BUY
        if price < prev_price:
            return SELL
    return UNKNOWN
