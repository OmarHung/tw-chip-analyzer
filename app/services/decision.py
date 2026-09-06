from app.models.signal import Action, SignalResult
from app.services.risk import build_risk_plan

def decide(
    score: float,
    reasons: list[str],
    last_price: float,
    atr14: float,
    recent_swing_low: float,
    already_in_position: bool = False,
    unrealized_r: float = 0.0,
):
    risk = build_risk_plan(last_price, atr14, recent_swing_low)

    if already_in_position:
        if score < 40:
            action = Action.EXIT
        elif score < 50:
            action = Action.REDUCE
        else:
            action = Action.HOLD
    else:
        if score >= 75:
            action = Action.BUY
        elif score >= 65:
            action = Action.WATCH
        elif score < 40:
            action = Action.AVOID
        else:
            action = Action.HOLD

    # Avoid late chasing: entry zone must not exceed planned upper bound.
    return SignalResult(
        score=score,
        action=action,
        reasons=reasons,
        entry_zone=(risk.entry_low, risk.entry_high) if action in (Action.BUY, Action.WATCH) else None,
        stop_loss=risk.stop_loss if action in (Action.BUY, Action.WATCH, Action.HOLD) else None,
        take_profit_1=risk.tp1 if action in (Action.BUY, Action.WATCH, Action.HOLD) else None,
        take_profit_2=risk.tp2 if action in (Action.BUY, Action.WATCH, Action.HOLD) else None,
        risk_reward=risk.rr if action in (Action.BUY, Action.WATCH, Action.HOLD) else None,
    )
