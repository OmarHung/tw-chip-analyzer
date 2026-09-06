"""Risk Engine。見 docs/04 §13。所有倍數/上限一律 config 化。

- 停損 = max( min(entry - k*ATR, swing_low - buf*ATR), 最大停損%地板 )
- TP1 = tp1_r * R，TP2 = tp2_r * R，R = entry - stop
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Thresholds, get_thresholds


@dataclass
class RiskPlan:
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    rr: float


def build_risk_plan(
    last_price: float,
    atr14: float,
    recent_swing_low: float,
    thresholds: Thresholds | None = None,
) -> RiskPlan:
    t = thresholds or get_thresholds()
    r = t.risk

    entry_low = round(last_price - r["entry_low_atr"] * atr14, 2)
    entry_high = round(last_price + r["entry_high_atr"] * atr14, 2)

    technical_stop = min(
        last_price - r["atr_stop_multiplier"] * atr14,
        recent_swing_low - r["swing_buffer_atr"] * atr14,
    )
    # 最大停損百分比地板：停損不得低於 last*(1-max_stop_pct)
    capped_stop = last_price * (1 - r["max_stop_pct"])
    stop = round(max(technical_stop, capped_stop), 2)

    risk = max(last_price - stop, last_price * 0.01)  # 避免除以 0
    tp1 = round(last_price + r["tp1_r"] * risk, 2)
    tp2 = round(last_price + r["tp2_r"] * risk, 2)
    rr = round((tp1 - last_price) / risk, 2)

    return RiskPlan(entry_low, entry_high, stop, tp1, tp2, rr)
