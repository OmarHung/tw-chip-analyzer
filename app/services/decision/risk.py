"""Risk Engine。見 docs/04 §13。所有倍數/上限一律 config 化。

- 停損 = max( min(entry - k*ATR, swing_low - buf*ATR), 最大停損%地板 )
- TP1 = tp1_r * R，TP2 = tp2_r * R，R = entry - stop（風控目標）
- RR（Entry Filter 用）= (目標 − 進場區上緣) ÷ (進場區上緣 − 停損)。目標取前高壓力位，
  已突破則用 ATR 倍數；**不可用 TP1 反算**，否則 RR 恆等於 tp1_r（docs/09 BUG-01）。
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
    target: float  # RR 所用的可達目標價（壓力位或突破後 ATR 目標）
    # RR 目標依據：resistance（前高壓力位）/ breakout_atr（已突破，ATR 推估，未經 OOS 驗證）
    # / unavailable（無壓力位資料，RR 以 0 計）
    rr_basis: str = "unavailable"


def stop_for(
    price: float, atr14: float, recent_swing_low: float, thresholds: Thresholds
) -> float:
    r = thresholds.risk
    technical_stop = min(
        price - r["atr_stop_multiplier"] * atr14,
        recent_swing_low - r["swing_buffer_atr"] * atr14,
    )
    # 最大停損百分比地板：停損不得低於 price*(1-max_stop_pct)
    capped_stop = price * (1 - r["max_stop_pct"])
    return round(max(technical_stop, capped_stop), 2)


def build_risk_plan(
    last_price: float,
    atr14: float,
    recent_swing_low: float,
    thresholds: Thresholds | None = None,
    resistance: float | None = None,
) -> RiskPlan:
    t = thresholds or get_thresholds()
    r = t.risk

    entry_low = round(last_price - r["entry_low_atr"] * atr14, 2)
    entry_high = round(last_price + r["entry_high_atr"] * atr14, 2)
    stop = stop_for(last_price, atr14, recent_swing_low, t)

    risk = max(last_price - stop, last_price * r["min_risk_pct"])  # 避免除以 0
    tp1 = round(last_price + r["tp1_r"] * risk, 2)
    tp2 = round(last_price + r["tp2_r"] * risk, 2)

    # RR：以最差成交（進場區上緣）計成本；無壓力位資料 → 保守給 0（不放行 BUY）
    if resistance is None:
        target, basis = entry_high, "unavailable"
    elif resistance > entry_high:
        target, basis = resistance, "resistance"
    else:
        target, basis = entry_high + r["breakout_target_atr"] * atr14, "breakout_atr"
    entry_risk = max(entry_high - stop, entry_high * r["min_risk_pct"])
    rr = round(max(target - entry_high, 0.0) / entry_risk, 2)

    return RiskPlan(entry_low, entry_high, stop, tp1, tp2, rr, round(target, 2), basis)
