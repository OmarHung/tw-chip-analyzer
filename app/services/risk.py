from dataclasses import dataclass

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
    breakout_price: float | None = None,
    max_stop_pct: float = 0.06,
) -> RiskPlan:
    """
    Example rule:
    - entry around last price, preferably not >1 ATR above breakout
    - stop under max(swing low buffer, 1.5 ATR), capped by max_stop_pct
    - TP1 = 2R, TP2 = 3R
    """
    entry_low = round(last_price - 0.25 * atr14, 2)
    entry_high = round(last_price + 0.15 * atr14, 2)

    technical_stop = min(last_price - 1.5 * atr14, recent_swing_low - 0.2 * atr14)
    capped_stop = last_price * (1 - max_stop_pct)
    stop = max(technical_stop, capped_stop)
    stop = round(stop, 2)

    risk = max(last_price - stop, last_price * 0.01)
    tp1 = round(last_price + 2.0 * risk, 2)
    tp2 = round(last_price + 3.0 * risk, 2)
    rr = round((tp1 - last_price) / risk, 2)

    return RiskPlan(entry_low, entry_high, stop, tp1, tp2, rr)
