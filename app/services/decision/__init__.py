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
    resistance: float | None = None,
) -> SignalResult:
    """產生完整訊號建議。

    - 未持倉：走 Entry Filter（BUY/WATCH/HOLD/AVOID）。
    - 持倉中：走 Exit Logic（HOLD/REDUCE/EXIT）；停損/TP 以持倉成本與停損為準，
      不給進場區與 RR（持倉管理不適用進場 gate）。
    """
    t = thresholds or get_thresholds()
    plan = build_risk_plan(last_price, atr14, recent_swing_low, t, resistance)

    if already_in_position:
        ectx = exit_ctx or ExitContext(price=last_price, stop_loss=plan.stop_loss)
        action, gate = decide_exit(score, ectx, t)
        has_plan = action in (Action.HOLD, Action.REDUCE)
        tp1, tp2 = plan.tp1, plan.tp2
        if ectx.entry_price is not None:
            risk = max(ectx.entry_price - ectx.stop_loss, 0.0)
            tp1 = round(ectx.entry_price + t.risk["tp1_r"] * risk, 2)
            tp2 = round(ectx.entry_price + t.risk["tp2_r"] * risk, 2)
        # 現價已越過的 TP 不再是「目標」：欄位留空、改以原因說明，避免顯示低於現價的停利價
        reached: list[str] = []
        if has_plan:
            if last_price >= tp1:
                reached.append(f"現價已達 TP1（{tp1:g}）")
                tp1 = None
            if last_price >= tp2:
                reached.append(f"現價已達 TP2（{tp2:g}）")
                tp2 = None
        return SignalResult(
            score=score,
            action=action,
            reasons=reasons + gate + reached,
            entry_zone=None,
            stop_loss=ectx.stop_loss if has_plan else None,
            take_profit_1=tp1 if has_plan else None,
            take_profit_2=tp2 if has_plan else None,
            risk_reward=None,
        )

    ctx = price_ctx or PriceContext()
    decision = decide_entry(score, plan.rr, ctx, t)
    action = decision.action
    has_plan = action in (Action.BUY, Action.WATCH, Action.HOLD)
    return SignalResult(
        score=score,
        action=action,
        reasons=reasons + decision.gate_reasons,
        entry_zone=entry_zone_for(action, plan),
        stop_loss=plan.stop_loss if has_plan else None,
        take_profit_1=plan.tp1 if has_plan else None,
        take_profit_2=plan.tp2 if has_plan else None,
        risk_reward=plan.rr if has_plan else None,
    )
