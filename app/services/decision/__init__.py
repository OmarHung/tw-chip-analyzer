"""決策引擎：整合 risk / entry / exit（見 docs/04）。"""
from __future__ import annotations

from app.core.config import Thresholds, get_thresholds
from app.models.signal import Action, SignalResult
from app.services.decision.entry import PriceContext, decide_entry, entry_zone_for
from app.services.decision.exit import ExitContext, decide_exit
from app.services.decision.risk import RiskPlan, build_risk_plan

__all__ = [
    "PriceContext",
    "ExitContext",
    "RiskPlan",
    "build_risk_plan",
    "decide_entry",
    "decide_exit",
    "decide",
]


def decide(
    score: float,
    reasons: list[str],
    last_price: float,
    atr14: float,
    recent_swing_low: float,
    price_ctx: PriceContext | None = None,
    already_in_position: bool = False,
    exit_ctx: ExitContext | None = None,
    thresholds: Thresholds | None = None,
) -> SignalResult:
    """產生完整訊號建議。

    - 未持倉：走 Entry Filter（BUY/WATCH/HOLD/AVOID）。
    - 持倉中：走 Exit Logic（HOLD/REDUCE/EXIT）。
    """
    t = thresholds or get_thresholds()
    plan = build_risk_plan(last_price, atr14, recent_swing_low, t)

    if already_in_position:
        ectx = exit_ctx or ExitContext(price=last_price, stop_loss=plan.stop_loss)
        action, gate = decide_exit(score, ectx, t)
        all_reasons = reasons + gate
    else:
        ctx = price_ctx or PriceContext()
        decision = decide_entry(score, plan.rr, ctx, t)
        action = decision.action
        all_reasons = reasons + decision.gate_reasons

    has_plan = action in (Action.BUY, Action.WATCH, Action.HOLD, Action.REDUCE)
    return SignalResult(
        score=score,
        action=action,
        reasons=all_reasons,
        entry_zone=entry_zone_for(action, plan),
        stop_loss=plan.stop_loss if has_plan else None,
        take_profit_1=plan.tp1 if has_plan else None,
        take_profit_2=plan.tp2 if has_plan else None,
        risk_reward=plan.rr if has_plan else None,
    )
