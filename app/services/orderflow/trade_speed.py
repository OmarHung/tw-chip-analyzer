"""成交速度（Trade Speed）。見 docs/03 §8。全部以 rolling Z-score 正規化。"""
from __future__ import annotations

from collections.abc import Sequence

from app.services.normalize import rolling_zscore


def rates(trade_count: int, volume: float, value: float, window_sec: float) -> dict:
    """回傳 trades/sec、volume/sec、value/sec。"""
    w = max(window_sec, 1e-9)
    return {
        "trades_per_sec": trade_count / w,
        "volume_per_sec": volume / w,
        "value_per_sec": value / w,
    }


def speed_zscore(history_rate: Sequence[float], current_rate: float) -> float:
    return rolling_zscore(history_rate, current_rate)
