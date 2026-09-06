"""Order Book Imbalance。見 docs/03 §8。只能當輔助訊號（掛單可撤）。"""
from __future__ import annotations

from collections.abc import Sequence


def order_book_imbalance(
    bid_volumes: Sequence[float], ask_volumes: Sequence[float]
) -> float:
    """OBI = (ΣBid - ΣAsk) / (ΣBid + ΣAsk)，範圍 -1..1。"""
    b = float(sum(bid_volumes))
    a = float(sum(ask_volumes))
    total = b + a
    if total == 0:
        return 0.0
    return (b - a) / total
